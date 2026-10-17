"""
Adaptive Fraud Intelligence (AFI) module.

Implements the prototype's adaptive-monitoring requirements:

  * Distribution drift detection on monitored features (Population Stability
    Index on transaction amounts and risk-score distributions).
  * Analyst case-decision tracking as a limited, selection-biased outcome
    signal (not as model precision or recall).
  * Anomaly-rate and average-risk trends over time.
  * Anchored drift baselines and volume thresholds that recommend capturing
    a new reference distribution; they do not refit the runtime detector.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from database.database import (
    get_connection,
    get_daily_transaction_stats,
    get_recent_transaction_samples,
    get_analyst_decision_counts,
    get_resolved_analyst_outcome_trend,
    get_model_drift_baseline,
    model_drift_logged_today,
    record_model_drift_baseline,
    record_model_monitoring_snapshot,
    create_audit_log,
    get_latest_model_version,
    get_recent_transactions_feed,
)
from services import events
from services.fraud_detector import fraud_detector

DRIFT_WARN = 0.10
DRIFT_CRIT = 0.25


def _get_hybrid_seed_sensitivity():
    report_path = (
        Path(__file__).resolve().parents[1]
        / "reports"
        / "hybrid-policy-seed-stability.json"
    )
    if not report_path.is_file():
        return None

    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["descriptive_seed_summary"]
    return {
        "seeds": report["seeds"],
        "evaluation_scikit_learn_version": report.get(
            "evaluation_scikit_learn_version"
        ),
        "policy_boundaries": report["fixed_runtime_policy_boundaries"],
        "medium_or_high": summary["hybrid_medium_or_higher_cutoff"]["overall"],
        "high": summary["hybrid_high_decision_cutoff"]["overall"],
        "warning": report["seed_stability_warning"],
    }


def _drift_status(psi):
    if psi is None:
        return "insufficient_data"
    if psi >= DRIFT_CRIT:
        return "critical"
    if psi >= DRIFT_WARN:
        return "warning"
    return "stable"


def _get_runtime_model_evaluation():
    evaluation = fraud_detector.config.get("evaluation")
    if not isinstance(evaluation, dict):
        return None

    holdout = evaluation.get("threshold_selection", {}).get(
        "selected_holdout_metrics", {}
    )
    test_split = evaluation.get("split_counts", {}).get("test", {})
    hybrid_holdout = evaluation.get("hybrid_policy_holdout", {})
    return {
        "dataset": evaluation.get("dataset"),
        "dataset_sha256": evaluation.get("dataset_sha256"),
        "scikit_learn_version": evaluation.get("scikit_learn_version"),
        "sampled_rows": evaluation.get("sampled_eligible_rows"),
        "seed": evaluation.get("seed"),
        "test_rows": test_split.get("rows"),
        "test_fraud_labels": test_split.get("fraud_labels"),
        "fraud_prevalence": evaluation.get("test_fraud_prevalence"),
        "threshold": holdout.get("threshold"),
        "precision": holdout.get("precision"),
        "recall": holdout.get("recall"),
        "false_positive_rate": holdout.get("false_positive_rate"),
        "hybrid_medium_or_high": hybrid_holdout.get("medium_or_high"),
        "hybrid_high": hybrid_holdout.get("high"),
        "hybrid_policy_validation": evaluation.get("hybrid_policy_validation"),
        "hybrid_policy_time_windows": evaluation.get(
            "hybrid_policy_time_windows"
        ),
        "hybrid_seed_sensitivity": _get_hybrid_seed_sensitivity(),
        "hybrid_threshold_sensitivity": evaluation.get(
            "hybrid_threshold_sensitivity"
        ),
        "active_medium_boundary": fraud_detector.low_threshold,
        "active_high_boundary": fraud_detector.medium_threshold,
    }


def _psi(expected, actual, buckets=5):
    """Population Stability Index between two distributions."""
    if not expected or not actual:
        return None

    buckets = max(1, min(buckets, min(len(expected), len(actual))))

    low = min(min(expected), min(actual))
    high = max(max(expected), max(actual))

    if high - low < 1e-9:
        return 0.0

    if buckets == 1:
        # Too few points for binning — use relative mean difference.
        mean_e = sum(expected) / len(expected)
        mean_a = sum(actual) / len(actual)
        if abs(mean_e) < 1e-9:
            return 0.0
        return round(abs(mean_a - mean_e) / abs(mean_e), 6)

    width = (high - low) / buckets

    def proportions(values):
        counts = [0] * buckets
        for value in values:
            index = min(int((value - low) / width), buckets - 1)
            counts[index] += 1
        return [count / len(values) for count in counts]

    expected_props = proportions(expected)
    actual_props = proportions(actual)
    psi = 0.0
    for expected_prop, actual_prop in zip(expected_props, actual_props):
        if expected_prop == 0 or actual_prop == 0:
            expected_prop = max(expected_prop, 1e-6)
            actual_prop = max(actual_prop, 1e-6)
        psi += (actual_prop - expected_prop) * math.log(
            actual_prop / expected_prop
        )

    return round(psi, 4)


def compute_flag_rates():
    """Proportion of transactions carrying each security signal."""
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(device_change), 0) AS device_change,
                COALESCE(SUM(ip_network_mismatch), 0) AS ip_mismatch,
                COALESCE(SUM(recent_sim_swap), 0) AS sim_swap,
                COALESCE(SUM(velocity_spike), 0) AS velocity,
                COALESCE(SUM(frequency_spike), 0) AS frequency
            FROM transactions
            """
        ).fetchone()

        total = int(row["total"]) or 1
        return {
            "device_change_rate": round(int(row["device_change"]) / total, 4),
            "ip_mismatch_rate": round(int(row["ip_mismatch"]) / total, 4),
            "sim_swap_rate": round(int(row["sim_swap"]) / total, 4),
            "velocity_rate": round(int(row["velocity"]) / total, 4),
            "frequency_spike_rate": round(int(row["frequency"]) / total, 4),
            "tracked": int(row["total"]),
        }

    finally:
        connection.close()


