"""MTN MoMo Disbursement API client for gated production and sandbox transfers."""

import base64
from decimal import Decimal
import os
import threading
import time
import uuid

import httpx
import requests

from services.mtn_collection import (
    MtnCollectionClient,
    MtnCollectionError,
    PRODUCTION_BASE_URL,
    PRODUCTION_CURRENCY,
    PRODUCTION_MAX_AMOUNT,
)


PRODUCTION_TARGET_ENVIRONMENT = "mtnghana"
SANDBOX_BASE_URL = "https://sandbox.momodeveloper.mtn.com"
SANDBOX_TARGET_ENVIRONMENT = "sandbox"
DEFAULT_SANDBOX_SUBSCRIPTION_KEY = "c92534d80b9f404b833fff6d5be2cfc5"
DEFAULT_SANDBOX_CALLBACK_URL = ""
DEFAULT_PRODUCTION_CALLBACK_URL = "http://127.0.0.1/"
DEFAULT_CALLBACK_URL = DEFAULT_SANDBOX_CALLBACK_URL
SANDBOX_MAX_AMOUNT = Decimal("10000.00")

DISBURSEMENT_ENVIRONMENTS = {
    "production": {
        "base_url": PRODUCTION_BASE_URL,
        "target_environment": PRODUCTION_TARGET_ENVIRONMENT,
        "credential_prefix": "MTN_PRODUCTION_DISBURSEMENT",
        "currency": PRODUCTION_CURRENCY,
        "max_amount": PRODUCTION_MAX_AMOUNT,
        "default_callback": DEFAULT_PRODUCTION_CALLBACK_URL,
    },
    "sandbox": {
        "base_url": SANDBOX_BASE_URL,
        "target_environment": SANDBOX_TARGET_ENVIRONMENT,
        "credential_prefix": "MTN_SANDBOX_DISBURSEMENT",
        "currency": "EUR",
        "max_amount": SANDBOX_MAX_AMOUNT,
        "default_callback": DEFAULT_SANDBOX_CALLBACK_URL,
    },
}


class MtnDisbursementError(MtnCollectionError):
    pass


def _resolve_credentials(environment):
    prefix = DISBURSEMENT_ENVIRONMENTS[environment]["credential_prefix"]
    sub_key = os.getenv(f"{prefix}_SUBSCRIPTION_KEY", "").strip()
    api_user = os.getenv(f"{prefix}_API_USER", "").strip()
    api_key = os.getenv(f"{prefix}_API_KEY", "").strip()

    if environment == "sandbox":
        if not sub_key:
            sub_key = (
                os.getenv("MTN_DISBURSEMENT_SUBSCRIPTION_KEY", "").strip()
                or os.getenv("MTN_PRODUCTION_DISBURSEMENT_SUBSCRIPTION_KEY", "").strip()
                or DEFAULT_SANDBOX_SUBSCRIPTION_KEY
            )
        if not api_user:
            api_user = (
                os.getenv("MTN_DISBURSEMENT_API_USER", "").strip()
                or os.getenv("MTN_PRODUCTION_DISBURSEMENT_API_USER", "").strip()
            )
        if not api_key:
            api_key = (
                os.getenv("MTN_DISBURSEMENT_API_KEY", "").strip()
                or os.getenv("MTN_PRODUCTION_DISBURSEMENT_API_KEY", "").strip()
            )
    return sub_key, api_user, api_key


def normalize_disbursement_amount(amount, environment=None):
    if environment is None:
        environment = os.getenv(
            "MTN_DISBURSEMENT_ENVIRONMENT", "sandbox"
        ).strip().lower()
    if environment not in DISBURSEMENT_ENVIRONMENTS:
        raise ValueError("MTN Disbursement environment must be production or sandbox.")
    normalized = MtnCollectionClient._normalize_amount(amount)
    maximum = DISBURSEMENT_ENVIRONMENTS[environment]["max_amount"]
    if maximum is not None and Decimal(normalized) > maximum:
        currency = DISBURSEMENT_ENVIRONMENTS[environment]["currency"]
        raise ValueError(
            f"MTN {environment} transfers are limited to "
            f"{currency} {format(maximum, 'f')}."
        )
    return normalized


