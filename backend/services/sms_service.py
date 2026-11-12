"""Africa's Talking SMS delivery for pilot transaction OTPs."""

import os
import re

import httpx

from services.otp_delivery import OtpDeliveryError


def _get_africastalking_config():
    environment = os.getenv("AFRICASTALKING_ENVIRONMENT", "sandbox").strip().lower()
    if environment not in {"sandbox", "production"}:
        raise OtpDeliveryError(
            "Africa's Talking environment must be sandbox or production."
        )

    username = os.getenv("AFRICASTALKING_USERNAME", "").strip()
    if not username and environment == "sandbox":
        username = "sandbox"
    api_key = os.getenv("AFRICASTALKING_API_KEY", "").strip()
    if not username or not api_key:
        raise OtpDeliveryError(
            "Africa's Talking SMS is not configured. Set its username and API key."
        )

    return {
        "environment": environment,
        "username": username,
        "api_key": api_key,
        "sender_id": os.getenv("AFRICASTALKING_SENDER_ID", "").strip(),
    }


def _normalize_ghana_phone(phone_number):
    digits = re.sub(r"\D", "", str(phone_number or ""))
    if len(digits) == 10 and digits.startswith("0"):
        digits = "233" + digits[1:]
    if len(digits) != 12 or not digits.startswith("233"):
        raise OtpDeliveryError(
            "The wallet phone number is not a valid Ghana mobile number."
        )
    return f"+{digits}"


def send_transaction_otp_sms(phone_number, otp_code, transaction_details=None):
    """Send an OTP via Africa's Talking without exposing it in the API response."""
    config = _get_africastalking_config()
    recipient = _normalize_ghana_phone(phone_number)
    details = transaction_details or {}
    transaction_type = str(details.get("transaction_type") or "MoMo").replace(
        "_", " "
    )
    payload = {
        "username": config["username"],
        "to": recipient,
        "message": (
            f"MoMo Sentinel: your {transaction_type} authorization code is "
            f"{otp_code}. It expires in 10 minutes. Do not share it."
        ),
    }
    if config["sender_id"]:
        payload["from"] = config["sender_id"]

    endpoint = (
        "https://api.sandbox.africastalking.com/version1/messaging"
        if config["environment"] == "sandbox"
        else "https://api.africastalking.com/version1/messaging"
    )
    try:
        response = httpx.post(
            endpoint,
            headers={
                "apiKey": config["api_key"],
                "Accept": "application/json",
            },
            data=payload,
            timeout=5.0,
        )
        response.raise_for_status()
        response_data = response.json()
        sms_data = (
            response_data.get("SMSMessageData", {})
            if isinstance(response_data, dict)
            else {}
        )
        recipients = sms_data.get("Recipients") if isinstance(sms_data, dict) else None
        if not isinstance(recipients, list) or not recipients:
            raise OtpDeliveryError(
                "Africa's Talking did not confirm SMS delivery."
            )
        if not any(
            str(item.get("statusCode")) in {"100", "101", "102"}
            for item in recipients
            if isinstance(item, dict)
        ):
            raise OtpDeliveryError(
                "Africa's Talking did not accept the OTP message."
            )
    except OtpDeliveryError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise OtpDeliveryError(
            "SMS delivery failed. No transaction was started; please try again."
        ) from error
    return True
