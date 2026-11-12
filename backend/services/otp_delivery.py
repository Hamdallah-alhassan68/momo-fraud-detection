"""Shared OTP delivery errors and keyed one-time-code verification."""

import hashlib
import hmac
import secrets


class OtpDeliveryError(RuntimeError):
    """Raised when a security-sensitive one-time code cannot be delivered."""


def hash_one_time_code(code, context):
    """Hash a short-lived code with the server secret to prevent offline guessing."""
    from services.auth import _auth_secret

    digest = hmac.new(
        _auth_secret(),
        f"{context}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def verify_one_time_code(code, stored_hash, context):
    """Constant-time verification for server-keyed one-time-code hashes."""
    if not isinstance(stored_hash, str) or not stored_hash.startswith("hmac-sha256:"):
        return False
    expected_hash = hash_one_time_code(code, context)
    return secrets.compare_digest(expected_hash, stored_hash)
