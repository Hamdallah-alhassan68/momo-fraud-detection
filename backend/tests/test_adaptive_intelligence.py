import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import database.database as database
from services.adaptive_intelligence import (
    _psi,
    build_monitoring,
    retrain_or_update_model,
)
from services.input_validation import (
    is_valid_simulated_recipient,
    is_valid_wallet_reference,
    validate_ip_address,
)
from services.momo_simulator import initiate_transaction, _ensure_customer


class PopulationStabilityIndexTests(unittest.TestCase):
    def test_identical_distributions_have_zero_drift(self):
        values = [0, 0.5, 1, 1.5, 2] * 4
        self.assertEqual(_psi(values, values), 0.0)

    def test_separated_distributions_have_positive_drift(self):
        expected = [0, 1, 2, 3, 4] * 4
        actual = [100, 101, 102, 103, 104] * 4
        self.assertGreater(_psi(expected, actual), 0.25)

    def test_empty_distribution_is_not_reported_as_stable(self):
        self.assertIsNone(_psi([], [1, 2, 3]))


class SyntheticScenarioEvaluationTests(unittest.TestCase):
    def test_evaluation_reports_all_scenarios_and_latency_scope(self):
        from scripts.evaluate_synthetic_scenarios import (
            evaluate_synthetic_scenarios,
        )

        result = evaluate_synthetic_scenarios(runs=2)

        self.assertEqual(result["evaluation_type"], "synthetic_engineered_scenarios")
        self.assertEqual(result["summary"]["scenario_count"], 13)
        self.assertEqual(result["summary"]["passing_scenarios"], 13)
        self.assertEqual(result["summary"]["scenario_pass_rate"], 1.0)
        self.assertEqual(result["summary"]["latency_runs_per_scenario"], 2)
        self.assertIn("excludes HTTP", result["latency_scope"])
        scenarios_by_name = {
            scenario["scenario"]: scenario
            for scenario in result["scenarios"]
        }
        self.assertEqual(
            scenarios_by_name["COMPOUND_SECURITY_SIGNALS"]["risk_level"],
            "HIGH",
        )
        self.assertEqual(
            scenarios_by_name["HIGH_BALANCE_SHARE_ONLY"]["risk_level"],
            "LOW",
        )
        self.assertEqual(
            result["summary"]["expected_risk_level_counts"],
            {"LOW": 2, "MEDIUM": 10, "HIGH": 1},
        )
        for scenario in result["scenarios"]:
            self.assertGreaterEqual(scenario["mean_scoring_latency_ms"], 0)
            self.assertGreaterEqual(scenario["p95_scoring_latency_ms"], 0)
            self.assertEqual(scenario["latency_runs"], 2)
            self.assertEqual(
                scenario["risk_level"],
                scenario["expected_risk_level"],
            )
            self.assertTrue(scenario["outcome_matches_expectation"])

    def test_evaluation_rejects_non_positive_run_count(self):
        from scripts.evaluate_synthetic_scenarios import (
            evaluate_synthetic_scenarios,
        )

        with self.assertRaisesRegex(ValueError, "at least 1"):
            evaluate_synthetic_scenarios(runs=0)


class ApiScoringBenchmarkTests(unittest.TestCase):
    def test_benchmark_reports_end_to_end_latency_and_request_counts(self):
        import os

        from database import database
        from scripts.benchmark_scoring_api import benchmark_api_scoring

        original_database_path = database.DATABASE_PATH
        previous_admin_token = os.environ.get("MOMO_ADMIN_TOKEN")
        result = benchmark_api_scoring(runs=2, warmup_runs=1)

        self.assertEqual(result["benchmark_type"], "in_process_asgi_scoring_route")
        self.assertEqual(result["measured_runs"], 2)
        self.assertEqual(result["warmup_runs"], 1)
        self.assertIn("SQLite persistence", result["scope"])
        self.assertIn("Excludes network sockets", result["scope"])
        self.assertTrue(
            all(value >= 0 for value in result["latency"].values())
        )
        self.assertEqual(database.DATABASE_PATH, original_database_path)
        self.assertEqual(os.environ.get("MOMO_ADMIN_TOKEN"), previous_admin_token)

    def test_benchmark_rejects_invalid_run_counts(self):
        from scripts.benchmark_scoring_api import benchmark_api_scoring

        with self.assertRaisesRegex(ValueError, "runs must be at least 1"):
            benchmark_api_scoring(runs=0)
        with self.assertRaisesRegex(ValueError, "warmup_runs cannot be negative"):
            benchmark_api_scoring(runs=1, warmup_runs=-1)


class SimulatorInputValidationTests(unittest.TestCase):
    KNOWN_RECIPIENTS = {"WALLET-2001"}

    def test_accepts_demo_recipients_and_ghana_mobile_numbers(self):
        self.assertTrue(
            is_valid_simulated_recipient("WALLET-2001", self.KNOWN_RECIPIENTS)
        )
        self.assertTrue(
            is_valid_simulated_recipient("WALLET-3001", self.KNOWN_RECIPIENTS)
        )
        self.assertTrue(
            is_valid_simulated_recipient("0241234567", self.KNOWN_RECIPIENTS)
        )
        self.assertTrue(
            is_valid_simulated_recipient("+233241234567", self.KNOWN_RECIPIENTS)
        )
        self.assertTrue(
            is_valid_simulated_recipient("BANK-ACC-7002", self.KNOWN_RECIPIENTS)
        )

    def test_rejects_malformed_recipient(self):
        self.assertFalse(
            is_valid_simulated_recipient("recipient name", self.KNOWN_RECIPIENTS)
        )

    def test_accepts_and_normalizes_valid_ip_addresses(self):
        self.assertEqual(validate_ip_address("192.168.1.22"), "192.168.1.22")
        self.assertEqual(validate_ip_address("2001:db8::1"), "2001:db8::1")

    def test_rejects_invalid_ip_address_with_guidance(self):
        with self.assertRaisesRegex(ValueError, "valid IPv4 or IPv6"):
            validate_ip_address("not-an-ip")


