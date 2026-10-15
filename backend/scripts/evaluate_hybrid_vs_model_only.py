"""Compare hybrid and model-only detection on temporal PaySim holdouts.

Each run uses a separately trained Isolation Forest artifact and the project's
configured rule weights. Results are synthetic-data experiments, not runtime
threshold recommendations or independent validation of the shipped model.
"""

import argparse
import gc
import hashlib
import json
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.train_evaluate import (
    _prepare_dataset,
    _temporal_split_indices,
    _threshold_metrics,
)


CANDIDATE_QUANTILES = (0.90, 0.95, 0.975, 0.99, 0.995)
COMPARISON_NAMES = (
    "model_only_validation_selected_threshold",
    "model_only_same_numeric_cutoff_0_3",
    "model_only_same_numeric_cutoff_0_5",
    "hybrid_validation_selected_threshold",
    "hybrid_medium_or_higher_cutoff",
    "hybrid_high_decision_cutoff",
)


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _calculate_rule_scores(matrix, amount_threshold):
    values = matrix
    return (
        0.20
        * (
            (values["oldbalanceOrg"].to_numpy() > 0)
            & (values["newbalanceOrig"].to_numpy() == 0)
        )
        + 0.10 * (values["newbalanceDest"].to_numpy() == 0)
        + 0.10 * (values["recipient_novel"].to_numpy() != 0)
        + 0.15 * (values["amount_to_origin_balance"].to_numpy() >= 0.90)
        + 0.10 * (values["amount"].to_numpy() >= amount_threshold)
    ).astype(float)


def _combine_hybrid_scores(iso_scores, rule_scores, step_up_signal, config):
    scores = np.clip(
        config["if_weight"] * iso_scores
        + config["rule_weight"] * rule_scores,
        0.0,
        1.0,
    )
    return np.where(
        step_up_signal,
        np.maximum(scores, config["low_threshold"]),
        scores,
    )


def _select_validation_threshold(labels, scores, normal_train_scores):
    candidates = []
    for quantile in CANDIDATE_QUANTILES:
        threshold = float(np.quantile(normal_train_scores, quantile))
        candidates.append(
            {
                "training_normal_quantile": quantile,
                **_threshold_metrics(labels, scores, threshold),
            }
        )
    return max(
        candidates,
        key=lambda result: (
            result["f2"],
            result["precision"],
            -result["false_positive_rate"],
        ),
    ), candidates


def _metrics_by_transaction_type(labels, scores, threshold, transaction_types):
    results = {}
    for transaction_type in ("TRANSFER", "CASH_OUT"):
        mask = transaction_types == transaction_type
        subset_labels = labels[mask]
        subset_scores = scores[mask]
        results[transaction_type] = {
            "rows": int(mask.sum()),
            "fraud_labels": int(subset_labels.sum()),
            **_threshold_metrics(subset_labels, subset_scores, threshold),
            "roc_auc": (
                float(roc_auc_score(subset_labels, subset_scores))
                if len(np.unique(subset_labels)) == 2
                else None
            ),
            "pr_auc": (
                float(average_precision_score(subset_labels, subset_scores))
                if len(np.unique(subset_labels)) == 2
                else None
            ),
        }
    return results


def _summarize_runs(runs):
    count_keys = ("rows", "fraud_labels")
    comparison_keys = (
        "precision",
        "recall",
        "f1",
        "f2",
        "false_positive_rate",
        "flagged_count",
        "roc_auc",
        "pr_auc",
    )
    summary = {}
    for comparison_name in COMPARISON_NAMES:
        entries = [run["test_comparison"][comparison_name] for run in runs]
        metrics_summary = {}
        for key in comparison_keys:
            values = [entry[key] for entry in entries]
            metrics_summary[key] = {
                "mean": float(np.mean(values)),
                "sample_stddev": (
                    float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
                ),
                "min": float(np.min(values)),
                "max": float(np.max(values)),
            }
        by_type_summary = {}
        for transaction_type in ("TRANSFER", "CASH_OUT"):
            type_metrics = {}
            for key in count_keys:
                values = [
                    entry["by_transaction_type"][transaction_type][key]
                    for entry in entries
                ]
                type_metrics[key] = {
                    "mean": float(np.mean(values)),
                    "sample_stddev": (
                        float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
                    ),
                    "min": int(np.min(values)),
                    "max": int(np.max(values)),
                }
            for key in comparison_keys:
                values = [
                    entry["by_transaction_type"][transaction_type][key]
                    for entry in entries
                    if entry["by_transaction_type"][transaction_type][key]
                    is not None
                ]
                type_metrics[key] = (
                    {
                        "mean": float(np.mean(values)),
                        "sample_stddev": (
                            float(np.std(values, ddof=1))
                            if len(values) > 1
                            else 0.0
                        ),
                        "min": float(np.min(values)),
                        "max": float(np.max(values)),
                    }
                    if values
                    else None
                )
            by_type_summary[transaction_type] = type_metrics
        summary[comparison_name] = {
            "overall": metrics_summary,
            "by_transaction_type": by_type_summary,
        }
    return summary