def build_monitoring():
    """Compute live AFI metrics, persist a snapshot, raise drift alerts."""
    daily = get_daily_transaction_stats(days=14)

    # ------------------------------------------------
    # Anomaly-rate & average-risk series
    # ------------------------------------------------
    anomaly_series = []
    risk_series = []

    for row in daily:
        total = max(1, row["total"])
        high = row["high"] or 0
        medium = row["medium"] or 0

        anomaly_series.append({
            "day": row["day"],
            "total": row["total"],
            "rate": round((high + medium) / total, 4),
            "high": round(high / total, 4),
            "medium": round(medium / total, 4),
        })
        risk_series.append({
            "day": row["day"],
            "value": round(row["avg_risk"], 4),
        })

    # ------------------------------------------------
    # Use the captured reference when available; otherwise compare rolling halves.
    # ------------------------------------------------
    samples = get_recent_transaction_samples()
    stored_baseline = get_model_drift_baseline()
    if stored_baseline:
        baseline_values = stored_baseline["samples"]
        recent_samples = [
            row for row in samples
            if row.get("created_at")
            and row["created_at"] > stored_baseline["recorded_at"]
        ]
        baseline_amounts = [
            float(value) for value in baseline_values.get("amount", [])
        ]
        baseline_risks = [
            float(value) for value in baseline_values.get("risk_score", [])
        ]
        baseline_info = {
            "recorded_at": stored_baseline["recorded_at"],
            "recorded_by": stored_baseline["recorded_by"],
            "transaction_count": stored_baseline["transaction_count"],
            "amount_samples": len(baseline_amounts),
            "risk_score_samples": len(baseline_risks),
            "comparison": "transactions recorded after this baseline",
        }
        recent_amounts = [float(row["amount"]) for row in recent_samples]
        recent_risks = [
            float(row["risk_score"])
            for row in recent_samples
            if row["risk_score"] is not None
        ]
    else:
        half_samples = len(samples) // 2
        baseline_samples = samples[:half_samples]
        recent_samples = samples[half_samples:]
        baseline_amounts = [
            float(row["amount"]) for row in baseline_samples
        ]
        recent_amounts = [float(row["amount"]) for row in recent_samples]
        baseline_risks = [
            float(row["risk_score"])
            for row in baseline_samples
            if row["risk_score"] is not None
        ]
        recent_risks = [
            float(row["risk_score"])
            for row in recent_samples
            if row["risk_score"] is not None
        ]
        baseline_info = {
            "recorded_at": None,
            "recorded_by": None,
            "transaction_count": None,
            "amount_samples": len(baseline_amounts),
            "risk_score_samples": len(baseline_risks),
            "comparison": "older half vs recent half of the rolling sample",
        }

    minimum_samples = 10

    psi_amount = (
        _psi(baseline_amounts, recent_amounts)
        if min(len(baseline_amounts), len(recent_amounts)) >= minimum_samples
        else None
    )
    psi_risk = (
        _psi(baseline_risks, recent_risks)
        if min(len(baseline_risks), len(recent_risks)) >= minimum_samples
        else None
    )

    drift = [
        {
            "feature": "Transaction amount",
            "psi": psi_amount,
            "status": _drift_status(psi_amount),
        },
        {
            "feature": "Risk score distribution",
            "psi": psi_risk,
            "status": _drift_status(psi_risk),
        },
    ]

    # ------------------------------------------------
    # Anomaly-rate baseline comparison
    # ------------------------------------------------
    daily_half = max(1, len(anomaly_series) // 2)
    baseline_anomaly = (
        sum(item["rate"] for item in anomaly_series[:daily_half]) / daily_half
        if anomaly_series else 0.0
    )
    recent_count = max(1, len(anomaly_series) - daily_half)
    recent_anomaly = (
        sum(item["rate"] for item in anomaly_series[daily_half:]) / recent_count
        if len(anomaly_series) > daily_half else baseline_anomaly
    )
    # ------------------------------------------------
    # Analyst outcome feedback is selective and is not population ground truth.
    # ------------------------------------------------
    decisions = get_analyst_decision_counts()
    resolved_outcomes = get_resolved_analyst_outcome_trend(days=14)
    resolved_conclusive = sum(
        int(row["confirmed"] or 0) + int(row["legitimate"] or 0)
        for row in resolved_outcomes
    )
    resolved_confirmed = sum(int(row["confirmed"] or 0) for row in resolved_outcomes)
    resolved_confirmation_rate = (
        round(resolved_confirmed / resolved_conclusive, 4)
        if resolved_conclusive
        else None
    )
    resolved_outcome_series = []
    for row in resolved_outcomes:
        conclusive = int(row["confirmed"] or 0) + int(row["legitimate"] or 0)
        if not conclusive:
            continue
        resolved_outcome_series.append({
            "day": row["day"],
            "value": round(int(row["confirmed"] or 0) / conclusive, 4),
        })
    confirmed = decisions.get("FRAUD_CONFIRMED", 0)
    legitimate = decisions.get("LEGITIMATE", 0)
    inconclusive = decisions.get("INCONCLUSIVE", 0)
    decided = confirmed + legitimate + inconclusive

    analyst_confirmation_rate = (
        round(confirmed / (confirmed + legitimate), 4)
        if (confirmed + legitimate)
        else None
    )

    # ------------------------------------------------
    # Baseline update recommendation
    # ------------------------------------------------
    retrain_reasons = [
        f"{item['feature']} drifted (PSI {item['psi']})"
        for item in drift
        if item["status"] in ("warning", "critical")
    ]

    if recent_anomaly > baseline_anomaly * 2 and baseline_anomaly > 0:
        retrain_reasons.append(
            f"Anomaly rate {recent_anomaly} vs baseline {baseline_anomaly}"
        )

    retrain_recommended = bool(retrain_reasons)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    latest_day = anomaly_series[-1]["day"] if anomaly_series else today

    # ------------------------------------------------
    # Persist snapshot + raise drift audit alert
    # ------------------------------------------------
    record_model_monitoring_snapshot(latest_day, {
        "total_transactions": (
            anomaly_series[-1]["total"] if anomaly_series else 0
        ),
        "anomaly_rate": anomaly_series[-1]["rate"] if anomaly_series else 0.0,
        "high_rate": anomaly_series[-1]["high"] if anomaly_series else 0.0,
        "medium_rate": anomaly_series[-1]["medium"] if anomaly_series else 0.0,
        "avg_risk": risk_series[-1]["value"] if risk_series else 0.0,
        "avg_amount": daily[-1]["avg_amount"] if daily else 0.0,
        "psi_amount": psi_amount,
        "psi_risk": psi_risk,
        "decisions": decisions,
    })

    if retrain_recommended and not model_drift_logged_today(today):
        create_audit_log(
            None,
            "MODEL_DRIFT_DETECTED",
            "ADAPTIVE_INTELLIGENCE",
            f"{today} | " + "; ".join(retrain_reasons),
        )
        events.publish("monitor", {
            "reason": "drift",
            "features": [
                item["feature"]
                for item in drift
                if item["status"] in ("warning", "critical")
            ],
        })

    latest_model = get_latest_model_version()
    active_model_version = latest_model["version"] if latest_model else "V3.0-HYBRID"
    recent_transactions = get_recent_transactions_feed(limit=5)
    flag_rates_data = compute_flag_rates()
    tracked = flag_rates_data.get("tracked", 0)
    today_count = next((item["total"] for item in anomaly_series if item["day"] == today), 0)

    # ------------------------------------------------
    # Automated baseline-capture trigger evaluation.
    # ------------------------------------------------
    if stored_baseline:
        reference_count = int(stored_baseline["transaction_count"])
        new_tx_since_baseline = max(0, tracked - reference_count)
    elif latest_model and latest_model.get("created_at"):
        trained_samples = int(latest_model.get("samples_count") or 0)
        if trained_samples > 0:
            new_tx_since_baseline = max(0, tracked - trained_samples)
        else:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM transactions WHERE created_at > ?",
                    (latest_model["created_at"],),
                ).fetchone()
                new_tx_since_baseline = int(row["count"]) if row else 0
            finally:
                conn.close()
    elif latest_model and int(latest_model.get("samples_count") or 0) > 0:
        new_tx_since_baseline = max(
            0,
            tracked - int(latest_model["samples_count"]),
        )
    else:
        new_tx_since_baseline = tracked

    psi_critical_breach = any(
        item["psi"] is not None and item["psi"] >= DRIFT_CRIT
        for item in drift
    )
    volume_breach = new_tx_since_baseline >= 50
    automated_trigger_required = psi_critical_breach or volume_breach

    automated_trigger_reasons = []
    if psi_critical_breach:
        breached_feats = [
            f"{item['feature']} (PSI: {item['psi']:.4f} >= 0.25)"
            for item in drift
            if item["psi"] is not None and item["psi"] >= DRIFT_CRIT
        ]
        automated_trigger_reasons.append(
            f"Critical population drift detected on: {', '.join(breached_feats)}"
        )
    if volume_breach:
        automated_trigger_reasons.append(
            f"New transaction volume ({new_tx_since_baseline}) reached the "
            "baseline-capture threshold (50 transactions)"
        )

    automated_trigger = {
        "action_required": automated_trigger_required,
        "psi_critical_breach": psi_critical_breach,
        "volume_breach": volume_breach,
        "psi_threshold": DRIFT_CRIT,
        "volume_threshold": 50,
        "new_transactions_since_baseline": new_tx_since_baseline,
        "new_transactions_since_retrain": new_tx_since_baseline,
        "reasons": automated_trigger_reasons,
        "auto_executable": True,
        "recommended_action": (
            "RECORD_DRIFT_BASELINE"
            if automated_trigger_required
            else "MONITOR_STABLE"
        ),
    }

    return {
        "active_model_version": active_model_version,
        "recent_transactions": recent_transactions,
        "tracked": tracked,
        "today_transactions_count": today_count,
        "anomaly_series": anomaly_series,
        "risk_series": risk_series,
        "drift": drift,
        "drift_baseline": baseline_info,
        "flag_rates": flag_rates_data,
        "decisions": decisions,
        "analyst_confirmation_rate": analyst_confirmation_rate,
        "resolved_case_feedback": {
            "conclusive_cases": resolved_conclusive,
            "confirmation_rate": resolved_confirmation_rate,
            "trend": resolved_outcome_series,
            "window_days": 14,
        },
        "runtime_model_evaluation": _get_runtime_model_evaluation(),
        "decided": decided,
        "confirmed": confirmed,
        "legitimate": legitimate,
        "inconclusive": inconclusive,
        "baseline_anomaly": round(baseline_anomaly, 4),
        "recent_anomaly": round(recent_anomaly, 4),
        "baseline_update_recommended": retrain_recommended,
        "baseline_update_reasons": retrain_reasons,
        "retrain_recommended": retrain_recommended,
        "retrain_reasons": retrain_reasons,
        "automated_trigger": automated_trigger,
        "latest_day": latest_day,
        "monitored_days": len(daily),
        "event_timestamp": datetime.now(timezone.utc).isoformat(),
    }


