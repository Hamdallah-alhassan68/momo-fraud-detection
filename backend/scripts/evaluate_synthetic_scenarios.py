"""Evaluate the hybrid scorer on repeatable, engineered simulator scenarios.

These are policy checks built from synthetic feature inputs, not statistical
model-performance estimates or substitutes for a labelled PaySim evaluation.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.fraud_detector import fraud_detector


SCENARIOS = {
    "NORMAL": {
        "transaction": {},
        "dest_previous_count": 8,
        "expected_risk_level": "LOW",
    },
    "RECENT_SIM_SWAP": {
        "transaction": {"recent_sim_swap": True},
        "dest_previous_count": 8,
        "expected_risk_level": "MEDIUM",
    },
    "DEVICE_CHANGE": {
        "transaction": {"device_change": True},
        "dest_previous_count": 8,
        "expected_risk_level": "MEDIUM",
    },
    "IP_MISMATCH": {
        "transaction": {"ip_network_mismatch": True},
        "dest_previous_count": 8,
        "expected_risk_level": "MEDIUM",
    },
    "VELOCITY_SPIKE": {
        "transaction": {"velocity_spike": True},
        "dest_previous_count": 8,
        "expected_risk_level": "MEDIUM",
    },
    "FREQUENCY_SPIKE": {
        "transaction": {"frequency_spike": True},
        "dest_previous_count": 8,
        "expected_risk_level": "MEDIUM",
    },
    "AMOUNT_DEVIATION": {
        "transaction": {"amount_deviation": True},
        "dest_previous_count": 8,
        "expected_risk_level": "MEDIUM",
    },
    "UNUSUAL_HOUR": {
        "transaction": {"unusual_hour": True},
        "dest_previous_count": 8,
        "expected_risk_level": "MEDIUM",
    },
    "NEW_RECIPIENT": {
        "transaction": {},
        "dest_previous_count": 0,
        "expected_risk_level": "LOW",
    },
    "HIGH_BALANCE_SHARE_ONLY": {
        "transaction": {
            "amount": 9_000.0,
            "new_balance_origin": 1_000.0,
        },
        "dest_previous_count": 8,
        "expected_risk_level": "LOW",
    },
    "DRAINED_HIGH_BALANCE_SHARE": {
        "transaction": {
            "amount": 9_000.0,
            "new_balance_origin": 0.0,
        },
        "dest_previous_count": 8,
        "expected_risk_level": "MEDIUM",
    },
    "DRAINED_NEW_RECIPIENT": {
        "transaction": {
            "amount": 9_000.0,
            "new_balance_origin": 0.0,
            "new_balance_destination": 0.0,
        },
        "dest_previous_count": 0,
        "expected_risk_level": "MEDIUM",
    },
    "COMPOUND_SECURITY_SIGNALS": {
        "transaction": {
            "recent_sim_swap": True,
            "device_change": True,
            "ip_network_mismatch": True,
            "amount_deviation": True,
            "unusual_hour": True,
            "velocity_spike": True,
            "frequency_spike": True,
        },
        "dest_previous_count": 8,
        "expected_risk_level": "HIGH",
    },
}


def evaluate_synthetic_scenarios(runs=100):
    if runs < 1:
        raise ValueError("runs must be at least 1")

    base_transaction = {
        "amount": 200.0,
        "transaction_type": "TRANSFER",
        "old_balance_origin": 10_000.0,
        "new_balance_origin": 9_800.0,
        "old_balance_destination": 10_000.0,
        "new_balance_destination": 10_200.0,
    }
    scenario_results = []
    risk_level_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    expected_risk_level_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}

    for scenario, specification in SCENARIOS.items():
        transaction = {
            **base_transaction,
            **specification["transaction"],
        }
        expected_risk_level = specification["expected_risk_level"]
        expected_risk_level_counts[expected_risk_level] += 1
        timings_ms = []
        assessment = None

        for _ in range(runs):
            started = time.perf_counter()
            assessment = fraud_detector.assess_transaction(
                transaction,
                dest_previous_count=specification["dest_previous_count"],
            )
            timings_ms.append((time.perf_counter() - started) * 1000)

        actual_risk_level = assessment["risk_level"]
        risk_level_counts[actual_risk_level] += 1
        detected_positive = assessment["risk_level"] != "LOW"
        outcome_matches_expectation = (
            actual_risk_level == expected_risk_level
        )

        scenario_results.append({
            "scenario": scenario,
            "expected_risk_level": expected_risk_level,
            "risk_score": assessment["risk_score"],
            "risk_level": actual_risk_level,
            "detected_as_risk": detected_positive,
            "outcome_matches_expectation": outcome_matches_expectation,
            "reason_codes": assessment["reasons"],
            "mean_scoring_latency_ms": float(np.mean(timings_ms)),
            "p95_scoring_latency_ms": float(np.percentile(timings_ms, 95)),
            "latency_runs": runs,
        })

    passing_scenarios = sum(
        result["outcome_matches_expectation"]
        for result in scenario_results
    )
    failing_scenarios = [
        result["scenario"]
        for result in scenario_results
        if not result["outcome_matches_expectation"]
    ]

    return {
        "evaluation_type": "synthetic_engineered_scenarios",
        "label_warning": (
            "Expected risk levels are policy checks for engineered input "
            "conditions, not independently verified fraud outcomes. Scenario "
            "pass rates are not PaySim or real-world detection performance."
        ),
        "latency_scope": (
            "Hybrid scorer including feature construction, Isolation Forest scoring, "
            "and rule evaluation; excludes HTTP, database, and client latency."
        ),
        "summary": {
            "scenario_count": len(SCENARIOS),
            "passing_scenarios": passing_scenarios,
            "failing_scenarios": failing_scenarios,
            "scenario_pass_rate": passing_scenarios / len(SCENARIOS),
            "risk_level_counts": risk_level_counts,
            "expected_risk_level_counts": expected_risk_level_counts,
            "latency_runs_per_scenario": runs,
        },
        "scenarios": scenario_results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = evaluate_synthetic_scenarios(args.runs)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