class MtnDisbursementClient:
    def __init__(
        self,
        subscription_key,
        api_user,
        api_key,
        http_client=None,
        clock=time.monotonic,
        environment="production",
    ):
        if environment not in DISBURSEMENT_ENVIRONMENTS:
            raise ValueError("MTN Disbursement environment must be production or sandbox.")
        environment_config = DISBURSEMENT_ENVIRONMENTS[environment]
        self.subscription_key = subscription_key
        self.api_user = api_user
        self.api_key = api_key
        self.environment = environment
        self.base_url = environment_config["base_url"]
        self.target_environment = environment_config["target_environment"]
        self.currency = environment_config["currency"]
        default_callback = environment_config["default_callback"]
        self.callback_url = (
            os.getenv("MTN_DISBURSEMENT_CALLBACK_URL", "").strip()
            or default_callback
        )
        self.http_client = http_client or httpx.Client(timeout=15.0)
        self.clock = clock
        self._token = None
        self._token_expires_at = 0
        self._token_lock = threading.Lock()

    @classmethod
    def from_environment(cls):
        environment = os.getenv("MTN_DISBURSEMENT_ENVIRONMENT", "sandbox").strip().lower()
        if environment not in DISBURSEMENT_ENVIRONMENTS:
            raise MtnDisbursementError(
                "MTN_DISBURSEMENT_ENVIRONMENT must be either 'production' or 'sandbox'.",
                status_code=500,
            )
        prefix = DISBURSEMENT_ENVIRONMENTS[environment]["credential_prefix"]
        names = (
            f"{prefix}_SUBSCRIPTION_KEY",
            f"{prefix}_API_USER",
            f"{prefix}_API_KEY",
        )
        sub_key, api_user, api_key = _resolve_credentials(environment)
        missing = []
        if not sub_key:
            missing.append(names[0])
        if not api_user:
            missing.append(names[1])
        if not api_key:
            missing.append(names[2])
        if (
            environment == "sandbox"
            and not os.getenv("MTN_DISBURSEMENT_CALLBACK_URL", "").strip()
        ):
            missing.append("MTN_DISBURSEMENT_CALLBACK_URL")

        if missing:
            raise MtnDisbursementError(
                f"MTN {environment} Disbursement is not configured. Missing backend "
                f"environment variables: {', '.join(missing)}.",
                status_code=503,
            )
        return cls(sub_key, api_user, api_key, environment=environment)

    def _send(self, method, path, operation, **kwargs):
        reference_id = kwargs.get("headers", {}).get("X-Reference-Id")
        try:
            response = self.http_client.request(
                method,
                f"{self.base_url}{path}",
                **kwargs,
            )
        except httpx.TimeoutException as error:
            raise MtnDisbursementError(
                f"MTN {self.environment} timed out during {operation}. Check the "
                "transfer status before submitting another transfer.",
                status_code=504,
                reference_id=reference_id,
            ) from error
        except httpx.RequestError as error:
            raise MtnDisbursementError(
                f"Could not connect to MTN {self.environment} during {operation}.",
                status_code=502,
                reference_id=reference_id,
            ) from error

        if response.is_error:
            error_detail = ""
            try:
                err_json = response.json()
                if isinstance(err_json, dict):
                    err_code = err_json.get("code") or err_json.get("statusCode")
                    err_msg = err_json.get("message") or err_json.get("description")
                    if err_code and err_msg:
                        error_detail = f": {err_code} - {err_msg}"
                    elif err_msg:
                        error_detail = f": {err_msg}"
                    elif err_code:
                        error_detail = f": {err_code}"
            except Exception:
                if response.text and len(response.text.strip()) < 200:
                    error_detail = f": {response.text.strip()}"

            if response.status_code in (401, 403):
                message = (
                    f"MTN {self.environment} Disbursement authentication failed. "
                    f"Check the Disbursement API user, API key, and subscription key{error_detail}."
                )
            else:
                message = (
                    f"MTN {self.environment} returned HTTP {response.status_code} "
                    f"during {operation}{error_detail}."
                )
            raise MtnDisbursementError(
                message,
                status_code=response.status_code,
                reference_id=reference_id,
            )
        return response

    def _access_token(self):
        with self._token_lock:
            if self._token and self._token_expires_at > self.clock():
                return self._token

            url = f"{self.base_url}/disbursement/token/"
            auth_string = f"{self.api_user}:{self.api_key}"
            encoded_auth = base64.b64encode(
                auth_string.encode("utf-8")
            ).decode("utf-8")
            headers = {
                "Authorization": f"Basic {encoded_auth}",
                "Ocp-Apim-Subscription-Key": self.subscription_key,
            }
            if self.environment == "sandbox":
                headers["Target-Environment"] = self.target_environment

            debug_headers = {
                **headers,
                "Authorization": "<redacted>",
                "Ocp-Apim-Subscription-Key": "<redacted>",
            }
            print(f"MTN token request: url={url}, headers={debug_headers}")
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    data="",
                    timeout=15.0,
                )
            except requests.Timeout as error:
                raise MtnDisbursementError(
                    f"MTN {self.environment} timed out during authentication.",
                    status_code=504,
                ) from error
            except requests.RequestException as error:
                raise MtnDisbursementError(
                    f"Could not connect to MTN {self.environment} during authentication.",
                    status_code=502,
                ) from error

            if response.status_code >= 400:
                error_detail = ""
                try:
                    err_json = response.json()
                    if isinstance(err_json, dict):
                        err_code = err_json.get("code") or err_json.get("statusCode")
                        err_msg = err_json.get("message") or err_json.get("description")
                        if err_code and err_msg:
                            error_detail = f": {err_code} - {err_msg}"
                        elif err_msg:
                            error_detail = f": {err_msg}"
                        elif err_code:
                            error_detail = f": {err_code}"
                except Exception:
                    if response.text and len(response.text.strip()) < 200:
                        error_detail = f": {response.text.strip()}"

                if response.status_code in (401, 403):
                    message = (
                        f"MTN {self.environment} Disbursement authentication failed. "
                        f"Check the Disbursement API user, API key, and subscription key{error_detail}."
                    )
                else:
                    message = (
                        f"MTN {self.environment} returned HTTP {response.status_code} "
                        f"during authentication{error_detail}."
                    )
                raise MtnDisbursementError(
                    message,
                    status_code=response.status_code,
                )

            try:
                payload = response.json()
                token = payload["access_token"]
                expires_in = float(payload["expires_in"])
            except (ValueError, KeyError, TypeError) as error:
                raise MtnDisbursementError(
                    f"MTN {self.environment} returned an invalid access-token response."
                ) from error

            if not isinstance(token, str) or not token or expires_in <= 0:
                raise MtnDisbursementError(
                    f"MTN {self.environment} returned an invalid access-token response."
                )

            self._token = token
            self._token_expires_at = self.clock() + max(expires_in - 30, 1)
            return token

    def normalize_amount(self, amount):
        return normalize_disbursement_amount(amount, environment=self.environment)

    @staticmethod
    def _normalize_reference(reference_id):
        try:
            return str(uuid.UUID(reference_id)).lower() if reference_id else str(uuid.uuid4()).lower()
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid MTN transfer reference.") from error

    @staticmethod
    def _normalize_external_id(external_id):
        if external_id is None:
            return str(uuid.uuid4()).lower()
        if isinstance(external_id, (int, float)):
            external_id = str(external_id)
        if isinstance(external_id, str):
            clean = external_id.strip()
            if not clean:
                return str(uuid.uuid4()).lower()
            try:
                return str(uuid.UUID(clean)).lower()
            except (ValueError, TypeError, AttributeError):
                return clean[:64]
        return str(uuid.uuid4()).lower()

    def transfer(
        self,
        amount,
        recipient_msisdn,
        external_id=None,
        payer_message="MoMo Sentinel transfer",
        payee_note="MoMo Sentinel transfer",
    ):
        normalized_amount = self.normalize_amount(amount)
        normalized_msisdn = MtnCollectionClient._normalize_msisdn(recipient_msisdn)
        reference_id = str(uuid.uuid4()).lower()
        external_id = self._normalize_external_id(external_id)
        token = self._access_token()

        response = self._send(
            "POST",
            "/disbursement/v1_0/transfer",
            "transfer request",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Callback-Url": self.callback_url,
                "X-Reference-Id": reference_id,
                "X-Target-Environment": self.target_environment,
                "Ocp-Apim-Subscription-Key": self.subscription_key,
                "Content-Type": "application/json",
            },
            json={
                "amount": normalized_amount,
                "currency": self.currency,
                "externalId": external_id,
                "payee": {
                    "partyIdType": "MSISDN",
                    "partyId": normalized_msisdn,
                },
                "payerMessage": payer_message,
                "payeeNote": payee_note,
            },
        )
        if response.status_code != 202:
            raise MtnDisbursementError(
                f"MTN {self.environment} did not accept the transfer request "
                f"(HTTP {response.status_code})."
            )
        return {
            "reference_id": reference_id,
            "status": "PENDING",
            "amount": normalized_amount,
            "currency": self.currency,
            "recipient_msisdn": normalized_msisdn,
        }

    def get_transfer_status(self, reference_id):
        reference_id = self._normalize_reference(reference_id)
        token = self._access_token()
        response = self._send(
            "GET",
            f"/disbursement/v1_0/transfer/{reference_id}",
            "transfer status check",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Target-Environment": self.target_environment,
                "Ocp-Apim-Subscription-Key": self.subscription_key,
            },
        )
        try:
            payload = response.json()
            status = payload["status"]
        except (ValueError, KeyError, TypeError) as error:
            raise MtnDisbursementError(
                f"MTN {self.environment} returned an invalid transfer-status response."
            ) from error

        if not isinstance(status, str) or not status:
            raise MtnDisbursementError(
                f"MTN {self.environment} returned an invalid transfer-status response."
            )

        result = {"reference_id": reference_id, "status": status}
        for field in ("amount", "currency", "externalId", "financialTransactionId", "reason"):
            value = payload.get(field)
            if isinstance(value, (str, int, float)):
                result[field] = value
        return result