def record_drift_baseline(triggered_by="admin"):
    """Capture current transaction distributions as an audited drift baseline."""
    connection = get_connection()
    try:
        row = connection.execute("SELECT COUNT(*) AS total FROM transactions").fetchone()
        tracked = int(row["total"]) if row else 0
    finally:
        connection.close()

    recent_samples = get_recent_transaction_samples()
    baseline_samples = {
        "amount": [float(row["amount"]) for row in recent_samples],
        "risk_score": [
            float(row["risk_score"])
            for row in recent_samples
            if row["risk_score"] is not None
        ],
    }
    baseline = record_model_drift_baseline(
        baseline_samples,
        tracked,
        triggered_by or "SYSTEM",
    )
    latest_model = get_latest_model_version()
    active_model_version = (
        latest_model["version"] if latest_model else "V3.0-HYBRID"
    )

    create_audit_log(
        None,
        "MODEL_DRIFT_BASELINE_RECORDED",
        triggered_by or "SYSTEM",
        (
            f"Drift reference captured from {len(baseline_samples['amount'])} "
            f"recent transactions at {baseline['recorded_at']}; "
            f"{tracked} transactions existed in total. Model "
            f"{active_model_version} was not refit or versioned."
        ),
    )

    events.publish(
        "monitor",
        {"reason": "drift_baseline_recorded"},
    )
    events.publish("stats", {"reason": "drift_baseline_recorded"})
    events.publish("audit", {"reason": "logged"})

    return {
        "status": "SUCCESS",
        "message": (
            f"Drift baseline captured from {len(baseline_samples['amount'])} "
            f"recent transactions at {baseline['recorded_at']}. The active "
            f"model remains {active_model_version}; no detector refit occurred."
        ),
        "active_model_version": active_model_version,
        "operation": "DRIFT_BASELINE_CAPTURE",
        "model_refit": False,
        "psi_baseline_reset": True,
        "tracked_transactions": tracked,
        "sample_count": len(baseline_samples["amount"]),
        "baseline_recorded_at": baseline["recorded_at"],
        "timestamp": baseline["recorded_at"],
    }