class AppendOnlyAuditLogTests(unittest.TestCase):
    def setUp(self):
        self.original_database_path = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "test.sqlite3"
        database.initialize_database()

        connection = database.get_connection()
        connection.execute(
            """
            INSERT INTO audit_logs (action, performed_by, details, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            ("TEST_EVENT", "TEST", "original", "2026-01-01T00:00:00+00:00"),
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_update_is_rejected(self):
        connection = database.get_connection()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE audit_logs SET details = ? WHERE action = ?",
                ("changed", "TEST_EVENT"),
            )
        connection.close()

    def test_delete_is_rejected(self):
        connection = database.get_connection()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM audit_logs WHERE action = ?",
                ("TEST_EVENT",),
            )
        connection.close()


class AdminFraudCaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.original_database_path = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "admin-workflow.sqlite3"
        database.initialize_database()
        self.case_id = self._create_case()

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def _create_case(
        self,
        transaction_type="TRANSFER",
        risk_level="MEDIUM",
        wallet_ref="WALLET-1001",
    ):
        customer_ref = wallet_ref
        destination = "WALLET-2001"
        if transaction_type == "CASH_IN":
            customer_ref, destination = "BANK-ACC-7001", wallet_ref

        connection = database.get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO transactions (
                transaction_reference, amount, transaction_type,
                origin_account, destination_account, timestamp, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"TEST-CASE-{transaction_type}-{wallet_ref}",
                250.0,
                transaction_type,
                customer_ref,
                destination,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        transaction_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO risk_assessments (
                transaction_id, risk_score, risk_level, decision, reasons, created_at
            )
            VALUES (?, ?, ?, 'TEMPORARY HOLD / BLOCK', '["New recipient"]', ?)
            """,
            (
                transaction_id,
                0.9 if risk_level == "HIGH" else 0.6,
                risk_level,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        cursor.execute(
            """
            INSERT INTO fraud_alerts (transaction_id, severity, reason, created_at)
            VALUES (?, 'HIGH', '["New recipient"]', ?)
            """,
            (transaction_id, "2026-01-01T00:00:00+00:00"),
        )
        alert_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO fraud_cases (
                transaction_id, alert_id, case_status, created_at, updated_at
            )
            VALUES (?, ?, 'OPEN', ?, ?)
            """,
            (
                transaction_id,
                alert_id,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        case_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return case_id

    def _confirm_fraud(self):
        database.update_fraud_case(
            self.case_id,
            {
                "case_status": "INVESTIGATING",
                "analyst_decision": "FRAUD_CONFIRMED",
            },
        )

    def test_resolved_cases_are_removed_from_review_queue(self):
        self.assertEqual(len(database.get_fraud_cases()), 1)
        database.update_fraud_case(self.case_id, {"case_status": "RESOLVED"})
        self.assertEqual(database.get_fraud_cases(), [])
        investigated_cases = database.get_fraud_cases(include_resolved=True)
        self.assertEqual(len(investigated_cases), 1)
        self.assertEqual(investigated_cases[0]["case_status"], "RESOLVED")

    def test_freeze_requires_confirmed_fraud_and_consent_for_non_high_risk(self):
        contact = database.record_fraud_case_contact(
            self.case_id, "CALL", "CONFIRMED"
        )
        self.assertEqual(contact["contact_outcome"], "CONFIRMED")
        self.assertEqual(
            database.get_fraud_cases(include_resolved=True)[0]["case_status"],
            "INVESTIGATING",
        )

        with self.assertRaisesRegex(ValueError, "Fraud must be confirmed"):
            database.freeze_account_for_fraud_case(self.case_id)
        self._confirm_fraud()

        database.record_fraud_case_contact(self.case_id, "SMS", "DECLINED")
        with self.assertRaisesRegex(ValueError, "explicit consent"):
            database.freeze_account_for_fraud_case(self.case_id)

        database.record_fraud_case_contact(self.case_id, "CALL", "CONFIRMED")
        result = database.freeze_account_for_fraud_case(self.case_id)
        self.assertEqual(result["customer_ref"], "WALLET-1001")
        self.assertTrue(database.get_account_status("WALLET-1001")["frozen"])
        cases = database.get_fraud_cases(include_resolved=True)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["case_status"], "INVESTIGATING")
        self.assertTrue(cases[0]["account_frozen"])

    def test_high_risk_freeze_does_not_require_contact_consent(self):
        connection = database.get_connection()
        connection.execute(
            """
            UPDATE risk_assessments
            SET risk_score = 0.9, risk_level = 'HIGH'
            WHERE transaction_id = (
                SELECT transaction_id FROM fraud_cases WHERE id = ?
            )
            """,
            (self.case_id,),
        )
        connection.commit()
        connection.close()
        self._confirm_fraud()

        result = database.freeze_account_for_fraud_case(self.case_id)

        self.assertEqual(result["customer_ref"], "WALLET-1001")
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertIsNone(result["contact_outcome"])
        self.assertTrue(database.get_account_status("WALLET-1001")["frozen"])
        self.assertEqual(
            database.get_fraud_cases(include_resolved=True)[0]["case_status"],
            "INVESTIGATING",
        )

    def test_admin_can_freeze_and_unfreeze_legacy_w_wallet_reference(self):
        self.case_id = self._create_case(
            risk_level="HIGH",
            wallet_ref="W-7",
        )
        self._confirm_fraud()

        database.freeze_account_for_fraud_case(self.case_id)
        self.assertTrue(database.get_account_status("W-7")["frozen"])

        database.unfreeze_account_for_fraud_case(self.case_id)
        self.assertFalse(database.get_account_status("W-7")["frozen"])

    def test_freeze_is_scoped_to_the_case_wallet(self):
        other_case_id = self._create_case(
            risk_level="HIGH",
            wallet_ref="WALLET-2002",
        )
        self._confirm_fraud()
        database.record_fraud_case_contact(
            self.case_id, "CALL", "CONFIRMED"
        )
        database.freeze_account_for_fraud_case(self.case_id)

        cases = {
            case["case_id"]: case
            for case in database.get_fraud_cases(include_resolved=True)
        }
        self.assertTrue(cases[self.case_id]["account_frozen"])
        self.assertFalse(cases[other_case_id]["account_frozen"])
        self.assertFalse(database.get_account_status("WALLET-2002")["frozen"])

    def test_account_freeze_and_unfreeze_do_not_change_related_cases(self):
        other_case_id = self._create_case(
            transaction_type="CASH_IN",
            risk_level="HIGH",
        )
        self._confirm_fraud()
        database.record_fraud_case_contact(
            self.case_id, "CALL", "CONFIRMED"
        )

        database.freeze_account_for_fraud_case(self.case_id)

        cases = database.get_fraud_cases(include_resolved=True)
        self.assertEqual(len(cases), 2)
        self.assertEqual(
            {case["case_id"]: case["case_status"] for case in cases},
            {
                self.case_id: "INVESTIGATING",
                other_case_id: "OPEN",
            },
        )
        self.assertTrue(all(case["account_frozen"] for case in cases))

        database.unfreeze_account_for_fraud_case(other_case_id)

        cases = database.get_fraud_cases(include_resolved=True)
        self.assertEqual(
            {case["case_id"]: case["case_status"] for case in cases},
            {
                self.case_id: "INVESTIGATING",
                other_case_id: "OPEN",
            },
        )
        self.assertFalse(any(case["account_frozen"] for case in cases))

    def test_dashboard_stats_count_investigations_and_refreshable_metrics(self):
        self._confirm_fraud()
        database.record_fraud_case_contact(
            self.case_id, "CALL", "CONFIRMED"
        )
        database.freeze_account_for_fraud_case(self.case_id)

        dashboard_stats = database.get_dashboard_stats()
        analytics = database.get_analytics_data()

        self.assertEqual(dashboard_stats["open_cases"], 1)
        self.assertEqual(dashboard_stats["frozen_accounts"], 1)
        self.assertEqual(dashboard_stats["admin_frozen_accounts"], 1)
        self.assertEqual(dashboard_stats["customer_frozen_accounts"], 0)
        self.assertEqual(analytics["totals"]["open_cases"], 1)
        self.assertEqual(analytics["totals"]["frozen_accounts"], 1)
        self.assertEqual(analytics["totals"]["admin_frozen_accounts"], 1)
        self.assertEqual(analytics["totals"]["customer_frozen_accounts"], 0)
        self.assertEqual(analytics["totals"]["avg_risk_score"], 0.6)

        database.set_account_frozen("WALLET-1002", "FROZEN_BY_CUSTOMER")
        categorized_freezes = database.get_analytics_data()["totals"]
        self.assertEqual(categorized_freezes["frozen_accounts"], 2)
        self.assertEqual(categorized_freezes["admin_frozen_accounts"], 1)
        self.assertEqual(categorized_freezes["customer_frozen_accounts"], 1)

        database.update_fraud_case(
            self.case_id,
            {"case_status": "RESOLVED"},
        )
        resolved_stats = database.get_analytics_data()["totals"]
        self.assertEqual(resolved_stats["open_cases"], 0)
        self.assertEqual(resolved_stats["resolved_cases"], 1)

    def test_cash_in_case_freezes_the_wallet_destination(self):
        self.case_id = self._create_case(
            transaction_type="CASH_IN", risk_level="HIGH"
        )
        self._confirm_fraud()
        database.record_fraud_case_contact(self.case_id, "CALL", "CONFIRMED")
        result = database.freeze_account_for_fraud_case(self.case_id)
        self.assertEqual(result["customer_ref"], "WALLET-1001")


class CustomerBehaviorProfileTests(unittest.TestCase):
    def setUp(self):
        self.original_database_path = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "profiles.sqlite3"
        database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def _insert(self, reference, destination, timestamp, amount=20):
        database.create_transaction(
            {
                "transaction_reference": reference,
                "amount": amount,
                "transaction_type": "TRANSFER",
                "origin_account": "WALLET-1001",
                "destination_account": destination,
                "timestamp": timestamp,
            }
        )

    def test_recipient_history_is_specific_to_customer(self):
        self._insert("PROFILE-1", "WALLET-2001", "2026-04-02T12:00:00+00:00")
        self._insert("PROFILE-2", "WALLET-2001", "2026-04-02T13:00:00+00:00")
        self.assertEqual(
            database.get_customer_recipient_history("WALLET-1001", "WALLET-2001"),
            2,
        )
        self.assertEqual(
            database.get_customer_recipient_history("WALLET-1002", "WALLET-2001"),
            0,
        )

    def test_baseline_contains_amount_hour_frequency_and_recipient_data(self):
        self._insert("PROFILE-1", "WALLET-2001", "2026-04-02T12:00:00+00:00", 20)
        self._insert("PROFILE-2", "WALLET-2001", "2026-04-03T12:00:00+00:00", 40)
        profile = database.get_customer_baseline("WALLET-1001")
        self.assertEqual(profile["count"], 2)
        self.assertEqual(profile["avg_amount"], 30)
        self.assertEqual(profile["usual_hours"], [12])
        self.assertEqual(profile["recipient_counts"], {"WALLET-2001": 2})
        self.assertEqual(profile["avg_transactions_per_day"], 1)

    def test_baseline_normalizes_timestamps_to_ghana_time(self):
        self._insert(
            "PROFILE-OFFSET-1",
            "WALLET-2001",
            "2026-04-01T23:30:00-02:00",
            20,
        )
        self._insert(
            "PROFILE-OFFSET-2",
            "WALLET-2001",
            "2026-04-02T10:00:00+02:00",
            40,
        )

        profile = database.get_customer_baseline("WALLET-1001")

        self.assertEqual(profile["usual_hours"], [1, 8])
        self.assertEqual(
            profile["transactions_per_day"],
            {"2026-04-02": 2},
        )

    def test_simulator_persists_explicit_scenario_and_rejects_unknown_values(self):
        from services import momo_simulator

        session = momo_simulator.initiate_transaction(
            customer_ref="WALLET-1001",
            transaction_type="TRANSFER",
            amount=25,
            recipient="WALLET-2001",
            scenario="RECENT_SIM_SWAP",
        )
        stored = database.get_momo_session(session["session_id"])
        self.assertEqual(stored["scenario"], "RECENT_SIM_SWAP")
        try:
            result = momo_simulator.verify_transaction(session["session_id"], "2233")
            self.assertTrue(result["signals"]["recent_sim_swap"])
        finally:
            momo_simulator.FROZEN_UNTIL.pop("WALLET-1001", None)
            momo_simulator._rebuild_balances()

        with self.assertRaisesRegex(ValueError, "Unsupported simulation scenario"):
            momo_simulator.initiate_transaction(
                customer_ref="WALLET-1001",
                transaction_type="TRANSFER",
                amount=25,
                recipient="WALLET-2001",
                scenario="UNSUPPORTED",
            )

    def test_simulator_accepts_multiple_scenarios(self):
        from services import momo_simulator

        session = momo_simulator.initiate_transaction(
            customer_ref="WALLET-1001",
            transaction_type="TRANSFER",
            amount=25,
            recipient="WALLET-2001",
            scenario=["DEVICE_CHANGE", "IP_MISMATCH", "RECENT_SIM_SWAP"],
        )

        # Stored in a deterministic order and exposed as a list.
        stored = database.get_momo_session(session["session_id"])
        self.assertEqual(
            stored["scenario"], "RECENT_SIM_SWAP,DEVICE_CHANGE,IP_MISMATCH"
        )
        self.assertEqual(
            session["scenarios"],
            ["RECENT_SIM_SWAP", "DEVICE_CHANGE", "IP_MISMATCH"],
        )

        try:
            result = momo_simulator.verify_transaction(session["session_id"], "2233")
            self.assertTrue(result["signals"]["recent_sim_swap"])
            self.assertTrue(result["signals"]["device_change"])
            self.assertTrue(result["signals"]["ip_network_mismatch"])
            self.assertEqual(
                result["signals"]["scenarios"],
                ["RECENT_SIM_SWAP", "DEVICE_CHANGE", "IP_MISMATCH"],
            )
        finally:
            momo_simulator.FROZEN_UNTIL.pop("WALLET-1001", None)
            momo_simulator._rebuild_balances()

    def test_simulator_supports_custom_wallet_reference(self):
        from services import momo_simulator

        custom_ref = "WALLET-9876"
        try:
            session = momo_simulator.initiate_transaction(
                customer_ref="wallet-9876",  # normalised to upper case
                transaction_type="TRANSFER",
                amount=25,
                recipient="WALLET-2001",
            )
            self.assertEqual(session["customer_ref"], custom_ref)
            self.assertEqual(session["scenario"], "NORMAL")

            registered = next(
                (c for c in momo_simulator.CUSTOMERS if c["ref"] == custom_ref),
                None,
            )
            self.assertIsNotNone(registered)
            self.assertTrue(registered["custom"])
            self.assertIn(custom_ref, momo_simulator.BALANCES)
        finally:
            momo_simulator.CUSTOMERS[:] = [
                c for c in momo_simulator.CUSTOMERS if c["ref"] != custom_ref
            ]
            momo_simulator.BALANCES.pop(custom_ref, None)
            momo_simulator.FROZEN_UNTIL.pop(custom_ref, None)
            momo_simulator._rebuild_balances()

    def test_simulator_rejects_invalid_custom_wallet_reference(self):
        from services import momo_simulator

        with self.assertRaisesRegex(ValueError, "demo wallet"):
            momo_simulator.initiate_transaction(
                customer_ref="not a wallet",
                transaction_type="TRANSFER",
                amount=25,
                recipient="WALLET-2001",
            )


class MediumRiskEmailStepUpTests(unittest.TestCase):
    def setUp(self):
        self.original_database_path = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "email-step-up.sqlite3"
        database.initialize_database()
        from services import momo_simulator

        self.simulator = momo_simulator
        self.original_balances = dict(momo_simulator.BALANCES)
        self.original_frozen_until = dict(momo_simulator.FROZEN_UNTIL)
        momo_simulator.FROZEN_UNTIL.clear()
        self.original_email_verified = momo_simulator.CUSTOMERS[0][
            "email_verified"
        ]

    def tearDown(self):
        self.simulator.CUSTOMERS[0]["email_verified"] = (
            self.original_email_verified
        )
        self.simulator.BALANCES.clear()
        self.simulator.BALANCES.update(self.original_balances)
        self.simulator.FROZEN_UNTIL.clear()
        self.simulator.FROZEN_UNTIL.update(self.original_frozen_until)
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_medium_risk_uses_simulated_email_not_sms(self):
        session = self.simulator.initiate_transaction(
            customer_ref="WALLET-1001",
            transaction_type="TRANSFER",
            amount=25,
            recipient="WALLET-2001",
            scenario="RECENT_SIM_SWAP",
        )
        challenge = self.simulator.verify_transaction(
            session["session_id"], "2233"
        )

        self.assertEqual(challenge["status"], "STEP_UP_REQUIRED")
        self.assertEqual(challenge["verification_method"], "EMAIL")
        self.assertEqual(
            challenge["verification_destination"], "k***h@example.test"
        )
        self.assertEqual(challenge["delivery_status"], "SIMULATED_NOT_SENT")
        self.assertIn("Recent SIM change", challenge["reasons"])
        self.assertNotIn("otp", challenge)

        result = self.simulator.verify_transaction(
            session["session_id"], challenge["demo_hint"], step_up=True
        )
        self.assertEqual(result["status"], "APPROVED")
        self.assertTrue(result["step_up_completed"])

    def test_medium_risk_fails_closed_without_verified_email(self):
        self.simulator.CUSTOMERS[0]["email_verified"] = False
        session = self.simulator.initiate_transaction(
            customer_ref="WALLET-1001",
            transaction_type="TRANSFER",
            amount=25,
            recipient="WALLET-2001",
            scenario="RECENT_SIM_SWAP",
        )

        result = self.simulator.verify_transaction(
            session["session_id"], "2233"
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["approved"])
        self.assertIn("no verified email", result["message"])
        self.assertNotIn("demo_hint", result)


class FastApiAdminAuthenticationTests(unittest.TestCase):
    def test_admin_token_is_required_and_compared(self):
        from fastapi import HTTPException
        from app import app, authenticate_admin, require_admin
        import asyncio
        import httpx

        import os

        previous = os.environ.get("MOMO_ADMIN_TOKEN")
        os.environ["MOMO_ADMIN_TOKEN"] = "x" * 40
        try:
            self.assertTrue(authenticate_admin({"token": "x" * 40})["authenticated"])
            with self.assertRaises(HTTPException) as invalid:
                authenticate_admin({"token": "wrong"})
            self.assertEqual(invalid.exception.status_code, 401)
            with self.assertRaises(HTTPException) as missing:
                require_admin(None)
            self.assertEqual(missing.exception.status_code, 401)
            require_admin("x" * 40)

            async def check_http_routes():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    self.assertEqual(
                        (
                            await client.get("/api/admin/dashboard-stats")
                        ).status_code,
                        401,
                    )
                    self.assertEqual(
                        (
                            await client.post(
                                "/api/admin/auth",
                                json={"token": "x" * 40},
                            )
                        ).status_code,
                        200,
                    )
                    authorized = await client.get(
                        "/api/admin/dashboard-stats",
                        headers={"X-Admin-Token": "x" * 40},
                    )
                    self.assertEqual(authorized.status_code, 200)

            asyncio.run(check_http_routes())
        finally:
            if previous is None:
                os.environ.pop("MOMO_ADMIN_TOKEN", None)
            else:
                os.environ["MOMO_ADMIN_TOKEN"] = previous


class FastApiAdminAccountControlTests(unittest.TestCase):
    def test_admin_can_freeze_without_a_case_and_counts_refresh(self):
        import asyncio
        import httpx
        import os
        from app import app

        previous_database_path = database.DATABASE_PATH
        previous_admin_token = os.environ.get("MOMO_ADMIN_TOKEN")
        temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(temp_dir.name) / "admin-freeze.sqlite3"
        database.initialize_database()
        transaction_id = database.create_transaction(
            {
                "transaction_reference": "ADMIN-CASE-WALLET",
                "amount": 100,
                "transaction_type": "TRANSFER",
                "origin_account": "W-7",
                "destination_account": "WALLET-1002",
                "timestamp": "2026-10-12T10:00:00+00:00",
            }
        )
        database.create_fraud_case(transaction_id)
        os.environ["MOMO_ADMIN_TOKEN"] = "admin-freeze-test-token-12345678"

        async def check_freeze_flow():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                headers = {"X-Admin-Token": os.environ["MOMO_ADMIN_TOKEN"]}
                frozen = await client.post(
                    "/api/admin/accounts/W-7/freeze",
                    headers=headers,
                )
                self.assertEqual(frozen.status_code, 200)
                self.assertEqual(frozen.json()["customer_ref"], "W-7")

                accounts = await client.get("/api/admin/accounts", headers=headers)
                self.assertEqual(accounts.status_code, 200)
                account = next(
                    item
                    for item in accounts.json()["accounts"]
                    if item["customer_ref"] == "W-7"
                )
                self.assertTrue(account["account_frozen"])
                self.assertEqual(account["reason"], "ADMIN_MANUAL")

                analytics = await client.get(
                    "/api/admin/analytics",
                    headers=headers,
                )
                self.assertEqual(analytics.status_code, 200)
                totals = analytics.json()["totals"]
                self.assertEqual(totals["frozen_accounts"], 1)
                self.assertEqual(totals["admin_frozen_accounts"], 1)
                self.assertEqual(totals["customer_frozen_accounts"], 0)

                unfrozen = await client.post(
                    "/api/admin/accounts/W-7/unfreeze",
                    headers=headers,
                )
                self.assertEqual(unfrozen.status_code, 200)
                updated_analytics = await client.get(
                    "/api/admin/analytics",
                    headers=headers,
                )
                updated_totals = updated_analytics.json()["totals"]
                self.assertEqual(updated_totals["frozen_accounts"], 0)
                self.assertEqual(updated_totals["admin_frozen_accounts"], 0)

        try:
            asyncio.run(check_freeze_flow())
        finally:
            database.DATABASE_PATH = previous_database_path
            temp_dir.cleanup()
            if previous_admin_token is None:
                os.environ.pop("MOMO_ADMIN_TOKEN", None)
            else:
                os.environ["MOMO_ADMIN_TOKEN"] = previous_admin_token

    def test_admin_can_list_and_unfreeze_customer_frozen_wallet(self):
        import asyncio
        import httpx
        import os
        from app import app

        previous_database_path = database.DATABASE_PATH
        previous_admin_token = os.environ.get("MOMO_ADMIN_TOKEN")
        temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(temp_dir.name) / "admin-accounts.sqlite3"
        database.initialize_database()
        database.set_account_frozen("WALLET-1001", "FROZEN_BY_CUSTOMER")
        os.environ["MOMO_ADMIN_TOKEN"] = "account-control-test-token-123456"

        async def check_account_routes():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                headers = {"X-Admin-Token": os.environ["MOMO_ADMIN_TOKEN"]}
                frozen = await client.get(
                    "/api/admin/accounts/frozen",
                    headers=headers,
                )
                self.assertEqual(frozen.status_code, 200)
                self.assertEqual(
                    frozen.json()["accounts"],
                    [
                        {
                            "customer_ref": "WALLET-1001",
                            "name": "Kwame Mensah",
                            "reason": "FROZEN_BY_CUSTOMER",
                        }
                    ],
                )

                all_accounts = await client.get(
                    "/api/admin/accounts",
                    headers=headers,
                )
                self.assertEqual(all_accounts.status_code, 200)
                customer_account = next(
                    item
                    for item in all_accounts.json()["accounts"]
                    if item["customer_ref"] == "WALLET-1001"
                )
                self.assertTrue(customer_account["account_frozen"])

                unfrozen = await client.post(
                    "/api/admin/accounts/WALLET-1001/unfreeze",
                    headers=headers,
                )
                self.assertEqual(unfrozen.status_code, 200)
                self.assertFalse(
                    database.get_account_status("WALLET-1001")["frozen"]
                )

                empty = await client.get(
                    "/api/admin/accounts/frozen",
                    headers=headers,
                )
                self.assertEqual(empty.json()["total"], 0)

        try:
            asyncio.run(check_account_routes())
        finally:
            database.DATABASE_PATH = previous_database_path
            temp_dir.cleanup()
            if previous_admin_token is None:
                os.environ.pop("MOMO_ADMIN_TOKEN", None)
            else:
                os.environ["MOMO_ADMIN_TOKEN"] = previous_admin_token


class FastApiModelRetrainEndpointTests(unittest.TestCase):
    def test_admin_model_retrain_endpoint_via_http(self):
        import asyncio
        import httpx
        import os
        from app import app

        previous_database_path = database.DATABASE_PATH
        previous_admin_token = os.environ.get("MOMO_ADMIN_TOKEN")
        temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(temp_dir.name) / "model-retrain.sqlite3"
        database.initialize_database()
        os.environ["MOMO_ADMIN_TOKEN"] = "model-retrain-endpoint-token-123456"

        async def check_retrain_endpoint():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                headers = {"X-Admin-Token": os.environ["MOMO_ADMIN_TOKEN"]}

                # Rejected without a valid admin token
                denied = await client.post("/api/admin/model/retrain")
                self.assertEqual(denied.status_code, 401)

                # Admin can record a prototype baseline marker over HTTP.
                response = await client.post(
                    "/api/admin/model/retrain",
                    headers=headers,
                    json={"triggered_by": "dashboard"},
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "SUCCESS")
                self.assertTrue(payload["version"].startswith("V3."))
                self.assertIn("ADAPTIVE", payload["version"])
                self.assertIsInstance(payload["trained_on_samples"], int)
                self.assertEqual(payload["operation"], "PROTOTYPE_BASELINE_MARKER")
                self.assertFalse(payload["model_refit"])
                self.assertFalse(payload["psi_baseline_reset"])
                self.assertEqual(
                    payload["tracked_transactions"],
                    payload["trained_on_samples"],
                )
                self.assertIn("detector was not refit", payload["message"])

                # A version record is persisted without claiming model metrics.
                latest = database.get_latest_model_version()
                self.assertEqual(latest["version"], payload["version"])
                self.assertEqual(latest["status"], "ACTIVE")
                self.assertEqual(latest["model_type"], "PrototypeBaselineMarker")
                for metric in (
                    "roc_auc",
                    "pr_auc",
                    "precision_score",
                    "recall_score",
                    "f1_score",
                ):
                    self.assertIsNone(latest[metric], metric)

                # Audit entry logged with operator attribution
                audit_logs = database.get_audit_logs()
                retrain_entries = [
                    log
                    for log in audit_logs
                    if log["action"] == "MODEL_BASELINE_MARKER_RECORDED"
                ]
                self.assertGreater(len(retrain_entries), 0)
                self.assertIn("admin_master", retrain_entries[0]["performed_by"])
                self.assertIn("detector was not refit", retrain_entries[0]["details"])

                # Monitoring exposes the automated baseline-marker thresholds.
                monitoring = await client.get(
                    "/api/admin/monitoring", headers=headers
                )
                self.assertEqual(monitoring.status_code, 200)
                trigger = monitoring.json()["automated_trigger"]
                self.assertEqual(trigger["psi_threshold"], 0.25)
                self.assertEqual(trigger["volume_threshold"], 50)
                self.assertIn(
                    trigger["recommended_action"],
                    ("RECORD_PROTOTYPE_BASELINE_MARKER", "MONITOR_STABLE"),
                )

        try:
            asyncio.run(check_retrain_endpoint())
        finally:
            database.DATABASE_PATH = previous_database_path
            temp_dir.cleanup()
            if previous_admin_token is None:
                os.environ.pop("MOMO_ADMIN_TOKEN", None)
            else:
                os.environ["MOMO_ADMIN_TOKEN"] = previous_admin_token

    def test_model_retrain_endpoint_rejects_non_admin_users(self):
        import asyncio
        import httpx
        from app import app
        from services.auth import create_access_token

        async def check_retrain_authorization():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                for role in ("analyst", "viewer"):
                    token = create_access_token(
                        user_id=1,
                        username=f"test_{role}",
                        role=role,
                    )
                    response = await client.post(
                        "/api/admin/model/retrain",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    self.assertEqual(response.status_code, 403, role)

        asyncio.run(check_retrain_authorization())


class PaySimTrainingPipelineTests(unittest.TestCase):
    def test_sampled_dataset_read_is_deterministic_and_rejects_zero_size(self):
        from scripts.train_evaluate import prepare_dataset

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "synthetic-paysim-format.csv"
            rows = []
            for index in range(30):
                transaction_type = (
                    "PAYMENT"
                    if index < 10
                    else "TRANSFER"
                    if index % 2 == 0
                    else "CASH_OUT"
                )
                rows.append(
                    {
                        "step": index,
                        "type": transaction_type,
                        "amount": 9000 if index % 6 == 0 else 40 + index,
                        "oldbalanceOrg": 10000,
                        "newbalanceOrig": 0 if index % 6 == 0 else 9960 - index,
                        "oldbalanceDest": 100,
                        "newbalanceDest": 0 if index % 6 == 0 else 140 + index,
                        "nameDest": f"DEST-{index % 5}",
                        "isFraud": int(index % 6 == 0),
                    }
                )
            pd.DataFrame(rows).to_csv(csv_path, index=False)

            first_matrix, first_labels = prepare_dataset(
                csv_path, sample_size=12, seed=7
            )
            second_matrix, second_labels = prepare_dataset(
                csv_path, sample_size=12, seed=7
            )

            pd.testing.assert_frame_equal(first_matrix, second_matrix)
            self.assertEqual(first_labels.tolist(), second_labels.tolist())
            self.assertEqual(len(first_matrix), 12)
            self.assertEqual(len(first_labels), 12)
            with self.assertRaisesRegex(ValueError, "at least 1"):
                prepare_dataset(csv_path, sample_size=0, seed=7)
            with self.assertRaisesRegex(ValueError, "eligible transaction count 20"):
                prepare_dataset(csv_path, sample_size=21, seed=7)

    def test_temporal_split_keeps_steps_separate_and_history_causal(self):
        from scripts.train_evaluate import (
            _prepare_dataset,
            _temporal_split_indices,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "temporal-paysim-format.csv"
            rows = []
            for step in range(1, 11):
                for offset in range(12):
                    fraud = int(offset == 0)
                    rows.append(
                        {
                            "step": step,
                            "type": "TRANSFER",
                            "amount": 9000 if fraud else 40 + offset,
                            "oldbalanceOrg": 10000,
                            "newbalanceOrig": 0 if fraud else 9960 - offset,
                            "oldbalanceDest": 100,
                            "newbalanceDest": 0 if fraud else 140 + offset,
                            "nameDest": "SHARED-DEST",
                            "isFraud": fraud,
                        }
                    )
            pd.DataFrame(rows).to_csv(csv_path, index=False)

            matrix, labels, steps, eligible_count = _prepare_dataset(csv_path)
            self.assertEqual(eligible_count, 120)
            self.assertEqual(matrix["dest_previous_count"].iloc[:12].tolist(), [0] * 12)
            self.assertEqual(
                matrix["dest_previous_count"].iloc[12:24].tolist(), [12] * 12
            )

            train_idx, validation_idx, test_idx = _temporal_split_indices(steps)
            self.assertLess(steps[train_idx].max(), steps[validation_idx].min())
            self.assertLess(
                steps[validation_idx].max(), steps[test_idx].min()
            )
            for indices in (train_idx, validation_idx, test_idx):
                self.assertEqual(len(np.unique(labels[indices])), 2)

    def test_wilson_intervals_bound_small_sample_metrics(self):
        from scripts.train_evaluate import _wilson_interval

        low, high = _wilson_interval(50, 100)
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)
        self.assertEqual(_wilson_interval(0, 0), [None, None])

    def test_hybrid_rule_score_and_seed_summary(self):
        from scripts.evaluate_hybrid_vs_model_only import (
            _calculate_rule_scores,
            _combine_hybrid_scores,
            _summarize_runs,
        )

        features = pd.DataFrame(
            [
                {
                    "oldbalanceOrg": 100,
                    "newbalanceOrig": 0,
                    "newbalanceDest": 0,
                    "recipient_novel": 1,
                    "amount_to_origin_balance": 1.0,
                    "amount": 200,
                },
                {
                    "oldbalanceOrg": 100,
                    "newbalanceOrig": 50,
                    "newbalanceDest": 100,
                    "recipient_novel": 0,
                    "amount_to_origin_balance": 0.5,
                    "amount": 50,
                },
            ]
        )
        rule_scores = _calculate_rule_scores(features, amount_threshold=1000)
        np.testing.assert_allclose(rule_scores, [0.55, 0.0])

        config = {
            "if_weight": 0.4,
            "rule_weight": 0.6,
            "low_threshold": 0.3,
        }
        hybrid_scores = _combine_hybrid_scores(
            np.array([0.0, 0.2]),
            np.array([0.0, 0.0]),
            np.array([True, False]),
            config,
        )
        np.testing.assert_allclose(hybrid_scores, [0.3, 0.08])

        run = {
            "test_comparison": {
                name: {
                    **{
                        key: 0.5
                        for key in (
                            "precision",
                            "recall",
                            "f1",
                            "f2",
                            "false_positive_rate",
                            "flagged_count",
                            "roc_auc",
                            "pr_auc",
                        )
                    },
                    "by_transaction_type": {
                        transaction_type: {
                            "rows": 10,
                            "fraud_labels": 1,
                            **{
                                key: 0.5
                                for key in (
                                    "precision",
                                    "recall",
                                    "f1",
                                    "f2",
                                    "false_positive_rate",
                                    "flagged_count",
                                    "roc_auc",
                                    "pr_auc",
                                )
                            },
                        }
                        for transaction_type in ("TRANSFER", "CASH_OUT")
                    },
                }
                for name in (
                    "model_only_validation_selected_threshold",
                    "model_only_same_numeric_cutoff_0_3",
                    "model_only_same_numeric_cutoff_0_5",
                    "hybrid_validation_selected_threshold",
                    "hybrid_medium_or_higher_cutoff",
                    "hybrid_high_decision_cutoff",
                )
            }
        }
        summary = _summarize_runs([run])
        self.assertEqual(
            summary["hybrid_high_decision_cutoff"]["overall"][
                "recall"
            ]["mean"],
            0.5,
        )
        self.assertIn(
            "CASH_OUT",
            summary["hybrid_high_decision_cutoff"]["by_transaction_type"],
        )

    def test_training_exports_metrics_and_runtime_artifacts(self):
        from scripts.train_evaluate import train_and_evaluate
        from scripts.evaluate_hybrid_vs_model_only import evaluate_run

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / "synthetic-paysim-format.csv"
            output_dir = root / "model-run"
            rows = []
            for index in range(120):
                fraud = int(index % 12 == 0)
                rows.append(
                    {
                        "step": index,
                        "type": "TRANSFER" if index % 2 == 0 else "CASH_OUT",
                        "amount": 9000 if fraud else 40 + index,
                        "oldbalanceOrg": 10000,
                        "newbalanceOrig": 0 if fraud else 9960 - index,
                        "oldbalanceDest": 100,
                        "newbalanceDest": 0 if fraud else 140 + index,
                        "nameDest": f"DEST-{index % 15}",
                        "isFraud": fraud,
                    }
                )
            pd.DataFrame(rows).to_csv(csv_path, index=False)
            metrics = train_and_evaluate(csv_path, output_dir, seed=17)
            self.assertEqual(metrics["seed"], 17)
            self.assertEqual(
                metrics["split"],
                {
                    "train": 0.60,
                    "validation": 0.20,
                    "test": 0.20,
                    "type": "chronological",
                    "ordered_by": "step",
                    "same_step_kept_together": True,
                },
            )
            split_counts = metrics["split_counts"]
            self.assertLess(
                split_counts["train"]["step_max"],
                split_counts["validation"]["step_min"],
            )
            self.assertLess(
                split_counts["validation"]["step_max"],
                split_counts["test"]["step_min"],
            )
            self.assertGreater(
                split_counts["test"]["fraud_labels"], 0
            )
            self.assertFalse(metrics["runtime_model_modified"])
            threshold_selection = metrics["threshold_selection"]
            self.assertIn("F2", threshold_selection["policy"])
            self.assertIn(
                "not changed",
                threshold_selection["operational_interpretation"],
            )
            self.assertEqual(
                len(threshold_selection["validation_candidates"]), 5
            )
            self.assertEqual(
                metrics["threshold"],
                threshold_selection["selected_threshold"],
            )
            self.assertEqual(metrics["f2"], threshold_selection[
                "selected_holdout_metrics"
            ]["f2"])
            self.assertEqual(
                len(threshold_selection["selected_holdout_metrics"]["precision_ci_95"]),
                2,
            )
            self.assertEqual(metrics["sampled_eligible_rows"], 120)
            self.assertGreaterEqual(metrics["roc_auc"], 0)
            self.assertGreater(metrics["p95_model_latency_ms_per_transaction"], 0)
            self.assertTrue((output_dir / "isolation_forest_model.joblib").exists())
            self.assertTrue((output_dir / "robust_scaler.joblib").exists())
            self.assertTrue((output_dir / "train_medians.joblib").exists())
            self.assertTrue((output_dir / "evaluation.json").exists())
            comparison = evaluate_run(
                csv_path, output_dir, sample_size=120, seed=17
            )
            self.assertEqual(comparison["sampled_eligible_rows"], 120)
            self.assertEqual(
                comparison["split_counts"]["test"]["rows"],
                24,
            )
            self.assertFalse(comparison.get("runtime_model_modified", False))
            self.assertIn(
                "TRANSFER",
                comparison["test_comparison"][
                    "hybrid_validation_selected_threshold"
                ]["by_transaction_type"],
            )


class CustomInputsAndRetrainingTests(unittest.TestCase):
    def setUp(self):
        # Isolate from the real development database so retraining tests never
        # pollute fraud.db or become order-dependent on accumulated versions.
        self.original_database_path = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "custom-inputs.sqlite3"
        database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_admin_model_retrain_lifecycle(self):
        res = retrain_or_update_model(triggered_by="TEST_ADMIN")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertTrue(res["version"].startswith("V3."))
        self.assertIn("ADAPTIVE", res["version"])
        self.assertIsInstance(res["trained_on_samples"], int)
        self.assertGreaterEqual(res["trained_on_samples"], 0)

        latest = database.get_latest_model_version()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["version"], res["version"])
        self.assertEqual(latest["status"], "ACTIVE")
        self.assertEqual(latest["samples_count"], res["trained_on_samples"])

        # Verify audit entry was logged
        audit_logs = database.get_audit_logs()
        retrain_entries = [
            log for log in audit_logs
            if log["action"] == "MODEL_BASELINE_MARKER_RECORDED"
        ]
        self.assertGreater(len(retrain_entries), 0)
        self.assertIn("TEST_ADMIN", retrain_entries[0]["performed_by"])
        self.assertIn("PSI baseline was not reset", retrain_entries[0]["details"])
        self.assertFalse(res["model_refit"])
        self.assertFalse(res["psi_baseline_reset"])
        for metric in (
            "roc_auc",
            "pr_auc",
            "precision_score",
            "recall_score",
            "f1_score",
        ):
            self.assertIsNone(latest[metric], metric)

    def test_retrain_rollover_increments_beyond_v3_major(self):
        # Seed an active V4.x version, as repeated retrains eventually produce.
        database.record_model_version(
            version="V4.0-ADAPTIVE",
            model_type="IsolationForest+RuleHybrid",
            roc_auc=0.98,
            pr_auc=0.89,
            precision_score=0.92,
            recall_score=0.88,
            f1_score=0.90,
            status="ACTIVE",
            samples_count=120,
        )
        result = retrain_or_update_model(triggered_by="TEST_ADMIN")
        self.assertEqual(result["status"], "SUCCESS")
        # Must roll forward to V4.1-ADAPTIVE, not reset to V3.1-ADAPTIVE
        # (a reset would violate the model_versions.version unique constraint).
        self.assertTrue(result["version"].startswith("V4."))
        self.assertNotEqual(result["version"], "V3.1-ADAPTIVE")

    def test_custom_ghana_phone_number_formats_and_custom_wallets(self):
        known = ["DEMO-RECIPIENT-01", "MERCHANT-01"]

        # Valid Ghana mobile numbers with 024, 055, 020, 027
        valid_ghana_numbers = [
            "0241234567",       # MTN 024
            "0559876543",       # MTN 055
            "0205551234",       # Telecel 020
            "0271122334",       # AT 027
            "+233241234567",    # E.164 +233 24
            "+233559876543",    # E.164 +233 55
            "024 123 4567",     # Spaced formatting
            "055-987-6543",     # Hyphen formatting
            "(024) 123-4567",   # Parenthesized formatting
        ]
        for num in valid_ghana_numbers:
            self.assertTrue(
                is_valid_simulated_recipient(num, known),
                f"Expected {num} to be a valid recipient",
            )
            self.assertTrue(
                is_valid_wallet_reference(num),
                f"Expected {num} to be a valid wallet reference",
            )

        # Structured & alphanumeric custom wallet IDs
        valid_custom_wallets = [
            "WALLET-CUSTOM-99",
            "MOMO-USER-100",
            "USER_4567",
            "CUSTOM-ACC-01",
        ]
        for wallet in valid_custom_wallets:
            self.assertTrue(
                is_valid_wallet_reference(wallet),
                f"Expected {wallet} to be a valid custom wallet",
            )

        # Invalid formats
        invalid_recipients = [
            "",
            "   ",
            "123",
            "+14155552671",     # Foreign US number
            "NOT A VALID WALLET NAME AT ALL",
        ]
        for invalid in invalid_recipients:
            self.assertFalse(
                is_valid_simulated_recipient(invalid, known),
                f"Expected {invalid!r} to be rejected",
            )

    def test_custom_ghana_phone_inputs_normalize_024_055_and_plus233(self):
        from services import momo_simulator

        inputs = {
            "024 123 4567": "0241234567",
            "055-987-6543": "0559876543",
            "+233 24 123 4567": "+233241234567",
        }
        starting_refs = {customer["ref"] for customer in momo_simulator.CUSTOMERS}
        created_refs = set()
        try:
            for input_value, expected_ref in inputs.items():
                with self.subTest(input_value=input_value):
                    customer = _ensure_customer(input_value)
                    self.assertEqual(customer["ref"], expected_ref)
                    if expected_ref not in starting_refs:
                        created_refs.add(expected_ref)
        finally:
            momo_simulator.CUSTOMERS[:] = [
                customer
                for customer in momo_simulator.CUSTOMERS
                if customer["ref"] in starting_refs
            ]
            for expected_ref in created_refs:
                momo_simulator.BALANCES.pop(expected_ref, None)

    def _build_monitoring_with_mocks(
        self,
        *,
        tracked,
        trained_samples,
        samples=None,
        psi_value=None,
    ):
        latest_model = {
            "version": "V3.0-HYBRID",
            "samples_count": trained_samples,
        }
        with (
            patch(
                "services.adaptive_intelligence.get_daily_transaction_stats",
                return_value=[],
            ),
            patch(
                "services.adaptive_intelligence.get_recent_transaction_samples",
                return_value=samples or [],
            ),
            patch(
                "services.adaptive_intelligence.get_analyst_decision_counts",
                return_value={},
            ),
            patch(
                "services.adaptive_intelligence.record_model_monitoring_snapshot"
            ),
            patch(
                "services.adaptive_intelligence.model_drift_logged_today",
                return_value=True,
            ),
            patch(
                "services.adaptive_intelligence.get_latest_model_version",
                return_value=latest_model,
            ),
            patch(
                "services.adaptive_intelligence.get_recent_transactions_feed",
                return_value=[],
            ),
            patch(
                "services.adaptive_intelligence.compute_flag_rates",
                return_value={"tracked": tracked},
            ),
            patch(
                "services.adaptive_intelligence._psi",
                return_value=psi_value,
            ),
        ):
            return build_monitoring()

    def test_automated_volume_trigger_activates_at_50_new_transactions(self):
        for new_transactions, expected in ((49, False), (50, True)):
            with self.subTest(new_transactions=new_transactions):
                monitoring = self._build_monitoring_with_mocks(
                    tracked=100 + new_transactions,
                    trained_samples=100,
                )
                trigger = monitoring["automated_trigger"]
                self.assertEqual(trigger["new_transactions_since_retrain"], new_transactions)
                self.assertEqual(trigger["volume_breach"], expected)
                self.assertEqual(trigger["action_required"], expected)
                self.assertEqual(
                    trigger["recommended_action"],
                    (
                        "RECORD_PROTOTYPE_BASELINE_MARKER"
                        if expected
                        else "MONITOR_STABLE"
                    ),
                )

    def test_automated_psi_trigger_activates_at_critical_boundary(self):
        samples = [
            {"amount": index, "risk_score": index / 20}
            for index in range(20)
        ]
        monitoring = self._build_monitoring_with_mocks(
            tracked=20,
            trained_samples=20,
            samples=samples,
            psi_value=0.25,
        )
        trigger = monitoring["automated_trigger"]
        self.assertTrue(trigger["psi_critical_breach"])
        self.assertTrue(trigger["action_required"])
        self.assertEqual(
            trigger["recommended_action"],
            "RECORD_PROTOTYPE_BASELINE_MARKER",
        )

    def test_custom_wallet_auto_provisioning_in_momo_simulator(self):
        custom_ref = "0248889900"
        customer = _ensure_customer(custom_ref)
        self.assertEqual(customer["ref"], custom_ref)
        self.assertEqual(customer["balance"], 5000.0)
        self.assertEqual(customer["pin"], "0000")

        # Test initiating a transaction with this custom wallet
        session = initiate_transaction(
            customer_ref=custom_ref,
            transaction_type="TRANSFER",
            amount=150.0,
            recipient="0551122334",
            device_id="DEV-CUSTOM-PHONE",
            ip_address="196.12.34.56",
        )
        self.assertIsNotNone(session)
        self.assertIn("session_id", session)
        self.assertIn(session.get("verification_method"), ("PIN", "OTP"))
        session_db = database.get_momo_session(session["session_id"])
        self.assertIsNotNone(session_db)
        self.assertIn(session_db["status"], ("PENDING", "INITIATED"))

    def test_automated_retraining_trigger_conditions(self):
        monitoring = build_monitoring()
        self.assertIn("automated_trigger", monitoring)
        trigger = monitoring["automated_trigger"]

        self.assertEqual(trigger["volume_threshold"], 50)
        self.assertEqual(trigger["psi_threshold"], 0.25)
        self.assertIsInstance(trigger["action_required"], bool)
        self.assertIsInstance(trigger["psi_critical_breach"], bool)
        self.assertIsInstance(trigger["volume_breach"], bool)
        self.assertIsInstance(trigger["new_transactions_since_retrain"], int)
        self.assertIsInstance(trigger["reasons"], list)
        self.assertTrue(trigger["auto_executable"])
        self.assertIn(
            trigger["recommended_action"],
            ("RECORD_PROTOTYPE_BASELINE_MARKER", "MONITOR_STABLE"),
        )


if __name__ == "__main__":
    unittest.main()