def get_configuration_status():
    environment = os.getenv("MTN_DISBURSEMENT_ENVIRONMENT", "sandbox").strip().lower()
    if environment not in DISBURSEMENT_ENVIRONMENTS:
        return {
            "configured": False,
            "missing_variables": ["MTN_DISBURSEMENT_ENVIRONMENT"],
            "environment": "invalid",
            "currency": PRODUCTION_CURRENCY,
            "maximum_amount": None,
        }
    prefix = DISBURSEMENT_ENVIRONMENTS[environment]["credential_prefix"]
    names = (
        f"{prefix}_SUBSCRIPTION_KEY",
        f"{prefix}_API_USER",
        f"{prefix}_API_KEY",
    )
    sub_key, api_user, api_key = _resolve_credentials(environment)
    missing = []
    if not sub_key:
        missing.append(names[0])
    if not api_user:
        missing.append(names[1])
    if not api_key:
        missing.append(names[2])
    if (
        environment == "sandbox"
        and not os.getenv("MTN_DISBURSEMENT_CALLBACK_URL", "").strip()
    ):
        missing.append("MTN_DISBURSEMENT_CALLBACK_URL")

    if len(os.getenv("MOMO_ADMIN_TOKEN", "")) < 32:
        missing.append("MOMO_ADMIN_TOKEN")

    return {
        "configured": not missing,
        "missing_variables": missing,
        "environment": environment,
        "currency": DISBURSEMENT_ENVIRONMENTS[environment]["currency"],
        "maximum_amount": (
            format(DISBURSEMENT_ENVIRONMENTS[environment]["max_amount"], "f")
            if DISBURSEMENT_ENVIRONMENTS[environment]["max_amount"] is not None
            else None
        ),
    }


_client = None
_client_lock = threading.Lock()


def get_mtn_disbursement_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = MtnDisbursementClient.from_environment()
        return _client