def evaluate_run(dataset_path, model_directory, sample_size=None, seed=42):
    matrix, labels, steps, eligible_count = _prepare_dataset(
        dataset_path, sample_size, seed
    )
    train_idx, validation_idx, test_idx = _temporal_split_indices(steps)
    splits = {
        "train": train_idx,
        "validation": validation_idx,
        "test": test_idx,
    }
    for split_name, indices in splits.items():
        if len(np.unique(labels[indices])) != 2:
            raise ValueError(
                f"The temporal {split_name} split must contain both classes."
            )

    model_directory = Path(model_directory)
    model = joblib.load(model_directory / "isolation_forest_model.joblib")
    scaler = joblib.load(model_directory / "robust_scaler.joblib")
    medians = joblib.load(model_directory / "train_medians.joblib")
    config = json.loads(
        (model_directory / "hybrid_model_config.json").read_text(
            encoding="utf-8"
        )
    )

    prepared = matrix.fillna(medians)
    scaled = scaler.transform(prepared)
    raw_scores = -model.decision_function(scaled)
    min_score = float(config["iso_min"])
    max_score = float(config["iso_max"])
    iso_scores = np.clip(
        (raw_scores - min_score) / (max_score - min_score + 1e-9),
        0,
        1,
    )
    del prepared, scaled, raw_scores
    gc.collect()

    rule_scores = _calculate_rule_scores(matrix, config["amount_threshold"])
    step_up_signal = matrix["recipient_novel"].to_numpy() != 0
    hybrid_scores = _combine_hybrid_scores(
        iso_scores, rule_scores, step_up_signal, config
    )

    normal_train_mask = labels[train_idx] == 0
    model_selected, model_candidates = _select_validation_threshold(
        labels[validation_idx],
        iso_scores[validation_idx],
        iso_scores[train_idx][normal_train_mask],
    )
    hybrid_selected, hybrid_candidates = _select_validation_threshold(
        labels[validation_idx],
        hybrid_scores[validation_idx],
        hybrid_scores[train_idx][normal_train_mask],
    )
    model_test = _threshold_metrics(
        labels[test_idx], iso_scores[test_idx], model_selected["threshold"]
    )
    hybrid_test = _threshold_metrics(
        labels[test_idx], hybrid_scores[test_idx], hybrid_selected["threshold"]
    )
    production_cutoff = float(config["medium_threshold"])
    step_up_cutoff = float(config["low_threshold"])
    model_only_low_cutoff_test = _threshold_metrics(
        labels[test_idx], iso_scores[test_idx], step_up_cutoff
    )
    model_only_high_cutoff_test = _threshold_metrics(
        labels[test_idx], iso_scores[test_idx], production_cutoff
    )
    fixed_policy_test = _threshold_metrics(
        labels[test_idx], hybrid_scores[test_idx], production_cutoff
    )
    step_up_policy_test = _threshold_metrics(
        labels[test_idx], hybrid_scores[test_idx], step_up_cutoff
    )
    types = matrix["is_transfer"].to_numpy()
    transaction_types = np.where(types == 1, "TRANSFER", "CASH_OUT")

    def split_summary(indices):
        return {
            "rows": int(len(indices)),
            "fraud_labels": int(labels[indices].sum()),
            "step_min": int(np.min(steps[indices])),
            "step_max": int(np.max(steps[indices])),
        }

    result = {
        "seed": int(seed),
        "model_artifact_directory": str(model_directory.name),
        "sampled_eligible_rows": int(len(labels)),
        "eligible_population_rows": int(eligible_count),
        "positive_labels": int(labels.sum()),
        "split_counts": {
            name: split_summary(indices)
            for name, indices in splits.items()
        },
        "runtime_rule_configuration": {
            "if_weight": float(config["if_weight"]),
            "rule_weight": float(config["rule_weight"]),
            "low_threshold": float(config["low_threshold"]),
            "medium_threshold": production_cutoff,
            "amount_threshold": float(config["amount_threshold"]),
        },
        "rule_signal_coverage": {
            "account_drained": int(
                (
                    (matrix["oldbalanceOrg"].to_numpy() > 0)
                    & (matrix["newbalanceOrig"].to_numpy() == 0)
                )[test_idx].sum()
            ),
            "zero_destination_balance": int(
                (matrix["newbalanceDest"].to_numpy() == 0)[test_idx].sum()
            ),
            "novel_recipient": int(step_up_signal[test_idx].sum()),
            "high_origin_balance_ratio": int(
                (
                    matrix["amount_to_origin_balance"].to_numpy() >= 0.90
                )[test_idx].sum()
            ),
            "amount_over_configured_threshold": int(
                (matrix["amount"].to_numpy() >= config["amount_threshold"])[
                    test_idx
                ].sum()
            ),
            "unavailable_signals": [
                "device_change",
                "ip_network_mismatch",
                "recent_sim_swap",
                "amount_deviation",
                "unusual_hour",
                "velocity_spike",
                "frequency_spike",
            ],
        },
        "validation_threshold_selection": {
            "policy": (
                "For model-only and hybrid score, select the training-normal "
                "quantile candidate with highest validation F2; ties prefer "
                "precision then lower false-positive rate."
            ),
            "model_only": {
                "selected_training_normal_quantile": model_selected[
                    "training_normal_quantile"
                ],
                "selected_threshold": model_selected["threshold"],
                "validation_candidates": model_candidates,
            },
            "hybrid": {
                "selected_training_normal_quantile": hybrid_selected[
                    "training_normal_quantile"
                ],
                "selected_threshold": hybrid_selected["threshold"],
                "validation_candidates": hybrid_candidates,
            },
        },
        "test_comparison": {
            "model_only_validation_selected_threshold": {
                **model_test,
                "roc_auc": float(
                    roc_auc_score(labels[test_idx], iso_scores[test_idx])
                ),
                "pr_auc": float(
                    average_precision_score(labels[test_idx], iso_scores[test_idx])
                ),
                "by_transaction_type": _metrics_by_transaction_type(
                    labels[test_idx],
                    iso_scores[test_idx],
                    model_selected["threshold"],
                    transaction_types[test_idx],
                ),
            },
            "model_only_same_numeric_cutoff_0_3": {
                "threshold": step_up_cutoff,
                **model_only_low_cutoff_test,
                "roc_auc": float(
                    roc_auc_score(labels[test_idx], iso_scores[test_idx])
                ),
                "pr_auc": float(
                    average_precision_score(labels[test_idx], iso_scores[test_idx])
                ),
                "by_transaction_type": _metrics_by_transaction_type(
                    labels[test_idx],
                    iso_scores[test_idx],
                    step_up_cutoff,
                    transaction_types[test_idx],
                ),
            },
            "model_only_same_numeric_cutoff_0_5": {
                "threshold": production_cutoff,
                **model_only_high_cutoff_test,
                "roc_auc": float(
                    roc_auc_score(labels[test_idx], iso_scores[test_idx])
                ),
                "pr_auc": float(
                    average_precision_score(labels[test_idx], iso_scores[test_idx])
                ),
                "by_transaction_type": _metrics_by_transaction_type(
                    labels[test_idx],
                    iso_scores[test_idx],
                    production_cutoff,
                    transaction_types[test_idx],
                ),
            },
            "hybrid_validation_selected_threshold": {
                **hybrid_test,
                "roc_auc": float(
                    roc_auc_score(labels[test_idx], hybrid_scores[test_idx])
                ),
                "pr_auc": float(
                    average_precision_score(
                        labels[test_idx], hybrid_scores[test_idx]
                    )
                ),
                "by_transaction_type": _metrics_by_transaction_type(
                    labels[test_idx],
                    hybrid_scores[test_idx],
                    hybrid_selected["threshold"],
                    transaction_types[test_idx],
                ),
            },
            "hybrid_medium_or_higher_cutoff": {
                "threshold": step_up_cutoff,
                **step_up_policy_test,
                "roc_auc": float(
                    roc_auc_score(labels[test_idx], hybrid_scores[test_idx])
                ),
                "pr_auc": float(
                    average_precision_score(
                        labels[test_idx], hybrid_scores[test_idx]
                    )
                ),
                "interpretation": (
                    "Rows scoring at or above the configured low threshold are "
                    "MEDIUM-or-HIGH. Test results are diagnostic only and do "
                    "not constitute calibration."
                ),
                "by_transaction_type": _metrics_by_transaction_type(
                    labels[test_idx],
                    hybrid_scores[test_idx],
                    step_up_cutoff,
                    transaction_types[test_idx],
                ),
            },
            "hybrid_high_decision_cutoff": {
                "threshold": production_cutoff,
                **fixed_policy_test,
                "roc_auc": float(
                    roc_auc_score(labels[test_idx], hybrid_scores[test_idx])
                ),
                "pr_auc": float(
                    average_precision_score(
                        labels[test_idx], hybrid_scores[test_idx]
                    )
                ),
                "interpretation": (
                    "Rows scoring at or above the configured medium boundary "
                    "are HIGH under the runtime policy. Test results are "
                    "diagnostic only and do not constitute calibration."
                ),
                "by_transaction_type": _metrics_by_transaction_type(
                    labels[test_idx],
                    hybrid_scores[test_idx],
                    production_cutoff,
                    transaction_types[test_idx],
                ),
            },
        },
        "limitations": [
            "Synthetic PaySim labels and synthetic transaction features only.",
            "Device, IP, SIM-swap, customer-deviation, unusual-hour, velocity, and frequency signals are absent and are not simulated as positive.",
            "Recipient history is derived only from sampled prior steps, which may differ from full production history.",
            "Each seed uses a model fitted on that seed's earlier training period; this does not independently validate the shipped model.",
            "Wilson confidence intervals describe per-test-set proportions only and do not account for temporal clustering or seed variation.",
            "Runtime model artifacts and thresholds are unchanged.",
        ],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--sample-size", type=int, default=300_000)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 7, 21, 99])
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path("models"),
        help="Directory containing temporal run subdirectories",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/synthetic-paysim-hybrid-comparison.json"),
    )
    args = parser.parse_args()
    runs = []
    for seed in args.seeds:
        run_name = (
            "synthetic-paysim-temporal-300k"
            if seed == 42
            else f"synthetic-paysim-temporal-300k-seed-{seed}"
        )
        runs.append(
            evaluate_run(
                args.dataset,
                args.models_dir / run_name,
                sample_size=args.sample_size,
                seed=seed,
            )
        )
        gc.collect()

    comparison = {
        "evaluation_type": "hybrid_vs_model_only_temporal_test",
        "dataset": args.dataset.name,
        "dataset_sha256": _sha256_file(args.dataset),
        "sample_size_per_seed": args.sample_size,
        "seeds": list(args.seeds),
        "split_policy": (
            "Chronological 60/20/20 by step; complete steps stay in one split; "
            "thresholds selected on validation only."
        ),
        "score_definitions": {
            "model_only": "Normalized Isolation Forest anomaly score.",
            "hybrid": (
                "Configured Isolation Forest/rule weighted score, clipped and "
                "with the production minimum-score floor for novel recipients."
            ),
            "production_policy": (
                "The configured low boundary (0.3) marks MEDIUM-or-HIGH and "
                "the medium boundary (0.5) marks HIGH; both are diagnostics, "
                "not approvals to deploy or recalibrate."
            ),
        },
        "runs": runs,
        "descriptive_seed_summary": _summarize_runs(runs),
        "runtime_model_modified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(comparison, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "seeds": args.seeds}, indent=2))


if __name__ == "__main__":
    main()
