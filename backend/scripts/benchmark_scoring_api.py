"""Measure end-to-end scoring route latency using an isolated SQLite database.

The ASGI benchmark includes request handling, admin authentication, model
scoring, and SQLite persistence. It does not include a network socket,
reverse proxy, or production deployment overhead.
"""

import argparse
import asyncio
import json
import math
import os
import platform
import secrets
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def benchmark_api_scoring(runs=100, warmup_runs=10):
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if warmup_runs < 0:
        raise ValueError("warmup_runs cannot be negative")

    from database import database

    original_database_path = database.DATABASE_PATH
    previous_admin_token = os.environ.get("MOMO_ADMIN_TOKEN")
    admin_token = secrets.token_hex(32)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = Path(temp_dir) / "benchmark.sqlite3"
            database.initialize_database()
            os.environ["MOMO_ADMIN_TOKEN"] = admin_token

            from app import app

            async def measure_requests():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://benchmark.local",
                    headers={"X-Admin-Token": admin_token},
                ) as client:
                    latencies_ms = []
                    total_requests = warmup_runs + runs
                    for index in range(total_requests):
                        transaction = {
                            "transaction_reference": f"API-BENCH-{index}",
                            "amount": 200.0,
                            "transaction_type": "TRANSFER",
                            "origin_account": "WALLET-1001",
                            "destination_account": "WALLET-2001",
                            "old_balance_origin": 10_000.0,
                            "new_balance_origin": 9_800.0,
                            "old_balance_destination": 10_000.0,
                            "new_balance_destination": 10_200.0,
                        }
                        started = time.perf_counter()
                        response = await client.post(
                            "/api/transactions/score",
                            json=transaction,
                        )
                        elapsed_ms = (time.perf_counter() - started) * 1000
                        if response.status_code != 200:
                            raise RuntimeError(
                                "Scoring benchmark request failed with "
                                f"HTTP {response.status_code}."
                            )
                        if index >= warmup_runs:
                            latencies_ms.append(elapsed_ms)
                    return latencies_ms

            latencies_ms = asyncio.run(measure_requests())
    finally:
        database.DATABASE_PATH = original_database_path
        if previous_admin_token is None:
            os.environ.pop("MOMO_ADMIN_TOKEN", None)
        else:
            os.environ["MOMO_ADMIN_TOKEN"] = previous_admin_token

    sorted_latencies = sorted(latencies_ms)
    p95_index = max(0, math.ceil(0.95 * len(sorted_latencies)) - 1)
    return {
        "benchmark_type": "in_process_asgi_scoring_route",
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "scope": (
            "Includes ASGI request handling, admin-token authentication, feature "
            "construction, hybrid model/rule scoring, and SQLite persistence. "
            "Excludes network sockets, reverse proxies, and deployment overhead."
        ),
        "database": "temporary isolated SQLite database",
        "warmup_runs": warmup_runs,
        "measured_runs": runs,
        "latency_unit": "milliseconds",
        "latency": {
            "mean": statistics.mean(sorted_latencies),
            "median": statistics.median(sorted_latencies),
            "p95": sorted_latencies[p95_index],
            "min": sorted_latencies[0],
            "max": sorted_latencies[-1],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--warmup-runs", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = benchmark_api_scoring(args.runs, args.warmup_runs)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
