"""Reproducible PaySim-format Isolation Forest training and evaluation.

Example:
    python scripts/train_evaluate.py C:\\data\\paysim.csv --output-dir models\\paysim-run

The dataset is supplied locally by the operator; no transaction data is bundled.
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import RobustScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.feature_engineering import ISO_FEATURES


REQUIRED_COLUMNS = {
    "step",
    "type",
    "amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "oldbalanceDest",
    "newbalanceDest",
    "isFraud",
}


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sampled_dataset(csv_path, sample_size, seed):
    columns = pd.read_csv(csv_path, nrows=0).columns
    missing = sorted(REQUIRED_COLUMNS - set(columns))
    if missing:
        raise ValueError(f"PaySim CSV is missing required columns: {', '.join(missing)}")

    eligible_count = sum(
        int(chunk["type"].isin(["TRANSFER", "CASH_OUT"]).sum())
        for chunk in pd.read_csv(
            csv_path, usecols=["type"], chunksize=250_000
        )
    )
    if sample_size > eligible_count:
        raise ValueError(
            f"Sample size {sample_size} exceeds eligible transaction count "
            f"{eligible_count}."
        )

    selected_rows = np.sort(
        np.random.default_rng(seed).choice(
            eligible_count,
            size=sample_size,
            replace=False,
        )
    )
    sampled_chunks = []
    eligible_offset = 0
    for chunk in pd.read_csv(csv_path, chunksize=250_000):
        eligible = chunk.loc[chunk["type"].isin(["TRANSFER", "CASH_OUT"])]
        chunk_end = eligible_offset + len(eligible)
        start = np.searchsorted(selected_rows, eligible_offset, side="left")
        end = np.searchsorted(selected_rows, chunk_end, side="left")
        if start < end:
            sampled_chunks.append(
                eligible.iloc[selected_rows[start:end] - eligible_offset]
            )
        eligible_offset = chunk_end

    return pd.concat(sampled_chunks, ignore_index=True), eligible_count


def _prepare_dataset(csv_path, sample_size=None, seed=42):
    if sample_size is not None and sample_size < 1:
        raise ValueError("Sample size must be at least 1.")

    frame, eligible_population_count = (
        _read_sampled_dataset(csv_path, sample_size, seed)
        if sample_size is not None
        else (pd.read_csv(csv_path), None)
    )
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"PaySim CSV is missing required columns: {', '.join(missing)}")
    frame = frame.loc[frame["type"].isin(["TRANSFER", "CASH_OUT"])].copy()
    frame = frame.sort_values("step", kind="stable").reset_index(drop=True)
    if eligible_population_count is None:
        eligible_population_count = len(frame)
    labels = frame["isFraud"].astype(int).to_numpy()

    destinations = (
        frame["nameDest"].astype(str).to_numpy()
        if "nameDest" in frame
        else np.asarray([f"DEST-{index}" for index in range(len(frame))])
    )
    destination_counts = Counter()
    destination_history = np.zeros(len(frame), dtype=np.int64)
    for positions in frame.groupby("step", sort=False).indices.values():
        step_destinations = destinations[positions]
        destination_history[positions] = [
            destination_counts[destination]
            for destination in step_destinations
        ]
        destination_counts.update(step_destinations)

    amount = frame["amount"].astype(float).to_numpy()
    origin_balance = frame["oldbalanceOrg"].astype(float).to_numpy()
    new_origin_balance = frame["newbalanceOrig"].astype(float).to_numpy()
    destination_balance = frame["oldbalanceDest"].astype(float).to_numpy()
    new_destination_balance = frame["newbalanceDest"].astype(float).to_numpy()
    transaction_types = frame["type"].to_numpy()
    matrix = pd.DataFrame(
        {
            "amount": amount,
            "log_amount": np.log1p(amount),
            "oldbalanceOrg": origin_balance,
            "newbalanceOrig": new_origin_balance,
            "oldbalanceDest": destination_balance,
            "newbalanceDest": new_destination_balance,
            "errorBalanceOrig": (
                origin_balance - amount - new_origin_balance
            ),
            "errorBalanceDest": (
                destination_balance + amount - new_destination_balance
            ),
            "amount_to_origin_balance": np.divide(
                amount,
                origin_balance,
                out=np.zeros_like(amount),
                where=origin_balance > 0,
            ),
            "amount_to_dest_balance": np.divide(
                amount,
                destination_balance,
                out=np.zeros_like(amount),
                where=destination_balance > 0,
            ),
            "dest_previous_count": destination_history,
            "recipient_novel": (destination_history == 0).astype(int),
            "is_transfer": (transaction_types == "TRANSFER").astype(int),
            "is_cashout": (transaction_types == "CASH_OUT").astype(int),
        },
        columns=ISO_FEATURES,
    )
    matrix = matrix.replace([np.inf, -np.inf], np.nan)
    return matrix, labels, frame["step"].to_numpy(), eligible_population_count


def prepare_dataset(csv_path, sample_size=None, seed=42):
    matrix, labels, _, _ = _prepare_dataset(csv_path, sample_size, seed)
    return matrix, labels


def _temporal_split_indices(steps, train_fraction=0.60, validation_fraction=0.20):
    step_values, step_counts = np.unique(steps, return_counts=True)
    if len(step_values) < 3:
        raise ValueError("Temporal evaluation requires at least three distinct steps.")

    cumulative_counts = np.cumsum(step_counts)
    row_count = len(steps)
    train_boundary_index = int(
        np.searchsorted(
            cumulative_counts, row_count * train_fraction, side="left"
        )
    )
    train_boundary_index = min(
        max(train_boundary_index, 0), len(step_values) - 3
    )
    validation_boundary_index = int(
        np.searchsorted(
            cumulative_counts,
            row_count * (train_fraction + validation_fraction),
            side="left",
        )
    )
    validation_boundary_index = min(
        max(validation_boundary_index, train_boundary_index + 1),
        len(step_values) - 2,
    )
    train_end = step_values[train_boundary_index]
    validation_end = step_values[validation_boundary_index]
    train_indices = np.flatnonzero(steps <= train_end)
    validation_indices = np.flatnonzero(
        (steps > train_end) & (steps <= validation_end)
    )
    test_indices = np.flatnonzero(steps > validation_end)
    return train_indices, validation_indices, test_indices


def _wilson_interval(successes, trials, z=1.959963984540054):
    if trials <= 0:
        return [None, None]
    proportion = successes / trials
    denominator = 1 + z**2 / trials
    center = (proportion + z**2 / (2 * trials)) / denominator
    half_width = (
        z
        * np.sqrt(
            proportion * (1 - proportion) / trials
            + z**2 / (4 * trials**2)
        )
        / denominator
    )
    return [
        float(max(0.0, center - half_width)),
        float(min(1.0, center + half_width)),
    ]


def _threshold_metrics(labels, scores, threshold):
    predictions = (scores >= threshold).astype(int)
    true_positives = int(np.sum((labels == 1) & (predictions == 1)))
    false_positives = int(np.sum((labels == 0) & (predictions == 1)))
    false_negatives = int(np.sum((labels == 1) & (predictions == 0)))
    true_negatives = int(np.sum((labels == 0) & (predictions == 0)))
    precision = float(
        precision_score(labels, predictions, zero_division=0)
    )
    recall = float(recall_score(labels, predictions, zero_division=0))
    f1 = float(f1_score(labels, predictions, zero_division=0))
    f2 = (
        5 * precision * recall / (4 * precision + recall)
        if precision + recall
        else 0.0
    )
    negatives = labels == 0
    false_positive_rate = (
        float(np.mean(predictions[negatives]))
        if np.any(negatives)
        else 0.0
    )
    return {
        "threshold": float(threshold),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "f2": float(f2),
        "false_positive_rate": false_positive_rate,
        "flagged_count": int(predictions.sum()),
        "fraud_count": int(labels.sum()),
        "true_positive_count": true_positives,
        "false_positive_count": false_positives,
        "false_negative_count": false_negatives,
        "true_negative_count": true_negatives,
        "precision_ci_95": _wilson_interval(
            true_positives, true_positives + false_positives
        ),
        "recall_ci_95": _wilson_interval(
            true_positives, true_positives + false_negatives
        ),
    }


def train_and_evaluate(csv_path, output_dir, seed=42, sample_size=None):
    matrix, labels, steps, eligible_population_count = _prepare_dataset(
        csv_path, sample_size, seed
    )
    if len(np.unique(labels)) != 2:
        raise ValueError("Evaluation requires both fraud and non-fraud samples.")
    train_idx, validation_idx, test_idx = _temporal_split_indices(steps)
    for name, indices in (
        ("train", train_idx),
        ("validation", validation_idx),
        ("test", test_idx),
    ):
        if len(np.unique(labels[indices])) != 2:
            raise ValueError(
                f"The temporal {name} split must contain fraud and "
                "non-fraud examples; choose a larger sample or different "
                "time coverage."
            )
    normal_train_idx = train_idx[labels[train_idx] == 0]
    medians = matrix.iloc[normal_train_idx].median()
    train = matrix.iloc[normal_train_idx].fillna(medians)
    validation = matrix.iloc[validation_idx].fillna(medians)
    test = matrix.iloc[test_idx].fillna(medians)
    scaler = RobustScaler()
    train_scaled = scaler.fit_transform(train)
    validation_scaled = scaler.transform(validation)
    test_scaled = scaler.transform(test)
    model = IsolationForest(
        n_estimators=200,
        contamination="auto",
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(train_scaled)

    train_raw = -model.decision_function(train_scaled)
    validation_raw = -model.decision_function(validation_scaled)
    test_raw = -model.decision_function(test_scaled)
    min_score = float(np.min(train_raw))
    max_score = float(np.max(train_raw))
    train_scores = np.clip(
        (train_raw - min_score) / (max_score - min_score + 1e-9), 0, 1
    )
    validation_scores = np.clip(
        (validation_raw - min_score) / (max_score - min_score + 1e-9), 0, 1
    )
    scores = np.clip(
        (test_raw - min_score) / (max_score - min_score + 1e-9), 0, 1
    )

    candidate_quantiles = (0.90, 0.95, 0.975, 0.99, 0.995)
    validation_thresholds = []
    for quantile in candidate_quantiles:
        threshold = float(np.quantile(train_scores, quantile))
        validation_metrics = _threshold_metrics(
            labels[validation_idx], validation_scores, threshold
        )
        validation_thresholds.append(
            {
                "training_normal_quantile": quantile,
                **validation_metrics,
            }
        )
    selected_threshold = max(
        validation_thresholds,
        key=lambda result: (
            result["f2"],
            result["precision"],
            -result["false_positive_rate"],
        ),
    )
    threshold = selected_threshold["threshold"]
    selected_test_metrics = _threshold_metrics(labels[test_idx], scores, threshold)
    baseline_threshold = float(np.quantile(train_scores, 0.99))
    baseline_test_metrics = _threshold_metrics(
        labels[test_idx], scores, baseline_threshold
    )

    latency_samples = []
    for row in test_scaled[: min(1000, len(test_scaled))]:
        started = time.perf_counter()
        model.decision_function(row.reshape(1, -1))
        latency_samples.append((time.perf_counter() - started) * 1000)
    dataset_hash = _sha256_file(csv_path)
    def split_summary(indices):
        split_steps = steps[indices]
        return {
            "rows": int(len(indices)),
            "fraud_labels": int(labels[indices].sum()),
            "step_min": int(np.min(split_steps)),
            "step_max": int(np.max(split_steps)),
        }

    metrics = {
        "dataset": Path(csv_path).name,
        "dataset_sha256": dataset_hash,
        "dataset_rows_used": int(len(labels)),
        "eligible_dataset_rows": int(eligible_population_count),
        "sampled_eligible_rows": int(len(labels)),
        "positive_count": int(labels.sum()),
        "seed": int(seed),
        "split": {
            "train": 0.60,
            "validation": 0.20,
            "test": 0.20,
            "type": "chronological",
            "ordered_by": "step",
            "same_step_kept_together": True,
        },
        "split_counts": {
            "train": split_summary(train_idx),
            "validation": split_summary(validation_idx),
            "test": split_summary(test_idx),
        },
        "threshold_selection": {
            "policy": (
                "Select the candidate maximizing validation F2 to weight fraud "
                "recall more than precision; ties prefer higher precision, then "
                "a lower false-positive rate. Model fitting uses only normal "
                "training rows."
            ),
            "operational_interpretation": (
                "A positive anomaly flag is a candidate for MEDIUM-risk "
                "step-up/review, not an automatic block. Runtime hybrid risk "
                "bands are not changed by this evaluation."
            ),
            "selected_training_normal_quantile": selected_threshold[
                "training_normal_quantile"
            ],
            "selected_threshold": threshold,
            "validation_candidates": validation_thresholds,
            "selected_holdout_metrics": selected_test_metrics,
            "baseline_99th_percentile": {
                "threshold": baseline_threshold,
                **baseline_test_metrics,
            },
        },
        "threshold_quantile": selected_threshold[
            "training_normal_quantile"
        ],
        "threshold": threshold,
        "roc_auc": float(roc_auc_score(labels[test_idx], scores)),
        "pr_auc": float(average_precision_score(labels[test_idx], scores)),
        "test_fraud_prevalence": float(np.mean(labels[test_idx])),
        "runtime_model_modified": False,
        "precision": selected_test_metrics["precision"],
        "recall": selected_test_metrics["recall"],
        "f1": selected_test_metrics["f1"],
        "f2": selected_test_metrics["f2"],
        "false_positive_rate": selected_test_metrics["false_positive_rate"],
        "mean_model_latency_ms_per_transaction": float(np.mean(latency_samples)),
        "p95_model_latency_ms_per_transaction": float(
            np.percentile(latency_samples, 95)
        ),
        "latency_measurement": "decision_function only; excludes HTTP and feature construction",
    }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output_dir / "isolation_forest_model.joblib")
    joblib.dump(scaler, output_dir / "robust_scaler.joblib")
    joblib.dump(medians, output_dir / "train_medians.joblib")
    config_path = Path(__file__).resolve().parents[1] / "models" / "hybrid_model_config.json"
    with config_path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    config.update(
        {
            "iso_min": min_score,
            "iso_max": max_score,
            "evaluation": metrics,
            "feature_names": ISO_FEATURES,
            "training_seed": seed,
            "roc_auc": metrics["roc_auc"],
            "pr_auc": metrics["pr_auc"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
        }
    )
    (output_dir / "hybrid_model_config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "evaluation.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Local PaySim-format CSV path")
    parser.add_argument("--output-dir", type=Path, default=Path("models/paysim-run"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--sample-size",
        type=int,
        help="Number of eligible TRANSFER/CASH_OUT rows to sample",
    )
    args = parser.parse_args()
    result = train_and_evaluate(args.csv, args.output_dir, args.seed, args.sample_size)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
