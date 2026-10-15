"""Authentication, authorization, and RBAC service for MoMo Sentinel."""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import database.database as database

logger = logging.getLogger(__name__)

# Default demo users with role-based access for local development and demonstration
DEFAULT_DEMO_USERS = [
    {
        "username": "admin",
        "display_name": "System Administrator",
        "password": "Admin@MoMo2026!",
        "role": "admin",
        "description": "Full administrative control (account freezes, user management, audit verification, investigation override)",
    },
    {
        "username": "analyst_kwame",
        "display_name": "Kwame Mensah (Lead Fraud Analyst)",
        "password": "Analyst@MoMo2026!",
        "role": "analyst",
        "description": "Case investigations, simulated customer contact, direct transaction scoring, fraud resolution",
    },
    {
        "username": "viewer_ama",
        "display_name": "Ama Osei (Compliance Viewer)",
        "password": "Viewer@MoMo2026!",
        "role": "viewer",
        "description": "Read-only access for regulatory compliance review, audit trail inspection, and drift monitoring",
    },
]


def _auth_secret() -> bytes:
    """Retrieve or generate the secret key used for signing session tokens."""
    configured = os.getenv("MOMO_AUTH_SECRET")
    if configured:
        return configured.encode("utf-8")

    # Persist an ephemeral key on disk across worker restarts during development if not explicitly configured
    token_file = database.BASE_DIR / ".auth_secret"
    try:
        if token_file.exists():
            content = token_file.read_bytes().strip()
            if len(content) >= 32:
                return content
        new_secret = secrets.token_hex(32).encode("utf-8")
        token_file.write_bytes(new_secret)
        return new_secret
    except Exception:
        if not hasattr(_auth_secret, "_fallback_secret"):
            _auth_secret._fallback_secret = secrets.token_hex(32).encode("utf-8")
        return _auth_secret._fallback_secret


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with 100,000 iterations and a random salt."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    )
    return f"pbkdf2:sha256:100000${salt}${key.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash."""
    if not plain_password or not hashed_password:
        return False
    try:
        parts = hashed_password.split("$")
        if len(parts) != 3:
            return False
        algo_iter, salt, expected_hex = parts
        prefix, algo, iter_str = algo_iter.split(":")
        if prefix != "pbkdf2" or algo != "sha256":
            return False
        iterations = int(iter_str)
        computed = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return hmac.compare_digest(computed.hex(), expected_hex)
    except Exception:
        return False


def create_access_token(
    user_id: int,
    username: str,
    role: str,
    display_name: str | None = None,
    expires_in_seconds: int = 86400,
) -> str:
    """Generate a tamper-evident HMAC-SHA256 signed bearer token."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "display_name": display_name or username,
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_payload).decode("utf-8").rstrip("=")
    signature = hmac.new(
        _auth_secret(),
        b64_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{b64_payload}.{signature}"


def decode_access_token(token: str) -> dict | None:
    """Validate token signature and expiration, returning the user payload if valid."""
    if not token or not isinstance(token, str):
        return None
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        b64_payload, signature = parts
        expected_sig = hmac.new(
            _auth_secret(),
            b64_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None

        # Add base64 padding if needed
        padded = b64_payload + "=" * (-len(b64_payload) % 4)
        raw_payload = base64.urlsafe_b64decode(padded)
        payload = json.loads(raw_payload.decode("utf-8"))

        # Verify expiration
        exp = payload.get("exp", 0)
        if exp < time.time():
            return None
        return payload
    except Exception:
        return None


def authenticate_user(username: str, password: str) -> tuple[dict | None, str | None]:
    """Authenticate a named admin/analyst/viewer user.

    Returns:
        (user_dict, None) on success
        (None, error_message) on failure
    """
    if not username or not password:
        return None, "Username and password are required."

    user = database.get_admin_user_by_username(username)
    if not user:
        return None, "Invalid username or password."

    if not user.get("is_active", 1):
        return None, "User account is deactivated. Contact an administrator."

    # Check account lockout
    locked_until_str = user.get("locked_until")
    if locked_until_str:
        try:
            locked_until = datetime.fromisoformat(locked_until_str)
            if datetime.now(timezone.utc) < locked_until:
                return None, f"Account is temporarily locked until {locked_until_str}. Try again later."
        except Exception:
            pass

    # Verify password hash
    if not verify_password(password, user.get("password_hash", "")):
        database.record_admin_login_failure(username)
        return None, "Invalid username or password."

    # Success: reset failed attempts & record login
    database.record_admin_login_success(user["id"])
    sanitized = {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": user.get("role", "viewer"),
        "last_login_at": datetime.now(timezone.utc).isoformat(),
    }
    return sanitized, None


def seed_default_admin_users() -> list[str]:
    """Ensure default role-based demo user accounts exist if the table is empty."""
    count = database.count_admin_users()
    if count > 0:
        return []

    seeded = []
    for user_info in DEFAULT_DEMO_USERS:
        pwd_hash = hash_password(user_info["password"])
        database.create_admin_user(
            username=user_info["username"],
            display_name=user_info["display_name"],
            password_hash=pwd_hash,
            role=user_info["role"],
        )
        seeded.append(user_info["username"])

    logger.info("Seeded %d default admin users: %s", len(seeded), ", ".join(seeded))
    return seeded
