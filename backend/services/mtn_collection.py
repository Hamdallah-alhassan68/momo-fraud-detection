"""MTN MoMo Collection API client for gated Ghana production testing."""

import os
import re
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation

import httpx


PRODUCTION_BASE_URL = "https://proxy.momoapi.mtn.com"
PRODUCTION_TARGET_ENVIRONMENT = "mtnghana"
PRODUCTION_CURRENCY = "GHS"
PRODUCTION_MAX_AMOUNT = Decimal("5.00")


class MtnCollectionError(Exception):
    def __init__(self, message, status_code=502, reference_id=None):
        super().__init__(message)
        self.status_code = status_code
        self.reference_id = reference_id


def get_configuration_status():
    required_variables = (
        "MTN_PRODUCTION_COLLECTION_SUBSCRIPTION_KEY",
        "MTN_PRODUCTION_API_USER",
        "MTN_PRODUCTION_API_KEY",
    )
    missing = [
        name
        for name in required_variables
        if not os.getenv(name, "").strip()
    ]
    return {
        "configured": not missing,
        "missing_variables": missing,
        "environment": "production",
        "currency": PRODUCTION_CURRENCY,
        "maximum_amount": format(PRODUCTION_MAX_AMOUNT, "f"),
    }


def normalize_configured_amount(amount):
    normalized = MtnCollectionClient._normalize_amount(amount)
    if Decimal(normalized) > PRODUCTION_MAX_AMOUNT:
        raise ValueError(
            "Production test payments are limited to GHS "
            f"{format(PRODUCTION_MAX_AMOUNT, 'f')}."
        )
    return normalized


