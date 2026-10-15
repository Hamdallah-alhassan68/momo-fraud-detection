"""
Adaptive Fraud Intelligence (AFI) module.

Implements the prototype's adaptive-monitoring requirements:

  * Distribution drift detection on monitored features (Population Stability
    Index on transaction amounts and risk-score distributions).
  * Analyst case-decision tracking as a limited, selection-biased outcome
    signal (not as model precision or recall).
  * Anomaly-rate and average-risk trends over time.
  * Drift and volume thresholds that recommend recording a prototype
    baseline marker; they do not refit the runtime fraud detector.
"""

import math
from datetime import datetime, timezone

from database.database import (
    get_connection,
    get_daily_transaction_stats,
    get_recent_transaction_samples,
    get_analyst_decision_counts,
    model_drift_logged_today,
    record_model_monitoring_snapshot,
    create_audit_log,
    record_model_version,
    get_latest_model_version,
    get_recent_transactions_feed,
)
from services import events

DRIFT_WARN = 0.10
DRIFT_CRIT = 0.25


def _drift_status(psi):
    if psi is None:
        return "insufficient_data"
    if psi >= DRIFT_CRIT:
        return "critical"
    if psi >= DRIFT_WARN:
        return "warning"
    return "stable"


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
    # Distribution drift (PSI) — older transaction half vs recent half.
    # ------------------------------------------------
    samples = get_recent_transaction_samples()
    half_samples = len(samples) // 2
    baseline_samples = samples[:half_samples]
    recent_samples = samples[half_samples:]
    minimum_samples = 10

    baseline_amounts = [float(row["amount"]) for row in baseline_samples]
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
    # Model performance (analyst decisions as ground truth)
    # ------------------------------------------------
    decisions = get_analyst_decision_counts()
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
    # Retraining recommendation
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
    # Automated Retraining Trigger Evaluation (BoG/ISO adaptive control)
    # ------------------------------------------------
    trained_samples = (latest_model.get("samples_count") or 0) if latest_model else 0
    if trained_samples > 0:
        new_tx_since_retrain = max(0, tracked - trained_samples)
    elif latest_model and latest_model.get("created_at"):
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM transactions WHERE created_at > ?",
                (latest_model["created_at"],),
            ).fetchone()
            new_tx_since_retrain = int(row["count"]) if row else 0
        finally:
            conn.close()
    else:
        new_tx_since_retrain = tracked

    psi_critical_breach = any(
        item["psi"] is not None and item["psi"] >= DRIFT_CRIT
        for item in drift
    )
    volume_breach = new_tx_since_retrain >= 50
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
            f"New transaction volume ({new_tx_since_retrain}) reached the prototype baseline-marker threshold (50 transactions)"
        )

    automated_trigger = {
        "action_required": automated_trigger_required,
        "psi_critical_breach": psi_critical_breach,
        "volume_breach": volume_breach,
        "psi_threshold": DRIFT_CRIT,
        "volume_threshold": 50,
        "new_transactions_since_retrain": new_tx_since_retrain,
        "reasons": automated_trigger_reasons,
        "auto_executable": True,
        "recommended_action": (
            "RECORD_PROTOTYPE_BASELINE_MARKER"
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
        "flag_rates": flag_rates_data,
        "decisions": decisions,
        "analyst_confirmation_rate": analyst_confirmation_rate,
        "decided": decided,
        "confirmed": confirmed,
        "legitimate": legitimate,
        "inconclusive": inconclusive,
        "baseline_anomaly": round(baseline_anomaly, 4),
        "recent_anomaly": round(recent_anomaly, 4),
        "retrain_recommended": retrain_recommended,
        "retrain_reasons": retrain_reasons,
        "automated_trigger": automated_trigger,
        "latest_day": latest_day,
        "monitored_days": len(daily),
        "event_timestamp": datetime.now(timezone.utc).isoformat(),
    }


def retrain_or_update_model(triggered_by="admin"):
    """
    Record a prototype baseline marker and audit entry.

    This updates version metadata and the transaction-volume counter only. It
    does not refit the detector, change scoring parameters, or reset the PSI
    comparison baseline.
    """
    connection = get_connection()
    try:
        row = connection.execute("SELECT COUNT(*) AS total FROM transactions").fetchone()
        tracked = int(row["total"]) if row else 0
    finally:
        connection.close()

    latest_model = get_latest_model_version()
    next_version = "V3.1-ADAPTIVE"
    if latest_model and latest_model.get("version"):
        # Increment from whatever the active version is, regardless of the
        # V-major prefix (V3.x, V4.x, ...), so rollover stays unique.
        try:
            prev_num = float(
                latest_model["version"].split("-")[0].replace("V", "").strip()
            )
            next_version = f"V{prev_num + 0.1:.1f}-ADAPTIVE"
        except Exception:
            next_version = "V3.1-ADAPTIVE"

    # These metrics remain unset because this operation does not evaluate a
    # newly fitted model.
    record_model_version(
        version=next_version,
        model_type="PrototypeBaselineMarker",
        status="ACTIVE",
        samples_count=tracked
    )

    create_audit_log(
        None,
        "MODEL_BASELINE_MARKER_RECORDED",
        triggered_by or "SYSTEM",
        (
            f"Prototype baseline marker recorded for {tracked} tracked transactions. "
            f"New active version: {next_version}. The detector was not refit, "
            "scoring parameters were not changed, and the PSI baseline was not reset."
        ),
    )

    # Broadcast event to all listening live tabs
    events.publish(
        "monitor",
        {"reason": "baseline_marker_recorded", "version": next_version},
    )
    events.publish("stats", {"reason": "baseline_marker_recorded"})
    events.publish("audit", {"reason": "logged"})

    return {
        "status": "SUCCESS",
        "message": (
            f"Prototype baseline marker {next_version} recorded for {tracked} "
            "tracked transactions. The detector was not refit and the PSI "
            "baseline was not reset."
        ),
        "version": next_version,
        "operation": "PROTOTYPE_BASELINE_MARKER",
        "model_refit": False,
        "psi_baseline_reset": False,
        "tracked_transactions": tracked,
        # Retained for compatibility; the value counts transactions, not
        # samples used to fit a new detector.
        "trained_on_samples": tracked,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }