"""Tests for the MTN Ghana production client using mocked HTTP responses."""

import json
import math
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

from services.mtn_collection import (
    MtnCollectionClient,
    MtnCollectionError,
    PRODUCTION_BASE_URL,
    PRODUCTION_MAX_AMOUNT,
    get_configuration_status,
    normalize_configured_amount,
)
from services.feature_engineering import build_features
import database.database as database
from app import app, auth, fraud_detector
from fastapi.testclient import TestClient


class MtnCollectionClientTests(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def handler(request):
            self.requests.append(request)
            if request.url.path == "/collection/token/":
                return httpx.Response(
                    200,
                    json={"access_token": "production-token", "expires_in": 3600},
                )
            if request.url.path == "/collection/v1_0/requesttopay":
                return httpx.Response(202)
            return httpx.Response(
                200,
                json={
                    "status": "SUCCESSFUL",
                    "amount": "12.50",
                    "currency": "GHS",
                    "financialTransactionId": "transaction-123",
                },
            )

        self.http_client = httpx.Client(transport=httpx.MockTransport(handler))
        self.client = MtnCollectionClient(
            "subscription-key",
            "api-user",
            "api-key",
            http_client=self.http_client,
            clock=lambda: 100,
        )

    def tearDown(self):
        self.http_client.close()

    def test_configuration_status_reports_missing_names_only(self):
        with patch.dict(
            "os.environ",
            {
                "MTN_PRODUCTION_COLLECTION_SUBSCRIPTION_KEY": "subscription-secret",
                "MTN_PRODUCTION_API_USER": "",
                "MTN_PRODUCTION_API_KEY": "api-secret",
            },
            clear=False,
        ):
            status = get_configuration_status()

        self.assertFalse(status["configured"])
        self.assertEqual(status["missing_variables"], ["MTN_PRODUCTION_API_USER"])
        self.assertEqual(status["environment"], "production")
        self.assertEqual(status["currency"], "GHS")
        self.assertEqual(status["maximum_amount"], "5.00")
        self.assertNotIn("subscription-secret", str(status))
        self.assertNotIn("api-secret", str(status))

    def test_production_configuration_uses_separate_credentials(self):
        with patch.dict(
            "os.environ",
            {
                "MTN_PRODUCTION_COLLECTION_SUBSCRIPTION_KEY": "prod-subscription",
                "MTN_PRODUCTION_API_USER": "prod-user",
                "MTN_PRODUCTION_API_KEY": "prod-api-key",
            },
            clear=False,
        ):
            status = get_configuration_status()
            client = MtnCollectionClient.from_environment()

        self.assertTrue(status["configured"])
        self.assertEqual(status["environment"], "production")
        self.assertEqual(status["currency"], "GHS")
        self.assertEqual(status["maximum_amount"], "5.00")
        self.assertEqual(client.environment, "production")
        self.assertEqual(client.subscription_key, "prod-subscription")
        self.assertEqual(normalize_configured_amount("5.00"), "5.00")
        with self.assertRaisesRegex(ValueError, "limited to GHS 5.00"):
            normalize_configured_amount("5.01")

    def test_request_to_pay_uses_ghana_production_contract_and_returns_pending(self):
        result = self.client.request_to_pay("5.00", "+233 24 123 4567")

        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["currency"], "GHS")
        self.assertEqual(str(uuid.UUID(result["reference_id"])), result["reference_id"])

        token_request, payment_request = self.requests
        self.assertEqual(token_request.url, f"{PRODUCTION_BASE_URL}/collection/token/")
        self.assertEqual(
            token_request.headers["Ocp-Apim-Subscription-Key"],
            "subscription-key",
        )
        self.assertTrue(payment_request.headers["Authorization"].startswith("Bearer "))
        self.assertEqual(
            payment_request.headers["X-Target-Environment"],
            "mtnghana",
        )
        self.assertEqual(
            payment_request.headers["X-Reference-Id"],
            result["reference_id"],
        )
        self.assertEqual(
            json.loads(payment_request.content),
            {
                "amount": "5.00",
                "currency": "GHS",
                "externalId": result["external_id"],
                "payer": {"partyIdType": "MSISDN", "partyId": "233241234567"},
                "payerMessage": "MoMo Sentinel demo payment",
                "payeeNote": "MoMo Sentinel demo payment",
            },
        )

    def test_production_request_uses_ghana_market_and_gated_amount(self):
        client = MtnCollectionClient(
            "production-subscription",
            "production-user",
            "production-key",
            http_client=self.http_client,
            clock=lambda: 100,
        )
        with patch.dict(
            "os.environ",
            {},
            clear=False,
        ):
            result = client.request_to_pay("5.00", "233241234567")

            self.assertEqual(result["currency"], "GHS")
            token_request, payment_request = self.requests[-2:]
            self.assertEqual(
                token_request.url,
                f"{PRODUCTION_BASE_URL}/collection/token/",
            )
            self.assertEqual(
                payment_request.headers["X-Target-Environment"],
                "mtnghana",
            )
            self.assertEqual(
                json.loads(payment_request.content)["currency"],
                "GHS",
            )
            with self.assertRaisesRegex(ValueError, "limited to GHS 5.00"):
                client.request_to_pay("5.01", "233241234567")

        self.assertEqual(PRODUCTION_MAX_AMOUNT, 5)
        self.assertEqual(len(self.requests), 2)

    def test_status_check_uses_reference_and_reuses_access_token(self):
        reference_id = str(uuid.uuid4())
        result = self.client.get_request_status(reference_id)

        self.assertEqual(result["status"], "SUCCESSFUL")
        self.assertEqual(result["reference_id"], reference_id)
        self.assertEqual(result["financialTransactionId"], "transaction-123")
        self.assertEqual(len([r for r in self.requests if r.url.path.endswith("/token/")]), 1)
        self.assertTrue(self.requests[-1].url.path.endswith(reference_id))

    def test_invalid_amount_and_msisdn_are_rejected_without_network_calls(self):
        with self.assertRaises(ValueError):
            self.            client.request_to_pay("0", "233241234567")
        with self.assertRaises(ValueError):
            self.            client.request_to_pay("1.234", "233241234567")
        with self.assertRaises(ValueError):
            self.            client.request_to_pay("1e1000", "233241234567")
        with self.assertRaises(ValueError):
            self.client.request_to_pay("1.00", "WALLET-1001")
        self.assertEqual(self.requests, [])

    def test_invalid_status_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client.get_request_status("not-a-uuid")
        self.assertEqual(self.requests, [])

    def test_upstream_auth_error_has_safe_actionable_message(self):
        def handler(request):
            return httpx.Response(401)

        client = MtnCollectionClient(
            "subscription-key",
            "api-user",
            "api-key",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        try:
            with self.assertRaises(MtnCollectionError) as caught:
                client.request_to_pay("1.00", "233241234567")
        finally:
            client.http_client.close()

        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("authentication failed", str(caught.exception))

    def test_unknown_recipient_history_is_not_scored_as_new(self):
        transaction = {
            "amount": 10,
            "transaction_type": "TRANSFER",
            "old_balance_origin": 500,
            "new_balance_origin": 490,
            "old_balance_destination": 500,
            "new_balance_destination": 510,
        }
        features = build_features(transaction, dest_previous_count=None)
        rule_score, reasons = fraud_detector.calculate_rule_score(
            transaction,
            features,
        )

        self.assertTrue(math.isnan(features["recipient_novel"]))
        self.assertEqual(rule_score, 0)
        self.assertNotIn("New recipient", reasons)


class MtnFraudGateRouteTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "mtn-route-test.sqlite3"
        database.initialize_database()
        self.client = TestClient(app)
        self.headers = {
            "X-Admin-Token": auth.create_access_token(
                user_id=1,
                username="test-admin",
                role="admin",
            )
        }

    def tearDown(self):
        self.client.close()
        database.DATABASE_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_high_risk_is_blocked_before_mtn_request(self):
        assessment = {
            "risk_score": 0.8,
            "risk_level": "HIGH",
            "decision": "TEMPORARY HOLD / BLOCK",
            "isolation_forest_score": 0.7,
            "rule_score": 0.9,
            "reasons": ["Test high risk"],
        }
        with patch("app.fraud_detector.assess_transaction", return_value=assessment), patch(
            "app.get_mtn_collection_client"
        ) as get_client:
            response = self.client.post(
                "/api/momo/mtn/collection/request-to-pay",
                headers=self.headers,
                json={
                    "amount": "5.00",
                    "payer_msisdn": "233241234567",
                    "production_confirmed": True,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "BLOCKED_BY_FRAUD_MODEL")
        self.assertIsNone(payload["mtn_reference_id"])
        self.assertEqual(payload["risk_assessment"]["risk_level"], "HIGH")
        get_client.assert_not_called()

    def test_assess_endpoint_returns_risk_without_contacting_mtn(self):
        assessment = {
            "risk_score": 0.1,
            "risk_level": "LOW",
            "decision": "APPROVE",
            "isolation_forest_score": 0.2,
            "rule_score": 0,
            "reasons": [],
        }
        with patch(
            "app.fraud_detector.assess_transaction",
            return_value=assessment,
        ) as assess, patch("app.get_mtn_collection_client") as get_client:
            response = self.client.post(
                "/api/momo/mtn/collection/assess",
                headers=self.headers,
                json={"amount": "5.00", "payer_msisdn": "233241234567"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "ASSESSED")
        self.assertEqual(payload["risk_assessment"]["risk_level"], "LOW")
        self.assertIn("Continue", payload["message"])
        get_client.assert_not_called()
        self.assertIsNone(assess.call_args.kwargs["dest_previous_count"])

    def test_configuration_endpoint_requires_admin_and_only_reports_missing_names(self):
        without_auth = self.client.get("/api/momo/mtn/collection/configuration")
        self.assertEqual(without_auth.status_code, 401)

        with patch.dict(
            "os.environ",
            {
                "MTN_PRODUCTION_COLLECTION_SUBSCRIPTION_KEY": "subscription-secret",
                "MTN_PRODUCTION_API_USER": "production-user",
                "MTN_PRODUCTION_API_KEY": "api-secret",
            },
            clear=False,
        ):
            response = self.client.get(
                "/api/momo/mtn/collection/configuration",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "configured": True,
                "missing_variables": [],
                "environment": "production",
                "currency": "GHS",
                "maximum_amount": "5.00",
            },
        )
        self.assertNotIn("subscription-secret", response.text)
        self.assertNotIn("api-secret", response.text)

    def test_low_risk_is_assessed_before_request_is_sent_to_mtn(self):
        assessment = {
            "risk_score": 0.1,
            "risk_level": "LOW",
            "decision": "APPROVE",
            "isolation_forest_score": 0.2,
            "rule_score": 0,
            "reasons": [],
        }
        fake_mtn_client = unittest.mock.Mock()
        fake_mtn_client.request_to_pay.return_value = {
            "reference_id": "93b1a6c0-c56d-4c96-a3bd-0735d4478eab",
            "external_id": "external-test",
            "status": "PENDING",
            "amount": "5.00",
            "currency": "GHS",
        }
        with patch(
            "app.fraud_detector.assess_transaction",
            return_value=assessment,
        ) as assess, patch(
            "app.get_mtn_collection_client",
            return_value=fake_mtn_client,
        ):
            response = self.client.post(
                "/api/momo/mtn/collection/request-to-pay",
                headers=self.headers,
                json={
                    "amount": "5.00",
                    "payer_msisdn": "233241234567",
                    "transaction_reference": "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
                    "production_confirmed": True,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "PENDING")
        self.assertEqual(payload["risk_assessment"]["risk_level"], "LOW")
        assess.assert_called_once()
        self.assertIsNone(assess.call_args.kwargs["dest_previous_count"])
        assessed_transaction = assess.call_args.args[0]
        self.assertEqual(
            assessed_transaction["transaction_reference"],
            "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
        )
        self.assertEqual(assessed_transaction["amount"], 5.0)
        self.assertTrue(all(
            math.isnan(assessed_transaction[field])
            for field in (
                "old_balance_origin",
                "new_balance_origin",
                "old_balance_destination",
                "new_balance_destination",
            )
        ))
        fake_mtn_client.request_to_pay.assert_called_once_with(
            amount="5.00",
            payer_msisdn="233241234567",
            reference_id="c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
        )

    def test_production_request_requires_explicit_confirmation(self):
        with patch.dict(
            "os.environ",
            {},
            clear=False,
        ), patch("app.fraud_detector.assess_transaction") as assess, patch(
            "app.get_mtn_collection_client"
        ) as get_client:
            response = self.client.post(
                "/api/momo/mtn/collection/request-to-pay",
                headers=self.headers,
                json={"amount": "1.00", "payer_msisdn": "233241234567"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Explicit confirmation", response.json()["detail"])
        assess.assert_not_called()
        get_client.assert_not_called()

    def test_production_assessment_enforces_the_server_side_amount_cap(self):
        with patch.dict(
            "os.environ",
            {},
            clear=False,
        ), patch("app.fraud_detector.assess_transaction") as assess:
            response = self.client.post(
                "/api/momo/mtn/collection/assess",
                headers=self.headers,
                json={"amount": "5.01", "payer_msisdn": "233241234567"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("limited to GHS 5.00", response.json()["detail"])
        assess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