class MtnCollectionClient:
    def __init__(
        self,
        subscription_key,
        api_user,
        api_key,
        http_client=None,
        clock=time.monotonic,
    ):
        self.subscription_key = subscription_key
        self.api_user = api_user
        self.api_key = api_key
        self.environment = "production"
        self.base_url = PRODUCTION_BASE_URL
        self.target_environment = PRODUCTION_TARGET_ENVIRONMENT
        self.currency = PRODUCTION_CURRENCY
        self.http_client = http_client or httpx.Client(timeout=15.0)
        self.clock = clock
        self._token = None
        self._token_expires_at = 0
        self._token_lock = threading.Lock()

    @classmethod
    def from_environment(cls):
        status = get_configuration_status()
        if not status["configured"]:
            missing = ", ".join(status["missing_variables"])
            raise MtnCollectionError(
                f"MTN {status['environment']} is not configured. Missing backend environment "
                f"variables: {missing}.",
                status_code=503,
            )

        variable_names = (
            "MTN_PRODUCTION_COLLECTION_SUBSCRIPTION_KEY",
            "MTN_PRODUCTION_API_USER",
            "MTN_PRODUCTION_API_KEY",
        )
        credentials = [os.getenv(name, "").strip() for name in variable_names]
        return cls(*credentials)

    def _send(self, method, path, operation, **kwargs):
        try:
            response = self.http_client.request(
                method,
                f"{self.base_url}{path}",
                **kwargs,
            )
        except httpx.TimeoutException as error:
            raise MtnCollectionError(
                f"MTN {self.environment} timed out during {operation}. Check the transaction "
                "status before submitting another payment request.",
                status_code=504,
            ) from error
        except httpx.RequestError as error:
            raise MtnCollectionError(
                f"Could not connect to MTN {self.environment} during {operation}.",
                status_code=502,
            ) from error

        if response.is_error:
            if response.status_code in (401, 403):
                message = (
                    f"MTN {self.environment} authentication failed. Check the API user, API key, "
                    "and Collection subscription key."
                )
            else:
                message = (
                    f"MTN {self.environment} returned HTTP {response.status_code} during "
                    f"{operation}."
                )
            raise MtnCollectionError(message)
        return response

    def _access_token(self):
        with self._token_lock:
            if self._token and self._token_expires_at > self.clock():
                return self._token

            response = self._send(
                "POST",
                "/collection/token/",
                "authentication",
                auth=(self.api_user, self.api_key),
                headers={
                    "Ocp-Apim-Subscription-Key": self.subscription_key,
                },
            )
            try:
                payload = response.json()
                token = payload["access_token"]
                expires_in = float(payload["expires_in"])
            except (ValueError, KeyError, TypeError) as error:
                raise MtnCollectionError(
                    f"MTN {self.environment} returned an invalid access-token response."
                ) from error

            if not isinstance(token, str) or not token or expires_in <= 0:
                raise MtnCollectionError(
                    f"MTN {self.environment} returned an invalid access-token response."
                )

            self._token = token
            self._token_expires_at = self.clock() + max(expires_in - 30, 1)
            return token

    @staticmethod
    def _normalize_amount(amount):
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            raise ValueError("Amount must be a positive number with up to 2 decimals.")
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -2:
            raise ValueError("Amount must be a positive number with up to 2 decimals.")
        try:
            return format(value.quantize(Decimal("0.01")), "f")
        except InvalidOperation as error:
            raise ValueError(
                "Amount must be a positive number with up to 2 decimals."
            ) from error

    @staticmethod
    def _normalize_msisdn(msisdn):
        if not isinstance(msisdn, str):
            raise ValueError("Enter the MTN payer mobile number.")
        normalized = re.sub(r"[\s()+-]", "", msisdn)
        if not normalized.isdigit() or not 8 <= len(normalized) <= 15:
            raise ValueError(
                "Enter an MSISDN with 8 to 15 digits, including its country code."
            )
        return normalized

    def request_to_pay(self, amount, payer_msisdn, reference_id=None):
        normalized_amount = normalize_configured_amount(amount)
        normalized_msisdn = self._normalize_msisdn(payer_msisdn)
        try:
            reference_id = str(uuid.UUID(reference_id)) if reference_id else str(uuid.uuid4())
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid MTN payment reference.") from error
        external_id = uuid.uuid4().hex

        response = self._send(
            "POST",
            "/collection/v1_0/requesttopay",
            "payment request",
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "X-Reference-Id": reference_id,
                "X-Target-Environment": self.target_environment,
                "Ocp-Apim-Subscription-Key": self.subscription_key,
                "Content-Type": "application/json",
            },
            json={
                "amount": normalized_amount,
                "currency": self.currency,
                "externalId": external_id,
                "payer": {
                    "partyIdType": "MSISDN",
                    "partyId": normalized_msisdn,
                },
                "payerMessage": "MoMo Sentinel demo payment",
                "payeeNote": "MoMo Sentinel demo payment",
            },
        )
        if response.status_code != 202:
            raise MtnCollectionError(
                f"MTN {self.environment} did not accept the payment request "
                f"(HTTP {response.status_code})."
            )
        return {
            "reference_id": reference_id,
            "external_id": external_id,
            "status": "PENDING",
            "amount": normalized_amount,
            "currency": self.currency,
        }

    def get_request_status(self, reference_id):
        try:
            normalized_reference = str(uuid.UUID(reference_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid MTN payment reference.") from error

        response = self._send(
            "GET",
            f"/collection/v1_0/requesttopay/{normalized_reference}",
            "payment status check",
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "X-Target-Environment": self.target_environment,
                "Ocp-Apim-Subscription-Key": self.subscription_key,
            },
        )
        try:
            payload = response.json()
            status = payload["status"]
        except (ValueError, KeyError, TypeError) as error:
            raise MtnCollectionError(
                f"MTN {self.environment} returned an invalid payment-status response."
            ) from error

        if not isinstance(status, str) or not status:
            raise MtnCollectionError(
                f"MTN {self.environment} returned an invalid payment-status response."
            )

        result = {"reference_id": normalized_reference, "status": status}
        for field in (
            "amount",
            "currency",
            "externalId",
            "financialTransactionId",
            "reason",
        ):
            value = payload.get(field)
            if isinstance(value, (str, int, float)):
                result[field] = value
        return result


_client = None
_client_lock = threading.Lock()


def get_mtn_collection_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = MtnCollectionClient.from_environment()
        return _client
