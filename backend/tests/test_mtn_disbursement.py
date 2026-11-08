"""Tests for MTN Disbursement using mocked HTTP responses."""

import base64
import json
import os
import uuid
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from app import app, auth
import database.database as database
from services.mtn_disbursement import (
    MtnDisbursementClient,
    MtnDisbursementError,
    get_configuration_status,
)


class MtnDisbursementClientTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.token_post_calls = []

        def handler(request):
            self.requests.append(request)
            if request.url.path == "/disbursement/v1_0/transfer":
                return httpx.Response(202)
            return httpx.Response(
                200,
                json={
                    "status": "SUCCESSFUL",
                    "amount": "5.00",
                    "currency": "EUR",
                    "financialTransactionId": "transfer-123",
                },
            )

        def token_post(url, headers, data, timeout):
            self.token_post_calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "data": data,
                    "timeout": timeout,
                }
            )
            request = httpx.Request(
                "POST",
                url,
                headers=headers,
                content=data,
            )
            self.requests.append(request)
            response = Mock(status_code=200)
            response.json.return_value = {
                "access_token": "disbursement-token",
                "expires_in": 3600,
            }
            return response

        self.token_post_patcher = patch(
            "services.mtn_disbursement.requests.post",
            side_effect=token_post,
        )
        self.token_post_patcher.start()
        self.http_client = httpx.Client(transport=httpx.MockTransport(handler))
        self.client = MtnDisbursementClient(
            "disbursement-subscription",
            "disbursement-user",
            "disbursement-key",
            http_client=self.http_client,
            clock=lambda: 100,
        )

    def tearDown(self):
        self.http_client.close()
        self.token_post_patcher.stop()

    def test_transfer_uses_disbursement_contract_and_requires_token(self):
        reference_id = str(uuid.uuid4())
        result = self.client.transfer("5.00", "+233 24 123 4567", reference_id)

        self.assertEqual(result["status"], "PENDING")
        self.assertNotEqual(result["reference_id"], reference_id)
        self.assertEqual(str(uuid.UUID(result["reference_id"])), result["reference_id"])
        self.assertEqual(result["recipient_msisdn"], "233241234567")
        token_request, transfer_request = self.requests
        self.assertEqual(
            str(token_request.url),
            "https://proxy.momoapi.mtn.com/disbursement/token/",
        )
        self.assertEqual(
            token_request.url.path,
            "/disbursement/token/",
        )
        self.assertEqual(
            token_request.headers["Ocp-Apim-Subscription-Key"],
            "disbursement-subscription",
        )
        self.assertEqual(
            transfer_request.url.path,
            "/disbursement/v1_0/transfer",
        )
        self.assertEqual(
            transfer_request.headers["X-Callback-Url"],
            "http://127.0.0.1/",
        )
        self.assertEqual(
            transfer_request.headers["Authorization"],
            "Bearer disbursement-token",
        )
        self.assertNotEqual(transfer_request.headers["X-Reference-Id"], reference_id)
        self.assertEqual(
            json.loads(transfer_request.content)["externalId"],
            reference_id,
        )
        self.assertEqual(
            transfer_request.headers["X-Target-Environment"],
            "mtnghana",
        )
        payload = json.loads(transfer_request.content)
        self.assertEqual(payload["amount"], "5.00")
        self.assertEqual(payload["currency"], "GHS")
        self.assertEqual(payload["externalId"], reference_id)
        self.assertEqual(
            payload["payee"],
            {"partyIdType": "MSISDN", "partyId": "233241234567"},
        )
        self.assertEqual(payload["payerMessage"], "MoMo Sentinel transfer")
        self.assertEqual(payload["payeeNote"], "MoMo Sentinel transfer")

    def test_transfer_status_uses_reference_and_cached_token(self):
        reference_id = str(uuid.uuid4())
        result = self.client.get_transfer_status(reference_id)

        self.assertEqual(result["status"], "SUCCESSFUL")
        self.assertEqual(result["reference_id"], reference_id)
        self.assertEqual(result["financialTransactionId"], "transfer-123")
        self.assertEqual(
            len([request for request in self.requests if request.url.path.endswith("/token/")]),
            1,
        )
        self.assertTrue(self.requests[-1].url.path.endswith(reference_id))
        self.assertEqual(
            self.requests[-1].headers["Authorization"],
            "Bearer disbursement-token",
        )

    def test_sandbox_uses_basic_auth_bearer_token_and_generated_reference(self):
        sandbox_client = MtnDisbursementClient(
            "sandbox-subscription",
            "sandbox-user",
            "sandbox-key",
            http_client=self.http_client,
            clock=lambda: 100,
            environment="sandbox",
        )

        result = sandbox_client.transfer(
            "1.00",
            "233241234567",
            "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
        )

        token_request, transfer_request = self.requests
        self.assertEqual(
            str(token_request.url),
            "https://sandbox.momodeveloper.mtn.com/disbursement/token/",
        )
        self.assertEqual(token_request.method, "POST")
        self.assertEqual(token_request.content, b"")
        self.assertNotIn("Content-Type", token_request.headers)
        self.assertEqual(self.token_post_calls[0]["data"], "")
        self.assertNotIn("Content-Length", self.token_post_calls[0]["headers"])
        self.assertEqual(
            self.token_post_calls[0]["url"],
            "https://sandbox.momodeveloper.mtn.com/disbursement/token/",
        )
        self.assertEqual(
            token_request.headers["Authorization"],
            "Basic "
            + base64.b64encode(b"sandbox-user:sandbox-key").decode("ascii"),
        )
        self.assertEqual(
            token_request.headers["Ocp-Apim-Subscription-Key"],
            "sandbox-subscription",
        )
        self.assertEqual(
            token_request.headers["Target-Environment"],
            "sandbox",
        )
        self.assertEqual(
            str(transfer_request.url),
            "https://sandbox.momodeveloper.mtn.com/disbursement/v1_0/transfer",
        )
        self.assertEqual(
            transfer_request.headers["X-Callback-Url"],
            "https://webhook.site/",
        )
        self.assertEqual(
            transfer_request.headers["Authorization"],
            "Bearer disbursement-token",
        )
        self.assertEqual(
            transfer_request.headers["Ocp-Apim-Subscription-Key"],
            "sandbox-subscription",
        )
        self.assertEqual(
            transfer_request.headers["X-Target-Environment"],
            "sandbox",
        )
        self.assertEqual(
            transfer_request.headers["Content-Type"],
            "application/json",
        )
        reference_id = transfer_request.headers["X-Reference-Id"]
        self.assertEqual(str(uuid.UUID(reference_id)), reference_id)
        self.assertEqual(uuid.UUID(reference_id).version, 4)
        self.assertNotEqual(reference_id, "c0bd43df-8a8b-4817-9bed-85a85ee85eb9")
        self.assertEqual(
            json.loads(transfer_request.content)["externalId"],
            "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
        )
        self.assertEqual(result["reference_id"], reference_id)
        payload = json.loads(transfer_request.content)
        self.assertEqual(payload["amount"], "1.00")
        self.assertEqual(payload["currency"], "EUR")
        self.assertEqual(
            payload["externalId"],
            "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
        )
        self.assertEqual(
            payload["payee"],
            {"partyIdType": "MSISDN", "partyId": "233241234567"},
        )
        self.assertEqual(payload["payerMessage"], "MoMo Sentinel transfer")
        self.assertEqual(payload["payeeNote"], "MoMo Sentinel transfer")

        second_result = sandbox_client.transfer(
            "1.00",
            "233241234567",
            "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
        )
        self.assertNotEqual(second_result["reference_id"], reference_id)
        self.assertEqual(
            len(
                [
                    request
                    for request in self.requests
                    if request.url.path == "/disbursement/v1_0/transfer"
                ]
            ),
            2,
        )
        transfer_requests = [
            request
            for request in self.requests
            if request.url.path == "/disbursement/v1_0/transfer"
        ]
        reference_ids = [
            request.headers["X-Reference-Id"]
            for request in transfer_requests
        ]
        self.assertEqual(len(set(reference_ids)), len(reference_ids))
        for request, transfer_reference in zip(transfer_requests, reference_ids):
            self.assertEqual(str(uuid.UUID(transfer_reference)), transfer_reference)
            self.assertEqual(uuid.UUID(transfer_reference).version, 4)
            self.assertEqual(
                request.headers["Authorization"],
                "Bearer disbursement-token",
            )
            self.assertEqual(
                request.headers["X-Callback-Url"],
                "https://webhook.site/",
            )
            self.assertEqual(
                request.headers["X-Target-Environment"],
                "sandbox",
            )
            self.assertEqual(
                request.headers["Ocp-Apim-Subscription-Key"],
                "sandbox-subscription",
            )
            self.assertEqual(request.headers["Content-Type"], "application/json")

    def test_sandbox_configuration_reads_sandbox_credentials(self):
        variables = {
            "MTN_DISBURSEMENT_ENVIRONMENT": "sandbox",
            "MTN_SANDBOX_DISBURSEMENT_SUBSCRIPTION_KEY": "sandbox-subscription",
            "MTN_SANDBOX_DISBURSEMENT_API_USER": "sandbox-user",
            "MTN_SANDBOX_DISBURSEMENT_API_KEY": "sandbox-key",
            "MOMO_ADMIN_TOKEN": "test-admin-token-with-more-than-32-characters",
        }
        with patch.dict(os.environ, variables, clear=False):
            status = get_configuration_status()
            client = MtnDisbursementClient.from_environment()

        self.assertTrue(status["configured"])
        self.assertEqual(status["environment"], "sandbox")
        self.assertEqual(status["missing_variables"], [])
        self.assertEqual(client.base_url, "https://sandbox.momodeveloper.mtn.com")
        self.assertEqual(client.target_environment, "sandbox")
        self.assertEqual(client.currency, "EUR")

    def test_callback_url_can_be_configured(self):
        with patch.dict(
            os.environ,
            {"MTN_DISBURSEMENT_CALLBACK_URL": "https://example.test/mtn/callback"},
            clear=False,
        ):
            client = MtnDisbursementClient(
                "sandbox-subscription",
                "sandbox-user",
                "sandbox-key",
                http_client=self.http_client,
                environment="sandbox",
            )

        self.assertEqual(
            client.callback_url,
            "https://example.test/mtn/callback",
        )

    def test_sandbox_is_the_default_environment(self):
        variables = {
            "MTN_SANDBOX_DISBURSEMENT_SUBSCRIPTION_KEY": "sandbox-subscription",
            "MTN_SANDBOX_DISBURSEMENT_API_USER": "sandbox-user",
            "MTN_SANDBOX_DISBURSEMENT_API_KEY": "sandbox-key",
            "MOMO_ADMIN_TOKEN": "test-admin-token-with-more-than-32-characters",
        }
        with patch.dict(os.environ, variables, clear=True):
            status = get_configuration_status()
            client = MtnDisbursementClient.from_environment()

        self.assertTrue(status["configured"])
        self.assertEqual(status["environment"], "sandbox")
        self.assertEqual(client.base_url, "https://sandbox.momodeveloper.mtn.com")

    def test_invalid_amount_recipient_and_reference_are_rejected_before_network(self):
        with self.assertRaises(ValueError):
            self.client.transfer("5.01", "233241234567")
        with self.assertRaises(ValueError):
            self.client.transfer("1.00", "not-a-number")
        with self.assertRaises(ValueError):
            self.client.get_transfer_status("not-a-uuid")
        self.assertEqual(self.requests, [])

    def test_token_http_error_preserves_upstream_status_code(self):
        client = MtnDisbursementClient(
            "sandbox-subscription",
            "sandbox-user",
            "sandbox-key",
            environment="sandbox",
        )
        self.token_post_patcher.stop()
        with patch(
            "services.mtn_disbursement.requests.post",
            return_value=Mock(
                status_code=400,
                text="invalid sandbox credentials",
            ),
        ):
            with self.assertRaises(MtnDisbursementError) as raised:
                client._access_token()
            self.assertEqual(raised.exception.status_code, 400)
        self.token_post_patcher.start()

    def test_configuration_reports_only_missing_disbursement_variables(self):
        names = {
            "MTN_DISBURSEMENT_ENVIRONMENT": "production",
            "MTN_PRODUCTION_DISBURSEMENT_SUBSCRIPTION_KEY": "",
            "MTN_PRODUCTION_DISBURSEMENT_API_USER": "user",
            "MTN_PRODUCTION_DISBURSEMENT_API_KEY": "",
            "MOMO_ADMIN_TOKEN": "test-admin-token-with-more-than-32-characters",
        }
        with patch.dict(os.environ, names, clear=False):
            status = get_configuration_status()

        self.assertFalse(status["configured"])
        self.assertEqual(
            status["missing_variables"],
            [
                "MTN_PRODUCTION_DISBURSEMENT_SUBSCRIPTION_KEY",
                "MTN_PRODUCTION_DISBURSEMENT_API_KEY",
            ],
        )
        self.assertNotIn("user", str(status))
        self.assertEqual(status["currency"], "GHS")
        self.assertEqual(status["maximum_amount"], "5.00")

    def test_missing_credentials_fail_clearly(self):
        names = (
            "MTN_PRODUCTION_DISBURSEMENT_SUBSCRIPTION_KEY",
            "MTN_PRODUCTION_DISBURSEMENT_API_USER",
            "MTN_PRODUCTION_DISBURSEMENT_API_KEY",
        )
        with patch.dict(
            os.environ,
            {
                **{name: "" for name in names},
                "MTN_DISBURSEMENT_ENVIRONMENT": "production",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(MtnDisbursementError, names[0]):
                MtnDisbursementClient.from_environment()



    def test_transfer_accepts_arbitrary_external_id_string(self):
        result = self.client.transfer("1.00", "233241234567", external_id="EXT-TX-998877")
        self.assertEqual(result["status"], "PENDING")
        transfer_request = self.requests[-1]
        payload = json.loads(transfer_request.content)
        self.assertEqual(payload["externalId"], "EXT-TX-998877")
        # Header X-Reference-Id must be canonical lowercase UUID v4
        ref_header = transfer_request.headers["X-Reference-Id"]
        self.assertEqual(ref_header, ref_header.lower())
        self.assertEqual(str(uuid.UUID(ref_header)), ref_header)

    def test_sandbox_allows_larger_amounts_than_production(self):
        sandbox_client = MtnDisbursementClient(
            "sandbox-subscription",
            "sandbox-user",
            "sandbox-key",
            http_client=self.http_client,
            environment="sandbox",
        )
        result = sandbox_client.transfer("100.00", "233241234567")
        self.assertEqual(result["amount"], "100.00")
        self.assertEqual(result["currency"], "EUR")

    def test_sandbox_rejects_amounts_above_provider_limit(self):
        sandbox_client = MtnDisbursementClient(
            "sandbox-subscription",
            "sandbox-user",
            "sandbox-key",
            http_client=self.http_client,
            environment="sandbox",
        )
        with self.assertRaisesRegex(ValueError, "EUR 10000.00"):
            sandbox_client.transfer("10000.01", "233241234567")

    def test_transfer_error_includes_gateway_json_detail(self):
        err_response = httpx.Response(
            400,
            json={"code": "INVALID_CALLBACK_URL_HOST", "message": "Callback host mismatch"},
            request=httpx.Request("POST", "https://example.com"),
        )
        error_client = httpx.Client(transport=httpx.MockTransport(lambda r: err_response))
        client = MtnDisbursementClient(
            "sub", "user", "key", http_client=error_client, environment="sandbox"
        )
        with patch.object(client, "_access_token", return_value="fake-token"):
            with self.assertRaises(MtnDisbursementError) as ctx:
                client.transfer("1.00", "233241234567")
            self.assertIn("INVALID_CALLBACK_URL_HOST", str(ctx.exception))
            self.assertIn("Callback host mismatch", str(ctx.exception))


class MtnDisbursementRouteTests(unittest.TestCase):
    def setUp(self):
        self.admin_token = "test-live-transfer-admin-token-0123456789"
        self.admin_token_patcher = patch.dict(
            os.environ,
            {"MOMO_ADMIN_TOKEN": self.admin_token},
            clear=False,
        )
        self.admin_token_patcher.start()
        self.client = TestClient(app)
        self.headers = {
            "X-Admin-Token": self.admin_token
        }

    def tearDown(self):
        self.client.close()
        self.admin_token_patcher.stop()

    def test_simulation_injects_requested_signals_without_sending_transfer(self):
        baseline = {
            "risk_score": 0.1,
            "risk_level": "LOW",
            "decision": "APPROVE",
            "isolation_forest_score": 0.2,
            "rule_score": 0,
            "reasons": [],
        }
        scenario = {
            "risk_score": 0.6,
            "risk_level": "HIGH",
            "decision": "TEMPORARY HOLD / BLOCK",
            "isolation_forest_score": 0.2,
            "rule_score": 0.5,
            "reasons": ["New or changed device", "New recipient"],
        }
        with patch(
            "app.fraud_detector.assess_transaction",
            side_effect=[baseline, scenario],
        ) as assess, patch("app.get_mtn_disbursement_client") as get_client:
            response = self.client.post(
                "/api/momo/mtn/disbursement/simulate",
                headers=self.headers,
                json={
                    "amount": "1.00",
                    "recipient_msisdn": "233241234567",
                    "scenarios": ["DEVICE_CHANGE", "NEW_RECIPIENT"],
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["mode"], "SIMULATION_ONLY")
        self.assertFalse(result["transfer_sent"])
        self.assertEqual(result["risk_score_change"], 0.5)
        self.assertEqual(
            result["simulated_signals"],
            {"device_change": True, "recipient_novel": True},
        )
        self.assertEqual(
            assess.call_args_list[1].args[0]["device_change"],
            True,
        )
        self.assertEqual(
            assess.call_args_list[0].kwargs["dest_previous_count"],
            5,
        )
        self.assertEqual(
            assess.call_args_list[1].kwargs["dest_previous_count"],
            0,
        )
        get_client.assert_not_called()

    def test_simulation_rejects_unknown_scenario_without_assessing(self):
        with patch("app.fraud_detector.assess_transaction") as assess:
            response = self.client.post(
                "/api/momo/mtn/disbursement/simulate",
                headers=self.headers,
                json={
                    "amount": "1.00",
                    "recipient_msisdn": "233241234567",
                    "scenarios": ["UNSUPPORTED"],
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported risk scenarios", response.json()["detail"])
        assess.assert_not_called()

    def test_simulated_new_recipient_increases_risk_score(self):
        baseline_response = self.client.post(
            "/api/momo/mtn/disbursement/simulate",
            headers=self.headers,
            json={
                "amount": "1.00",
                "recipient_msisdn": "233241234567",
                "scenarios": [],
            },
        )
        new_recipient_response = self.client.post(
            "/api/momo/mtn/disbursement/simulate",
            headers=self.headers,
            json={
                "amount": "1.00",
                "recipient_msisdn": "233241234567",
                "scenarios": ["NEW_RECIPIENT"],
            },
        )

        self.assertEqual(baseline_response.status_code, 200)
        self.assertEqual(new_recipient_response.status_code, 200)
        baseline_score = baseline_response.json()["risk_assessment"]["risk_score"]
        simulation = new_recipient_response.json()
        self.assertGreater(simulation["risk_assessment"]["risk_score"], baseline_score)
        self.assertIn("New recipient", simulation["risk_assessment"]["reasons"])

    def test_medium_risk_transfer_requires_step_up_and_does_not_call_mtn(self):
        assessment = {
            "transaction_reference": "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
            "risk_score": 0.4,
            "risk_level": "MEDIUM",
            "decision": "STEP-UP VERIFICATION",
            "isolation_forest_score": 0.2,
            "rule_score": 0.3,
            "reasons": ["New or changed device"],
        }
        with patch(
            "app._assess_mtn_disbursement",
            return_value=("1.00", "233241234567", assessment),
        ), patch("app.get_mtn_disbursement_client") as get_client, patch(
            "app.create_audit_log"
        ) as audit_log:
            response = self.client.post(
                "/api/momo/mtn/disbursement/transfer",
                headers=self.headers,
                json={
                    "amount": "1.00",
                    "recipient_msisdn": "233241234567",
                    "transaction_reference": assessment["transaction_reference"],
                    "production_confirmed": True,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "STEP_UP_REQUIRED")
        get_client.assert_not_called()
        self.assertEqual(
            audit_log.call_args.args[1],
            "MTN_DISBURSEMENT_STEP_UP_REQUIRED",
        )

    def test_transfer_requires_explicit_confirmation(self):
        with patch("app.fraud_detector.assess_transaction") as assess, patch(
            "app.get_mtn_disbursement_client"
        ) as get_client:
            response = self.client.post(
                "/api/momo/mtn/disbursement/transfer",
                headers=self.headers,
                json={
                    "amount": "1.00",
                    "recipient_msisdn": "233241234567",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Explicit confirmation", response.json()["detail"])
        assess.assert_not_called()
        get_client.assert_not_called()

    def test_transfer_fails_closed_without_a_dedicated_admin_token(self):
        with patch.dict(os.environ, {"MOMO_ADMIN_TOKEN": ""}), patch(
            "app.fraud_detector.assess_transaction"
        ) as assess, patch("app.get_mtn_disbursement_client") as get_client:
            response = self.client.post(
                "/api/momo/mtn/disbursement/transfer",
                headers=self.headers,
                json={
                    "amount": "1.00",
                    "recipient_msisdn": "233241234567",
                    "production_confirmed": True,
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("MOMO_ADMIN_TOKEN", response.json()["detail"])
        assess.assert_not_called()
        get_client.assert_not_called()

    def test_transfer_rejects_demo_admin_tokens(self):
        demo_token = auth.create_access_token(
            user_id=1,
            username="admin",
            role="admin",
        )
        with patch("app.fraud_detector.assess_transaction") as assess, patch(
            "app.get_mtn_disbursement_client"
        ) as get_client:
            response = self.client.post(
                "/api/momo/mtn/disbursement/transfer",
                headers={"X-Admin-Token": demo_token},
                json={
                    "amount": "1.00",
                    "recipient_msisdn": "233241234567",
                    "production_confirmed": True,
                },
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("dedicated backend admin token", response.json()["detail"])
        assess.assert_not_called()
        get_client.assert_not_called()

    def test_high_risk_transfer_is_blocked_without_contacting_mtn(self):
        assessment = {
            "risk_score": 0.9,
            "risk_level": "HIGH",
            "decision": "TEMPORARY HOLD / BLOCK",
            "isolation_forest_score": 0.9,
            "rule_score": 0.9,
            "reasons": ["Test high risk"],
        }
        with patch(
            "app.fraud_detector.assess_transaction",
            return_value=assessment,
        ), patch("app.get_mtn_disbursement_client") as get_client, patch(
            "app.create_audit_log"
        ) as audit_log:
            response = self.client.post(
                "/api/momo/mtn/disbursement/transfer",
                headers=self.headers,
                json={
                    "amount": "1.00",
                    "recipient_msisdn": "233241234567",
                    "transaction_reference": "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
                    "production_confirmed": True,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "BLOCKED_BY_FRAUD_MODEL")
        self.assertIsNone(response.json()["reference_id"])
        get_client.assert_not_called()
        self.assertEqual(
            audit_log.call_args.args[1],
            "MTN_DISBURSEMENT_BLOCKED_BY_RISK",
        )

    def test_low_risk_transfer_sends_to_assessed_recipient(self):
        assessment = {
            "risk_score": 0.1,
            "risk_level": "LOW",
            "decision": "APPROVE",
            "isolation_forest_score": 0.2,
            "rule_score": 0,
            "reasons": [],
        }
        fake_client = unittest.mock.Mock()
        fake_client.transfer.return_value = {
            "reference_id": "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
            "status": "PENDING",
            "amount": "1.00",
            "currency": "GHS",
            "recipient_msisdn": "233241234567",
        }
        with patch(
            "app.fraud_detector.assess_transaction",
            return_value=assessment,
        ) as assess, patch(
            "app.get_mtn_disbursement_client",
            return_value=fake_client,
        ), patch(
            "app.create_audit_log"
        ):
            response = self.client.post(
                "/api/momo/mtn/disbursement/transfer",
                headers=self.headers,
                json={
                    "amount": "1.00",
                    "recipient_msisdn": "+233 24 123 4567",
                    "transaction_reference": "c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
                    "production_confirmed": True,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "PENDING")
        self.assertEqual(
            response.json()["recipient_msisdn"],
            "233241234567",
        )
        self.assertEqual(
            assess.call_args.args[0]["destination_account"],
            "233241234567",
        )
        fake_client.transfer.assert_called_once_with(
            amount="1.00",
            recipient_msisdn="233241234567",
            external_id="c0bd43df-8a8b-4817-9bed-85a85ee85eb9",
        )


class SandboxCustomerEmailVerificationTests(unittest.TestCase):
    def setUp(self):
        import app as app_module

        self.app_module = app_module
        self.client = TestClient(app)
        self.subscriber = {
            "customer_ref": "WALLET-1001",
            "phone_number": "0241001001",
            "full_name": "Test Customer",
            "email": "",
            "email_verified": False,
            "status": "ACTIVE",
        }

    def tearDown(self):
        self.client.close()
        self.app_module._EMAIL_VERIFICATION_CHALLENGES.clear()

    def test_customer_email_requires_and_completes_an_ownership_challenge(self):
        with patch.dict(
            os.environ, {"MTN_DISBURSEMENT_ENVIRONMENT": "sandbox"}, clear=False
        ), patch("app.find_subscriber", return_value=self.subscriber), patch(
            "app.verify_subscriber_pin", return_value=(True, "PIN verified")
        ), patch(
            "app.send_email_verification_email", return_value=True
        ) as send_email, patch(
            "app.kyc_update_email"
        ) as save_email, patch(
            "app.momo_update_email"
        ):
            login = self.client.post(
                "/api/momo/customer/login",
                json={"phone_number": self.subscriber["phone_number"], "pin": "1234"},
            )
            self.assertEqual(login.status_code, 200, login.text)
            headers = {"Authorization": f"Bearer {login.json()['token']}"}

            challenge = self.client.post(
                "/api/momo/customer/email/challenge",
                headers=headers,
                json={"email": "customer@example.com"},
            )
            self.assertEqual(challenge.status_code, 200, challenge.text)
            self.assertNotIn("code", challenge.json())
            code = send_email.call_args.args[1]

            verified = self.client.post(
                "/api/momo/customer/email/verify",
                headers=headers,
                json={"email": "customer@example.com", "code": code},
            )

        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertTrue(verified.json()["email_verified"])
        save_email.assert_called_once_with(
            "WALLET-1001", "customer@example.com", verified=True
        )

    def test_unresolved_mtn_transfer_blocks_a_duplicate_submission(self):
        token = auth.create_access_token(
            user_id=0,
            username=self.subscriber["phone_number"],
            role="momo_customer",
        )
        with patch.dict(
            os.environ, {"MTN_DISBURSEMENT_ENVIRONMENT": "sandbox"}, clear=False
        ), patch("app.find_subscriber", return_value=self.subscriber), patch(
            "app.has_pending_mtn_transfer", return_value=True
        ), patch("app.momo_initiate") as initiate:
            response = self.client.post(
                "/api/momo/transaction/initiate",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "transaction_type": "TRANSFER",
                    "amount": 1,
                    "recipient": "0242002002",
                    "live_mtn": True,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("still unresolved", response.json()["detail"])
        initiate.assert_not_called()


class SandboxCustomerEnrollmentTests(unittest.TestCase):
    def setUp(self):
        import app as app_module
        import tempfile
        from pathlib import Path
        from services import momo_simulator

        self.app_module = app_module
        self.momo_simulator = momo_simulator
        self.original_database_path = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "sandbox-enrollment.sqlite3"
        database.initialize_database()
        self.client = TestClient(app)
        self.phone = "0261234567"

    def tearDown(self):
        self.client.close()
        self.momo_simulator.CUSTOMERS[:] = [
            customer
            for customer in self.momo_simulator.CUSTOMERS
            if customer["ref"] != self.phone
        ]
        self.momo_simulator.BALANCES.pop(self.phone, None)
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_registration_stores_email_without_verification_and_creates_signin(self):
        with patch.dict(
            os.environ, {"MTN_DISBURSEMENT_ENVIRONMENT": "sandbox"}, clear=False
        ), patch(
            "app.send_email_verification_email", return_value=True
        ) as send_email:
            created = self.client.post(
                "/api/momo/customer/register",
                json={
                    "phone_number": self.phone,
                    "full_name": "Sandbox Test Customer",
                    "id_type": "PASSPORT",
                    "id_number": "P-TEST-84521",
                    "email": "customer@example.test",
                    "pin": "4826",
                    "device_id": "browser-device-1",
                    "location_latitude": 5.60,
                    "location_longitude": -0.18,
                },
            )
            self.assertEqual(created.status_code, 200, created.text)

        profile = database.get_sandbox_customer_profile(self.phone)
        self.assertEqual(profile["full_name"], "Sandbox Test Customer")
        self.assertEqual(profile["email"], "customer@example.test")
        self.assertFalse(profile["email_verified"])
        self.assertNotEqual(profile["pin_hash"], "4826")
        self.assertTrue(auth.verify_password("4826", profile["pin_hash"]))
        self.assertTrue(auth.verify_password("PTEST84521", profile["id_hash"]))
        self.assertEqual(created.json()["customer"]["name"], "Sandbox Test Customer")
        self.assertFalse(created.json()["customer"]["email_verified"])
        send_email.assert_not_called()

        login = self.client.post(
            "/api/momo/customer/login",
            json={
                "phone_number": "+233 26 123 4567",
                "pin": "4826",
                "device_id": "browser-device-1",
                "location_latitude": 5.60,
                "location_longitude": -0.18,
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["customer"]["phone_number"], self.phone)
        history = database.get_customer_security_history(self.phone)
        self.assertIn("browser-device-1", history["device_ids"])
        self.assertIn("5.6", {str(location[0]) for location in history["locations"]})

    def test_transaction_requires_location_and_persists_unshown_security_signals(self):
        database.create_sandbox_customer_profile(
            phone_number=self.phone,
            customer_ref=self.phone,
            full_name="Sandbox Test Customer",
            id_type="PASSPORT",
            id_hash=auth.hash_password("PTEST84521"),
            email="customer@example.test",
            pin_hash=auth.hash_password("4826"),
        )
        token = auth.create_access_token(
            user_id=0,
            username=self.phone,
            role="momo_customer",
        )
        headers = {"Authorization": f"Bearer {token}"}
        with patch.dict(
            os.environ, {"MTN_DISBURSEMENT_ENVIRONMENT": "sandbox"}, clear=False
        ), patch("app.momo_initiate") as initiate:
            missing_location = self.client.post(
                "/api/momo/transaction/initiate",
                headers=headers,
                json={
                    "transaction_type": "TRANSFER",
                    "amount": 1,
                    "recipient": "0242002002",
                    "device_id": "browser-device-2",
                    "live_mtn": True,
                },
            )
            self.assertEqual(missing_location.status_code, 428)
            initiate.assert_not_called()

            initiate.return_value = {"session_id": "test-session"}
            started = self.client.post(
                "/api/momo/transaction/initiate",
                headers=headers,
                json={
                    "transaction_type": "TRANSFER",
                    "amount": 1,
                    "recipient": "0242002002",
                    "device_id": "browser-device-2",
                    "location_latitude": 5.61,
                    "location_longitude": -0.19,
                    "live_mtn": True,
                },
            )

        self.assertEqual(started.status_code, 200, started.text)
        connection = database.get_connection()
        try:
            signal = connection.execute(
                """
                SELECT event_type, device_id, ip_address,
                       location_latitude, location_longitude, trusted
                FROM customer_security_signals
                WHERE session_id = 'test-session'
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(signal["device_id"], "browser-device-2")
        self.assertEqual(signal["location_latitude"], 5.61)
        self.assertEqual(signal["location_longitude"], -0.19)
        self.assertFalse(signal["trusted"])

    def test_registered_wallet_pin_authorizes_transaction_using_saved_email(self):
        with patch.dict(
            os.environ, {"MTN_DISBURSEMENT_ENVIRONMENT": "sandbox"}, clear=False
        ), patch(
            "services.momo_simulator._resolve_outcome",
            return_value={"status": "AUTHORIZED"},
        ) as resolve_outcome:
            created = self.client.post(
                "/api/momo/customer/register",
                json={
                    "phone_number": self.phone,
                    "full_name": "Sandbox Test Customer",
                    "id_type": "PASSPORT",
                    "id_number": "P-TEST-84521",
                    "email": "customer@example.test",
                    "pin": "4826",
                    "device_id": "browser-device-1",
                    "location_latitude": 5.60,
                    "location_longitude": -0.18,
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            headers = {"Authorization": f"Bearer {created.json()['token']}"}
            started = self.client.post(
                "/api/momo/transaction/initiate",
                headers=headers,
                json={
                    "transaction_type": "TRANSFER",
                    "amount": 1,
                    "recipient": "0242002002",
                    "device_id": "browser-device-1",
                    "location_latitude": 5.60,
                    "location_longitude": -0.18,
                    "live_mtn": True,
                },
            )
            self.assertEqual(started.status_code, 200, started.text)
            verified = self.client.post(
                "/api/momo/transaction/verify",
                headers=headers,
                json={
                    "session_id": started.json()["session_id"],
                    "code": "4826",
                },
            )

        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertEqual(verified.json()["status"], "AUTHORIZED")
        self.assertEqual(
            resolve_outcome.call_args.kwargs["step_up_email"],
            "customer@example.test",
        )


class StepUpCredentialTests(unittest.TestCase):
    def test_passport_or_email_code_can_authenticate_step_up(self):
        from services import momo_simulator

        session = {
            "status": "STEP_UP",
            "customer_ref": "0241001001",
            "live_mtn": False,
            "otp_code": "123456",
        }
        passport_customer = {
            "id_type": "PASSPORT",
            "id_hash": auth.hash_password("PTEST84521"),
        }
        momo_simulator._verify_step_up_credential(
            passport_customer, session, "P-TEST-84521", "PASSPORT"
        )
        momo_simulator._verify_step_up_credential(
            passport_customer, session, "123456", "EMAIL"
        )

        with self.assertRaisesRegex(ValueError, "Incorrect passport number"):
            momo_simulator._verify_step_up_credential(
                passport_customer, session, "wrong", "PASSPORT"
            )
        with self.assertRaisesRegex(ValueError, "not available"):
            momo_simulator._verify_step_up_credential(
                {"id_type": "GHANA_CARD"}, session, "GHA-12345", "PASSPORT"
            )
