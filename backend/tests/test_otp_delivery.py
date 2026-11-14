"""Tests for one-time-code delivery and storage safety."""

import base64
import os
import unittest
from unittest.mock import Mock, patch

from services.email_service import send_medium_risk_otp_email
from services.otp_delivery import OtpDeliveryError, hash_one_time_code, verify_one_time_code
from services.sms_service import send_transaction_otp_sms


class OtpDeliveryTests(unittest.TestCase):
    def test_sms_uses_africas_talking_and_normalizes_ghana_phone(self):
        response = Mock()
        response.json.return_value = {
            "SMSMessageData": {
                "Recipients": [{"statusCode": 101, "status": "Success"}]
            }
        }
        with patch.dict(
            os.environ,
            {
                "AFRICASTALKING_ENVIRONMENT": "sandbox",
                "AFRICASTALKING_USERNAME": "sandbox",
                "AFRICASTALKING_API_KEY": "test-key-not-a-secret",
                "AFRICASTALKING_SENDER_ID": "",
            },
            clear=False,
        ), patch("services.sms_service.httpx.post", return_value=response) as post:
            delivered = send_transaction_otp_sms(
                "0241234567",
                "123456",
                {"transaction_type": "CASH_OUT", "amount": 5},
            )

        self.assertTrue(delivered)
        args, kwargs = post.call_args
        self.assertEqual(
            args[0],
            "https://api.sandbox.africastalking.com/version1/messaging",
        )
        self.assertEqual(kwargs["data"]["to"], "+233241234567")
        self.assertIn("123456", kwargs["data"]["message"])
        self.assertNotIn("api-key", kwargs["headers"])

    def test_sms_provider_rejection_returns_delivery_error(self):
        response = Mock()
        response.json.return_value = {
            "SMSMessageData": {
                "Recipients": [{"statusCode": 401, "status": "Rejected"}]
            }
        }
        with patch.dict(
            os.environ,
            {
                "AFRICASTALKING_ENVIRONMENT": "sandbox",
                "AFRICASTALKING_USERNAME": "sandbox",
                "AFRICASTALKING_API_KEY": "test-key",
            },
            clear=False,
        ), patch("services.sms_service.httpx.post", return_value=response):
            with self.assertRaisesRegex(OtpDeliveryError, "did not accept"):
                send_transaction_otp_sms("0241234567", "123456")

    def test_sms_rejects_non_ghana_numbers_before_provider_request(self):
        with patch.dict(
            os.environ,
            {
                "AFRICASTALKING_ENVIRONMENT": "sandbox",
                "AFRICASTALKING_USERNAME": "sandbox",
                "AFRICASTALKING_API_KEY": "test-key",
            },
            clear=False,
        ), patch("services.sms_service.httpx.post") as post:
            with self.assertRaisesRegex(OtpDeliveryError, "Ghana mobile"):
                send_transaction_otp_sms("12345", "123456")
        post.assert_not_called()

    def test_medium_risk_email_is_not_added_to_outbox(self):
        server = Mock()
        server.__enter__ = Mock(return_value=server)
        server.__exit__ = Mock(return_value=False)
        with patch(
            "services.email_service.get_smtp_config",
            return_value={
                "configured": True,
                "host": "smtp.example.com",
                "port": 587,
                "user": "pilot@example.com",
                "password": "test-password",
                "from_address": "pilot@example.com",
                "use_tls": True,
            },
        ), patch("services.email_service.smtplib.SMTP", return_value=server), patch(
            "services.email_service._RECENT_OUTBOX", new=[]
        ) as outbox:
            self.assertTrue(
                send_medium_risk_otp_email(
                    "tester@example.com",
                    "654321",
                    {"transaction_type": "TRANSFER", "amount": 5},
                )
            )
        encoded_message = server.sendmail.call_args.args[2].split("\n\n", 1)[1]
        decoded_message = base64.b64decode(encoded_message).decode("utf-8")
        self.assertIn(
            "654321",
            decoded_message,
        )
        self.assertIn("expires in 10 minutes", decoded_message)
        self.assertEqual(outbox, [])

    def test_medium_risk_email_uses_brevo_https_api_when_configured(self):
        response = Mock()
        with patch(
            "services.email_service.get_smtp_config",
            return_value={
                "configured": True,
                "host": "",
                "port": 587,
                "user": "",
                "password": "",
                "from_address": "verified@example.com",
                "use_tls": True,
                "brevo_configured": True,
                "brevo_api_key": "test-api-key",
            },
        ), patch.dict(
            os.environ, {"SMTP_FROM": "verified@example.com"}, clear=False
        ), patch("services.email_service.httpx.post", return_value=response) as post, patch(
            "services.email_service.smtplib.SMTP"
        ) as smtp:
            self.assertTrue(
                send_medium_risk_otp_email(
                    "tester@example.com",
                    "654321",
                    {"transaction_type": "TRANSFER", "amount": 5},
                )
            )

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(kwargs["headers"], {"api-key": "test-api-key"})
        self.assertEqual(kwargs["json"]["sender"]["email"], "verified@example.com")
        self.assertEqual(kwargs["json"]["to"], [{"email": "tester@example.com"}])
        self.assertIn("654321", kwargs["json"]["textContent"])
        smtp.assert_not_called()

    def test_medium_risk_email_reports_brevo_delivery_failure(self):
        import httpx

        response = Mock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "request failed",
            request=Mock(),
            response=Mock(status_code=401),
        )
        with patch(
            "services.email_service.get_smtp_config",
            return_value={
                "configured": True,
                "host": "",
                "port": 587,
                "user": "",
                "password": "",
                "from_address": "verified@example.com",
                "use_tls": True,
                "brevo_configured": True,
                "brevo_api_key": "test-api-key",
            },
        ), patch.dict(
            os.environ, {"SMTP_FROM": "verified@example.com"}, clear=False
        ), patch("services.email_service.httpx.post", return_value=response):
            with self.assertRaisesRegex(OtpDeliveryError, "No transaction was completed"):
                send_medium_risk_otp_email("tester@example.com", "654321")

    def test_one_time_code_hash_is_context_bound_and_not_plaintext(self):
        saved = hash_one_time_code("123456", "session-a")
        self.assertNotIn("123456", saved)
        self.assertTrue(verify_one_time_code("123456", saved, "session-a"))
        self.assertFalse(verify_one_time_code("123456", saved, "session-b"))
        self.assertFalse(verify_one_time_code("123456", "123456", "session-a"))


if __name__ == "__main__":
    unittest.main()
