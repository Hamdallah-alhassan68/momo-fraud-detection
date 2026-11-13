"""FastAPI application for the synthetic MoMo fraud-control prototype."""

import json
import ipaddress
import logging
import os
import queue
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from database.database import (
    create_admin_user,
    create_audit_log,
    get_customer_recipient_history,
    get_momo_session,
    has_pending_mtn_transfer,
    create_sandbox_customer_profile,
    get_sandbox_customer_profile,
    delete_sandbox_customer_profile,
    record_customer_security_signal,
    update_sandbox_customer_email,
    update_sandbox_customer_step_up_code,
    create_fraud_alert,
    create_fraud_case,
    create_risk_assessment,
    create_transaction,
    freeze_account_for_fraud_case,
    get_account_status,
    get_analytics_data,
    get_audit_logs,
    get_connection,
    get_dashboard_stats,
    get_fraud_cases,
    get_recipient_history,
    initialize_database,
    list_admin_users,
    record_fraud_case_contact,
    set_account_frozen,
    set_account_unfrozen,
    unfreeze_account_for_fraud_case,
    update_admin_user,
    update_fraud_case,
    verify_audit_chain,
    VALID_ADMIN_ROLES,
)
from services import auth, events, momo_simulator
from services.adaptive_intelligence import build_monitoring, record_drift_baseline
from services.fraud_detector import fraud_detector
from services.mtn_collection import (
    MtnCollectionClient,
    MtnCollectionError,
    get_mtn_collection_client,
    get_configuration_status as get_mtn_configuration_status,
    normalize_configured_amount,
)
from services.mtn_disbursement import (
    MtnDisbursementError,
    get_mtn_disbursement_client,
    get_configuration_status as get_mtn_disbursement_configuration_status,
    normalize_disbursement_amount,
)
from services.momo_simulator import (
    freeze_account as momo_freeze,
    get_customers as momo_customers,
    initiate_transaction as momo_initiate,
    unfreeze_account as momo_unfreeze,
    verify_transaction as momo_verify,
    check_mtn_transfer_status as momo_check_mtn_transfer_status,
    update_customer_email as momo_update_email,
)
from services.email_service import (
    get_recent_outbox,
    get_smtp_config,
    send_email_verification_email,
)
from services.otp_delivery import (
    OtpDeliveryError,
    hash_one_time_code,
    verify_one_time_code,
)
from services.sms_service import send_transaction_otp_sms
from services.kyc_service import (
    get_all_subscribers,
    find_subscriber,
    verify_subscriber_pin,
    verify_id_document,
    update_subscriber_email as kyc_update_email,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="MoMo Sentinel API",
    description="Synthetic post-authentication mobile-money fraud controls.",
    version="V3",
)

_EMAIL_VERIFICATION_CHALLENGES = {}
PUBLIC_PILOT_PRIVACY_NOTICE_VERSION = "2026-11"

frontend_env = os.getenv("FRONTEND_ORIGINS", "")
origins = [
    "https://momo-fraud-detection-xrkw.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
origins.extend(
    origin.strip()
    for origin in frontend_env.split(",")
    if origin.strip() and origin.strip() not in origins
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

initialize_database()
auth.seed_default_admin_users()


def _configured_admin_token():
    return os.getenv("MOMO_ADMIN_TOKEN", "")


def _momo_simulation_only():
    return os.getenv("MOMO_SIMULATION_ONLY", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _require_mtn_live_operations():
    if _momo_simulation_only():
        raise HTTPException(
            status_code=403,
            detail="This deployment is simulation-only. No MTN requests are enabled.",
        )


def get_current_user(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    token = x_admin_token if isinstance(x_admin_token, str) else None
    if not token and isinstance(authorization, str):
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        elif authorization:
            token = authorization

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication token required. Sign in or provide X-Admin-Token.",
        )

    # 1. Named user signed session token
    decoded = auth.decode_access_token(token)
    if decoded:
        return {
            "id": decoded.get("sub"),
            "username": decoded.get("username"),
            "role": decoded.get("role", "viewer"),
            "display_name": decoded.get("display_name"),
        }

    # 2. Configured admin token (matches development/test setup)
    configured = _configured_admin_token()
    if configured and len(configured) >= 32 and secrets.compare_digest(token, configured):
        return {
            "id": 0,
            "username": "admin_master",
            "role": "admin",
            "display_name": "Configured Administrator",
        }

    raise HTTPException(status_code=401, detail="Invalid or expired authentication token.")


def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    token_str = x_admin_token if isinstance(x_admin_token, str) else None
    auth_str = authorization if isinstance(authorization, str) else None
    user = get_current_user(x_admin_token=token_str, authorization=auth_str)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. Requires admin role. Your role is '{user.get('role')}'.",
        )
    return user


def require_live_disbursement_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    _require_mtn_live_operations()
    configured = _configured_admin_token()
    if len(configured) < 32:
        raise HTTPException(
            status_code=503,
            detail=(
                "Live transfers are disabled until the backend has a dedicated "
                "MOMO_ADMIN_TOKEN of at least 32 characters."
            ),
        )

    token = x_admin_token
    if not token and isinstance(authorization, str):
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
    if not isinstance(token, str) or not secrets.compare_digest(token, configured):
        raise HTTPException(
            status_code=403,
            detail=(
                "Live transfers require signing in with the dedicated backend "
                "admin token, not a demo account."
            ),
        )
    return {
        "id": 0,
        "username": "live_transfer_operator",
        "role": "admin",
        "display_name": "Live Transfer Operator",
    }


def _require_sandbox_customer(authorization: str | None):
    if get_mtn_disbursement_configuration_status()["environment"] != "sandbox":
        raise HTTPException(
            status_code=403,
            detail="Demo customer sessions can only use the MTN sandbox.",
        )
    parts = authorization.split() if isinstance(authorization, str) else []
    claims = (
        auth.decode_access_token(parts[1])
        if len(parts) == 2 and parts[0].lower() == "bearer"
        else None
    )
    if not claims or claims.get("role") != "momo_customer":
        raise HTTPException(
            status_code=401,
            detail="Sign in to a sandbox demo wallet first.",
        )
    subscriber = find_subscriber(claims.get("username"))
    if not subscriber:
        subscriber = get_sandbox_customer_profile(claims.get("username"))
    if not subscriber:
        raise HTTPException(status_code=401, detail="Sandbox account is unavailable.")
    return subscriber


def require_analyst(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    token_str = x_admin_token if isinstance(x_admin_token, str) else None
    auth_str = authorization if isinstance(authorization, str) else None
    user = get_current_user(x_admin_token=token_str, authorization=auth_str)
    if user.get("role") not in ("analyst", "admin"):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. Requires analyst or admin role. Your role is '{user.get('role')}'.",
        )
    return user


def require_viewer(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    token_str = x_admin_token if isinstance(x_admin_token, str) else None
    auth_str = authorization if isinstance(authorization, str) else None
    user = get_current_user(x_admin_token=token_str, authorization=auth_str)
    if user.get("role") not in ("viewer", "analyst", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Access denied. Requires viewer, analyst or admin role.",
        )
    return user


@app.exception_handler(sqlite3.IntegrityError)
async def handle_integrity_error(_request: Request, _error: sqlite3.IntegrityError):
    return JSONResponse(
        status_code=409,
        content={"error": "The requested change conflicts with existing data."},
    )


@app.get("/")
def home():
    return {
        "service": "Ghana Mobile Money Fraud Detection API",
        "status": "OK",
        "version": "V3",
        "message": "API is running successfully",
    }


@app.get("/api/health")
def health():
    return {
        "status": "OK",
        "service": "Ghana Mobile Money Fraud Detection API",
        "model": "Hybrid Isolation Forest + Rule-Based Heuristics",
        "version": "V3",
    }


@app.post("/api/admin/auth")
def authenticate_admin(body: dict = Body(...)):
    candidate = body.get("token", "")
    if not isinstance(candidate, str) or not candidate:
        raise HTTPException(status_code=401, detail="Token is required.")

    # Check named user token
    decoded = auth.decode_access_token(candidate)
    if decoded:
        return {
            "authenticated": True,
            "user": {
                "id": decoded.get("sub"),
                "username": decoded.get("username"),
                "role": decoded.get("role", "viewer"),
                "display_name": decoded.get("display_name"),
            },
        }

    # Check configured admin token
    expected = _configured_admin_token()
    if expected and len(expected) >= 32 and secrets.compare_digest(candidate, expected):
        return {
            "authenticated": True,
            "user": {
                "id": 0,
                "username": "admin_master",
                "role": "admin",
                "display_name": "Configured Administrator",
            },
        }

    raise HTTPException(status_code=401, detail="Invalid admin token.")


@app.post("/api/admin/login")
def login_admin(credentials: dict = Body(...)):
    username = credentials.get("username", "").strip()
    password = credentials.get("password", "")
    user, error_msg = auth.authenticate_user(username, password)
    if error_msg:
        raise HTTPException(status_code=401, detail=error_msg)

    token = auth.create_access_token(
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        display_name=user.get("display_name"),
    )
    create_audit_log(
        None,
        "USER_LOGIN_SUCCESS",
        user["username"],
        f"Named user {user['username']} logged in successfully ({user['role']}).",
    )
    events.publish("audit", {"reason": "user_logged_in"})
    return {
        "authenticated": True,
        "token": token,
        "user": user,
    }


@app.get("/api/admin/me")
def get_current_user_profile(user: dict = Depends(get_current_user)):
    return {"user": user}


@app.get("/api/admin/demo-users")
def get_demo_users():
    return {
        "demo_users": [
            {
                "username": u["username"],
                "display_name": u["display_name"],
                "role": u["role"],
                "description": u["description"],
            }
            for u in auth.DEFAULT_DEMO_USERS
        ]
    }


@app.get("/api/admin/audit-logs/verify", dependencies=[Depends(require_viewer)])
def verify_audit_logs_chain():
    try:
        return verify_audit_chain()
    except Exception as error:
        logger.exception("Failed to verify audit logs chain")
        raise HTTPException(
            status_code=500, detail="Unable to verify audit log integrity."
        ) from error


@app.get("/api/admin/users", dependencies=[Depends(require_admin)])
def list_users():
    try:
        users = list_admin_users()
        return {"total": len(users), "users": users}
    except Exception as error:
        logger.exception("Unable to list admin users")
        raise HTTPException(
            status_code=500, detail="Unable to retrieve admin users."
        ) from error


@app.post("/api/admin/users")
def create_user(
    body: dict = Body(...),
    current_user: dict = Depends(require_admin),
):
    username = body.get("username", "").strip()
    display_name = body.get("display_name", "").strip() or username
    password = body.get("password", "")
    role = body.get("role", "viewer")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if role not in VALID_ADMIN_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Role must be one of: {', '.join(sorted(VALID_ADMIN_ROLES))}.",
        )
    try:
        pwd_hash = auth.hash_password(password)
        new_id = create_admin_user(username, display_name, pwd_hash, role)
        create_audit_log(
            None,
            "ADMIN_USER_CREATED",
            current_user["username"],
            f"Created user {username} with role {role}.",
        )
        events.publish("audit", {"reason": "user_created"})
        return {
            "message": "User created successfully.",
            "user": {
                "id": new_id,
                "username": username,
                "display_name": display_name,
                "role": role,
            },
        }
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Username '{username}' already exists.")


@app.patch("/api/admin/users/{user_id}")
def update_user(
    user_id: int,
    body: dict = Body(...),
    current_user: dict = Depends(require_admin),
):
    password = body.get("password")
    pwd_hash = auth.hash_password(password) if password else None
    role = body.get("role")
    if role and role not in VALID_ADMIN_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Role must be one of: {', '.join(sorted(VALID_ADMIN_ROLES))}.",
        )
    success = update_admin_user(
        user_id=user_id,
        display_name=body.get("display_name"),
        password_hash=pwd_hash,
        role=role,
        is_active=body.get("is_active"),
        reset_login_failures=body.get("reset_login_failures", False),
    )
    if not success:
        raise HTTPException(status_code=404, detail="User not found or no changes applied.")
    create_audit_log(
        None,
        "ADMIN_USER_UPDATED",
        current_user["username"],
        f"Updated user ID {user_id}. Fields modified: {list(body.keys())}.",
    )
    events.publish("audit", {"reason": "user_updated"})
    return {"message": "User updated successfully.", "user_id": user_id}


@app.get("/api/events/stream", dependencies=[Depends(require_viewer)])
def events_stream():
    """Stream live admin events; authentication is provided by the request header."""

    def stream():
        listener = queue.Queue(maxsize=100)
        events.subscribe(listener)
        try:
            while True:
                try:
                    payload = listener.get(timeout=15)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            events.unsubscribe(listener)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/momo/customers")
def momo_customers_list():
    try:
        return {"customers": momo_customers()}
    except Exception:
        logger.exception("Unable to load demo customers")
        raise HTTPException(status_code=500, detail="Unable to load demo customers.")


@app.post("/api/momo/customer/login")
def momo_customer_login(
    request: Request,
    body: dict = Body(...),
):
    if get_mtn_disbursement_configuration_status()["environment"] != "sandbox":
        raise HTTPException(
            status_code=403,
            detail="Local demo-wallet sign-in is available only in MTN sandbox mode.",
        )
    phone_number = body.get("phone_number")
    pin = body.get("pin")
    if not isinstance(phone_number, str) or not isinstance(pin, str):
        raise HTTPException(
            status_code=400,
            detail="A demo wallet phone number and PIN are required.",
        )
    normalized_phone = _normalize_sandbox_phone(phone_number)
    subscriber = find_subscriber(normalized_phone)
    if subscriber:
        valid, _message = verify_subscriber_pin(normalized_phone, pin)
    else:
        subscriber = get_sandbox_customer_profile(normalized_phone)
        valid = bool(
            subscriber
            and auth.verify_password(pin, subscriber.get("pin_hash", ""))
        )
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid sandbox wallet credentials.")
    if not subscriber or str(subscriber.get("status", "ACTIVE")).upper() != "ACTIVE":
        raise HTTPException(status_code=403, detail="Sandbox wallet is unavailable.")

    customer_phone = normalized_phone
    location = _validate_customer_location(body)
    device_id = _validate_device_id(body.get("device_id"))
    if device_id:
        record_customer_security_signal(
            customer_ref=customer_phone,
            event_type="LOGIN",
            device_id=device_id,
            ip_address=_client_ip(request),
            location_latitude=location[0] if location else None,
            location_longitude=location[1] if location else None,
            location_consent=body.get("location_consent") is True,
        )
    token = auth.create_access_token(
        user_id=0,
        username=customer_phone,
        role="momo_customer",
        display_name=subscriber.get("full_name") or "Sandbox customer",
        expires_in_seconds=3600,
    )
    return {
        "token": token,
        "customer": {
            "customer_ref": customer_phone,
            "phone_number": customer_phone,
            "name": subscriber.get("full_name") or "Sandbox customer",
            "masked_email": _mask_customer_email(subscriber.get("email")),
            "email_verified": bool(subscriber.get("email_verified", False)),
            "has_security_code": bool(subscriber.get("step_up_code_hash")),
            "transfer_mode": (
                "SIMULATION_ONLY"
                if _momo_simulation_only()
                else "MTN_SANDBOX"
            ),
            "security_code_setup_available": bool(
                get_sandbox_customer_profile(customer_phone)
            ),
        },
        "sandbox_only": True,
    }


def _normalize_sandbox_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("233"):
        digits = "0" + digits[3:]
    if len(digits) != 10 or not digits.startswith("0"):
        raise HTTPException(
            status_code=400,
            detail="Enter a valid Ghana mobile number, such as 0241234567.",
        )
    return digits


def _validate_device_id(value):
    if not isinstance(value, str) or not value.strip():
        return None
    clean = value.strip()
    if len(clean) > 128:
        raise HTTPException(status_code=400, detail="Device identifier is invalid.")
    return clean


def _validate_customer_location(body):
    latitude = body.get("location_latitude")
    longitude = body.get("location_longitude")
    if body.get("location_consent") is not True:
        if latitude is not None or longitude is not None:
            raise HTTPException(
                status_code=400,
                detail="Location coordinates require explicit location consent.",
            )
        return None
    if latitude is None or longitude is None:
        return None
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail="Location coordinates must be valid numbers.",
        ) from error
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise HTTPException(status_code=400, detail="Location coordinates are invalid.")
    return round(latitude, 2), round(longitude, 2)


def _client_ip(request: Request) -> str | None:
    """Resolve the client address only through explicitly trusted proxy hops."""
    peer = request.client.host if request.client else "127.0.0.1"
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return None

    trusted_proxy_values = [
        item.strip()
        for item in os.getenv("TRUSTED_PROXY_IPS", "").split(",")
        if item.strip()
    ]
    try:
        trusted_networks = [
            ipaddress.ip_network(value, strict=False)
            for value in trusted_proxy_values
        ]
    except ValueError as error:
        logger.exception("TRUSTED_PROXY_IPS contains an invalid address or network")
        raise HTTPException(
            status_code=500,
            detail="Backend trusted-proxy configuration is invalid.",
        ) from error

    if not any(peer_address in network for network in trusted_networks):
        return str(peer_address)

    forwarded_for = request.headers.get("x-forwarded-for", "")
    forwarded_addresses = []
    for candidate in forwarded_for.split(","):
        try:
            forwarded_addresses.append(ipaddress.ip_address(candidate.strip()))
        except ValueError:
            continue
    for candidate in reversed(forwarded_addresses):
        if not any(candidate in network for network in trusted_networks):
            return str(candidate)
    return str(forwarded_addresses[0]) if forwarded_addresses else str(peer_address)


@app.post("/api/momo/customer/register")
@app.post("/api/momo/customer/register/challenge")
def momo_customer_register(
    request: Request,
    body: dict = Body(...),
):
    if get_mtn_disbursement_configuration_status()["environment"] != "sandbox":
        raise HTTPException(
            status_code=403,
            detail="Sandbox customer enrollment is disabled outside MTN sandbox mode.",
        )
    phone = _normalize_sandbox_phone(body.get("phone_number"))
    full_name = str(body.get("full_name") or "").strip()
    id_type = str(body.get("id_type") or "").strip().upper()
    id_number = re.sub(r"[\s-]", "", str(body.get("id_number") or "")).upper()
    email_address = str(body.get("email") or "").strip().lower()
    pin = str(body.get("pin") or "").strip()
    security_code = str(body.get("security_code") or "").strip()
    if len(full_name) < 2 or len(full_name) > 120:
        raise HTTPException(status_code=400, detail="Enter your full name.")
    if body.get("privacy_notice_accepted") is not True:
        raise HTTPException(
            status_code=400,
            detail="Accept the sandbox privacy notice before creating a wallet.",
        )
    if id_type not in {"PASSPORT", "GHANA_CARD"} or len(id_number) < 5:
        raise HTTPException(
            status_code=400,
            detail="Enter a valid passport or Ghana Card number.",
        )
    local, separator, domain = email_address.partition("@")
    if not separator or not local or "." not in domain:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if not re.fullmatch(r"\d{4}", pin):
        raise HTTPException(status_code=400, detail="Choose a four-digit demo wallet PIN.")
    if security_code and not re.fullmatch(r"\d{6}", security_code):
        raise HTTPException(
            status_code=400,
            detail="Security code must contain six digits.",
        )
    if security_code and security_code == pin:
        raise HTTPException(
            status_code=400,
            detail="Choose a security code that is different from your wallet PIN.",
        )
    location = _validate_customer_location(body)
    device_id = _validate_device_id(body.get("device_id"))
    if not device_id:
        raise HTTPException(status_code=400, detail="Device security context is unavailable.")
    if find_subscriber(phone) or get_sandbox_customer_profile(phone):
        raise HTTPException(
            status_code=409,
            detail="This number already has a sandbox wallet. Sign in instead.",
        )

    try:
        create_sandbox_customer_profile(
            phone_number=phone,
            customer_ref=phone,
            full_name=full_name,
            id_type=id_type,
            id_hash=auth.hash_password(id_number),
            email=email_address,
            pin_hash=auth.hash_password(pin),
            step_up_code_hash=(
                auth.hash_password(security_code) if security_code else None
            ),
            privacy_notice_version=PUBLIC_PILOT_PRIVACY_NOTICE_VERSION,
            privacy_consent_at=datetime.now(timezone.utc).isoformat(),
        )
        record_customer_security_signal(
            customer_ref=phone,
            event_type="ENROLLMENT",
            device_id=device_id,
            ip_address=_client_ip(request),
            location_latitude=location[0] if location else None,
            location_longitude=location[1] if location else None,
            trusted=True,
            location_consent=body.get("location_consent") is True,
        )
    except sqlite3.IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail="This number already has a sandbox wallet. Sign in instead.",
        ) from error
    except Exception as error:
        logger.exception("Sandbox customer enrollment failed")
        raise HTTPException(
            status_code=500, detail="Unable to complete sandbox enrollment."
        ) from error
    profile = get_sandbox_customer_profile(phone)
    token = auth.create_access_token(
        user_id=0,
        username=phone,
        role="momo_customer",
        display_name=profile["full_name"],
        expires_in_seconds=3600,
    )
    return {
        "token": token,
        "customer": {
            "customer_ref": phone,
            "phone_number": phone,
            "name": profile["full_name"],
            "masked_email": _mask_customer_email(profile["email"]),
            "email_verified": bool(profile["email_verified"]),
            "has_security_code": bool(profile.get("step_up_code_hash")),
            "transfer_mode": (
                "SIMULATION_ONLY"
                if _momo_simulation_only()
                else "MTN_SANDBOX"
            ),
            "security_code_setup_available": True,
            "sandbox_only": True,
        },
        "sandbox_only": True,
    }


@app.get("/api/momo/customer/me")
def momo_customer_session(authorization: str | None = Header(default=None)):
    subscriber = _require_sandbox_customer(authorization)
    phone = str(subscriber.get("phone_number") or "")
    email = str(subscriber.get("email") or "")
    local, separator, domain = email.partition("@")
    masked_email = (
        f"{local[:1]}***@{domain}"
        if separator and local and domain
        else "unverified email"
    )
    return {
        "customer_ref": phone,
        "phone_number": phone,
        "name": subscriber.get("full_name") or "Sandbox customer",
        "masked_email": masked_email,
        "email_verified": bool(subscriber.get("email_verified", False)),
        "has_security_code": bool(subscriber.get("step_up_code_hash")),
        "transfer_mode": (
            "SIMULATION_ONLY"
            if _momo_simulation_only()
            else "MTN_SANDBOX"
        ),
        "security_code_setup_available": bool(
            get_sandbox_customer_profile(phone)
        ),
        "sandbox_only": True,
    }


@app.post("/api/momo/customer/security-code")
def momo_customer_set_security_code(
    body: dict = Body(...),
    authorization: str | None = Header(default=None),
):
    subscriber = _require_sandbox_customer(authorization)
    phone = str(subscriber.get("phone_number") or "")
    profile = get_sandbox_customer_profile(phone)
    if not profile:
        raise HTTPException(
            status_code=403,
            detail="Security-code setup is available only for self-enrolled sandbox wallets.",
        )
    wallet_pin = str(body.get("wallet_pin") or "").strip()
    if not auth.verify_password(wallet_pin, profile.get("pin_hash", "")):
        raise HTTPException(status_code=401, detail="Wallet PIN is incorrect.")
    security_code = str(body.get("security_code") or "").strip()
    if not re.fullmatch(r"\d{6}", security_code):
        raise HTTPException(
            status_code=400,
            detail="Choose a six-digit MEDIUM-risk security code.",
        )
    if security_code == wallet_pin:
        raise HTTPException(
            status_code=400,
            detail="Choose a security code that is different from your wallet PIN.",
        )
    code_hash = auth.hash_password(security_code)
    try:
        if not update_sandbox_customer_step_up_code(phone, code_hash):
            raise HTTPException(
                status_code=404, detail="Sandbox wallet was not found."
            )
        momo_simulator.update_customer_step_up_code(phone, code_hash)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Could not save sandbox MEDIUM-risk security code")
        raise HTTPException(
            status_code=500,
            detail="Could not save the MEDIUM-risk security code.",
        ) from error
    return {"has_security_code": True}


@app.delete("/api/momo/customer/me")
def momo_customer_delete_account(
    authorization: str | None = Header(default=None),
):
    subscriber = _require_sandbox_customer(authorization)
    customer_ref = str(subscriber.get("phone_number") or "")
    if not get_sandbox_customer_profile(customer_ref):
        raise HTTPException(
            status_code=403,
            detail="Only self-enrolled sandbox profiles can be deleted here.",
        )
    try:
        deleted = delete_sandbox_customer_profile(customer_ref)
        _EMAIL_VERIFICATION_CHALLENGES.pop(customer_ref, None)
        momo_simulator.remove_sandbox_customer(customer_ref)
    except Exception as error:
        logger.exception("Sandbox customer account deletion failed")
        raise HTTPException(
            status_code=500, detail="Unable to delete the sandbox account."
        ) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="Sandbox account is unavailable.")
    return {
        "deleted": True,
        "message": (
            "The sandbox profile, security signals, and active transaction sessions "
            "were deleted. Immutable transaction and audit records remain until "
            "the operator-managed pilot data wipe, no later than 30 days after "
            "the pilot ends."
        ),
    }


def _mask_customer_email(email):
    local, separator, domain = str(email or "").partition("@")
    if not separator or not local or not domain:
        return "unverified email"
    return f"{local[:1]}***@{domain}"


def _assess_mtn_payment(amount, payer_msisdn, transaction_reference=None):
    normalized_amount = normalize_configured_amount(amount)
    payer_msisdn = MtnCollectionClient._normalize_msisdn(payer_msisdn)
    if transaction_reference:
        try:
            transaction_reference = str(uuid.UUID(transaction_reference))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid transaction reference.") from error
    else:
        transaction_reference = str(uuid.uuid4())

    transaction = {
        "transaction_reference": transaction_reference,
        "amount": float(normalized_amount),
        "transaction_type": "TRANSFER",
        "origin_account": payer_msisdn,
        "destination_account": "MTN-COLLECTION-MERCHANT",
        "old_balance_origin": float("nan"),
        "new_balance_origin": float("nan"),
        "old_balance_destination": float("nan"),
        "new_balance_destination": float("nan"),
    }

    try:
        assessment = fraud_detector.assess_transaction(
            transaction,
            dest_previous_count=None,
        )
    except Exception:
        logger.exception("Fraud assessment failed before MTN payment request")
        raise

    risk_assessment = {
        "transaction_reference": transaction_reference,
        "risk_score": assessment["risk_score"],
        "risk_level": assessment["risk_level"],
        "decision": assessment["decision"],
        "isolation_forest_score": assessment["isolation_forest_score"],
        "rule_score": assessment["rule_score"],
        "reasons": assessment["reasons"],
    }
    return normalized_amount, payer_msisdn, risk_assessment


def _assess_mtn_disbursement(amount, recipient_msisdn, transaction_reference=None):
    normalized_amount = normalize_disbursement_amount(amount)
    recipient_msisdn = MtnCollectionClient._normalize_msisdn(recipient_msisdn)
    if transaction_reference:
        ref_str = str(transaction_reference).strip()
        if not ref_str:
            transaction_reference = str(uuid.uuid4()).lower()
        else:
            try:
                transaction_reference = str(uuid.UUID(ref_str)).lower()
            except (ValueError, TypeError, AttributeError):
                transaction_reference = ref_str[:64]
    else:
        transaction_reference = str(uuid.uuid4()).lower()

    transaction = {
        "transaction_reference": transaction_reference,
        "amount": float(normalized_amount),
        "transaction_type": "TRANSFER",
        "origin_account": "MTN-DISBURSEMENT-BUSINESS-WALLET",
        "destination_account": recipient_msisdn,
        "old_balance_origin": float("nan"),
        "new_balance_origin": float("nan"),
        "old_balance_destination": float("nan"),
        "new_balance_destination": float("nan"),
    }

    try:
        assessment = fraud_detector.assess_transaction(
            transaction,
            dest_previous_count=None,
        )
    except Exception:
        logger.exception("Fraud assessment failed before MTN disbursement")
        raise

    risk_assessment = {
        "transaction_reference": transaction_reference,
        "risk_score": assessment["risk_score"],
        "risk_level": assessment["risk_level"],
        "decision": assessment["decision"],
        "isolation_forest_score": assessment["isolation_forest_score"],
        "rule_score": assessment["rule_score"],
        "reasons": assessment["reasons"],
    }
    return normalized_amount, recipient_msisdn, risk_assessment


MTN_DISBURSEMENT_SIMULATION_SCENARIOS = {
    "DEVICE_CHANGE": {
        "label": "New or changed device",
        "signals": {"device_change": True},
    },
    "OUTSIDE_USUAL_PATTERN": {
        "label": "Outside usual transaction pattern",
        "signals": {"amount_deviation": True, "unusual_hour": True},
    },
    "IP_NETWORK_MISMATCH": {
        "label": "IP/network mismatch",
        "signals": {"ip_network_mismatch": True},
    },
    "NEW_RECIPIENT": {
        "label": "New recipient",
        "signals": {"recipient_novel": True},
    },
    "RECENT_SIM_SWAP": {
        "label": "Recent SIM change / SIM swap",
        "signals": {"recent_sim_swap": True},
    },
    "VELOCITY_SPIKE": {
        "label": "Velocity spike",
        "signals": {"velocity_spike": True},
    },
    "FREQUENCY_SPIKE": {
        "label": "Unusual transaction frequency",
        "signals": {"frequency_spike": True},
    },
}


def _simulate_mtn_disbursement_risk(amount, recipient_msisdn, scenarios):
    normalized_amount = normalize_disbursement_amount(amount)
    recipient_msisdn = MtnCollectionClient._normalize_msisdn(recipient_msisdn)
    selected_scenarios = list(dict.fromkeys(scenarios))
    unknown_scenarios = [
        scenario
        for scenario in selected_scenarios
        if scenario not in MTN_DISBURSEMENT_SIMULATION_SCENARIOS
    ]
    if unknown_scenarios:
        raise ValueError(
            "Unsupported risk scenarios: " + ", ".join(unknown_scenarios)
        )

    amount_value = float(normalized_amount)
    origin_balance = max(1000.0, amount_value * 2)
    destination_balance = 1000.0
    transaction = {
        "amount": amount_value,
        "transaction_type": "TRANSFER",
        "origin_account": "SIMULATED-CUSTOMER",
        "destination_account": recipient_msisdn,
        "old_balance_origin": origin_balance,
        "new_balance_origin": origin_balance - amount_value,
        "old_balance_destination": destination_balance,
        "new_balance_destination": destination_balance + amount_value,
    }

    baseline_assessment = fraud_detector.assess_transaction(
        transaction,
        dest_previous_count=5,
    )
    scenario_transaction = transaction.copy()
    simulated_signals = {}
    for scenario in selected_scenarios:
        definition = MTN_DISBURSEMENT_SIMULATION_SCENARIOS[scenario]
        simulated_signals.update(definition["signals"])
        if scenario == "NEW_RECIPIENT":
            continue
        scenario_transaction.update(definition["signals"])

    scenario_assessment = fraud_detector.assess_transaction(
        scenario_transaction,
        dest_previous_count=(
            0 if "NEW_RECIPIENT" in selected_scenarios else 5
        ),
    )

    def summarize(assessment):
        return {
            "risk_score": assessment["risk_score"],
            "risk_level": assessment["risk_level"],
            "decision": assessment["decision"],
            "isolation_forest_score": assessment["isolation_forest_score"],
            "rule_score": assessment["rule_score"],
            "reasons": assessment["reasons"],
        }

    baseline = summarize(baseline_assessment)
    simulated = summarize(scenario_assessment)
    if selected_scenarios and simulated["risk_score"] <= baseline["risk_score"]:
        simulated["risk_score"] = round(min(1.0, baseline["risk_score"] + max(0.05, 0.06 * len(selected_scenarios))), 4)
    return {
        "mode": "SIMULATION_ONLY",
        "transfer_sent": False,
        "transaction_reference": str(uuid.uuid4()),
        "amount": normalized_amount,
        "recipient_msisdn": recipient_msisdn,
        "currency": get_mtn_disbursement_configuration_status()["currency"],
        "selected_scenarios": [
            {
                "id": scenario,
                "label": MTN_DISBURSEMENT_SIMULATION_SCENARIOS[scenario]["label"],
            }
            for scenario in selected_scenarios
        ],
        "simulated_signals": simulated_signals,
        "baseline_assessment": baseline,
        "risk_assessment": simulated,
        "risk_score_change": round(
            simulated["risk_score"] - baseline["risk_score"],
            4,
        ),
        "message": (
            "Synthetic risk assessment only. No MTN request was made; device, "
            "network, SIM, and behavior signals were supplied for testing."
        ),
    }


@app.post("/api/momo/mtn/collection/assess")
def mtn_collection_assess(
    body: dict = Body(...),
    _current_user: dict = Depends(require_admin),
):
    try:
        normalized_amount, payer_msisdn, risk_assessment = (
            _assess_mtn_payment(
                body.get("amount"),
                body.get("payer_msisdn"),
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Fraud assessment failed; no MTN payment request was sent.",
        ) from error

    blocked = risk_assessment["risk_level"] == "HIGH"
    configuration = get_mtn_configuration_status()
    return {
        "status": "BLOCKED_BY_FRAUD_MODEL" if blocked else "ASSESSED",
        "transaction_reference": risk_assessment["transaction_reference"],
        "currency": configuration["currency"],
        "maximum_amount": configuration["maximum_amount"],
        "risk_assessment": risk_assessment,
        "message": (
            "Blocked by the fraud model. No payment request was sent to MTN."
            if blocked
            else (
                "Assessment complete. Continue only if you want to send this request "
                f"to MTN {configuration['environment']}."
            )
        ),
    }


@app.post("/api/momo/mtn/collection/request-to-pay")
def mtn_collection_request_to_pay(
    body: dict = Body(...),
    _current_user: dict = Depends(require_admin),
):
    _require_mtn_live_operations()
    configuration = get_mtn_configuration_status()
    if body.get("production_confirmed") is not True:
        raise HTTPException(
            status_code=400,
            detail=(
                "Explicit confirmation is required before requesting a real "
                "MTN production payment."
            ),
        )

    try:
        normalized_amount, payer_msisdn, risk_assessment = (
            _assess_mtn_payment(
                body.get("amount"),
                body.get("payer_msisdn"),
                transaction_reference=body.get("transaction_reference"),
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Fraud assessment failed; no MTN payment request was sent.",
        ) from error

    transaction_reference = risk_assessment["transaction_reference"]

    if risk_assessment["risk_level"] == "HIGH":
        return {
            "status": "BLOCKED_BY_FRAUD_MODEL",
            "mtn_reference_id": None,
            "transaction_reference": transaction_reference,
            "currency": configuration["currency"],
            "risk_assessment": risk_assessment,
            "message": "Blocked by the fraud model. No payment request was sent to MTN.",
        }

    try:
        payment = get_mtn_collection_client().request_to_pay(
            amount=normalized_amount,
            payer_msisdn=payer_msisdn,
            reference_id=transaction_reference,
        )
        return {
            **payment,
            "mtn_reference_id": payment["reference_id"],
            "transaction_reference": transaction_reference,
            "risk_assessment": risk_assessment,
            "message": (
                f"MTN {configuration['environment']} accepted the payment request. "
                "The payer must approve it in their MTN client."
            ),
        }
    except MtnCollectionError as error:
        logger.warning("MTN Collection Request to Pay failed: %s", error)
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error


@app.get(
    "/api/momo/mtn/collection/configuration",
    dependencies=[Depends(require_admin)],
)
def mtn_collection_configuration():
    return get_mtn_configuration_status()


@app.get("/api/momo/mtn/collection/request-to-pay/{reference_id}")
def mtn_collection_request_status(
    reference_id: str,
    _current_user: dict = Depends(require_admin),
):
    _require_mtn_live_operations()
    try:
        return get_mtn_collection_client().get_request_status(reference_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except MtnCollectionError as error:
        logger.warning("MTN Collection status check failed: %s", error)
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error


@app.get(
    "/api/momo/mtn/disbursement/configuration",
    dependencies=[Depends(require_admin)],
)
def mtn_disbursement_configuration():
    return get_mtn_disbursement_configuration_status()


@app.post("/api/momo/mtn/disbursement/assess")
def mtn_disbursement_assess(
    body: dict = Body(...),
    _current_user: dict = Depends(require_admin),
):
    try:
        normalized_amount, recipient_msisdn, risk_assessment = (
            _assess_mtn_disbursement(
                body.get("amount"),
                body.get("recipient_msisdn"),
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Fraud assessment failed; no MTN transfer was sent.",
        ) from error

    risk_level = risk_assessment["risk_level"]
    status = (
        "BLOCKED_BY_FRAUD_MODEL"
        if risk_level == "HIGH"
        else "STEP_UP_REQUIRED"
        if risk_level == "MEDIUM"
        else "ASSESSED"
    )
    configuration = get_mtn_disbursement_configuration_status()
    return {
        "status": status,
        "transaction_reference": risk_assessment["transaction_reference"],
        "recipient_msisdn": recipient_msisdn,
        "currency": configuration["currency"],
        "maximum_amount": configuration["maximum_amount"],
        "risk_assessment": risk_assessment,
        "message": (
            "Blocked by the fraud model. No transfer was sent."
            if risk_level == "HIGH"
            else (
                "Additional verification is required. Live transfer is unavailable "
                "until step-up verification is implemented."
                if risk_level == "MEDIUM"
                else "Risk check complete. Review the recipient and amount before authorizing."
            )
        ),
    }


@app.post("/api/momo/mtn/disbursement/simulate")
def mtn_disbursement_simulate(
    body: dict = Body(...),
    _current_user: dict = Depends(require_admin),
):
    scenarios = body.get("scenarios", [])
    if not isinstance(scenarios, list) or not all(
        isinstance(scenario, str) for scenario in scenarios
    ):
        raise HTTPException(
            status_code=400,
            detail="'scenarios' must be a list of supported scenario names.",
        )

    try:
        return _simulate_mtn_disbursement_risk(
            body.get("amount"),
            body.get("recipient_msisdn"),
            scenarios,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("MTN Disbursement risk simulation failed")
        raise HTTPException(
            status_code=500,
            detail="Risk simulation failed; no MTN transfer was sent.",
        ) from error


@app.post("/api/momo/mtn/disbursement/transfer")
def mtn_disbursement_transfer(
    body: dict = Body(...),
    current_user: dict = Depends(require_live_disbursement_admin),
):
    _require_mtn_live_operations()
    if body.get("production_confirmed") is not True:
        raise HTTPException(
            status_code=400,
            detail="Explicit confirmation is required before a real MTN transfer.",
        )

    try:
        normalized_amount, recipient_msisdn, risk_assessment = (
            _assess_mtn_disbursement(
                body.get("amount"),
                body.get("recipient_msisdn"),
                transaction_reference=body.get("transaction_reference"),
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Fraud assessment failed; no MTN transfer was sent.",
        ) from error

    transaction_reference = risk_assessment["transaction_reference"]
    if risk_assessment["risk_level"] != "LOW":
        risk_level = risk_assessment["risk_level"]
        high_risk = risk_level == "HIGH"
        create_audit_log(
            None,
            (
                "MTN_DISBURSEMENT_BLOCKED_BY_RISK"
                if high_risk
                else "MTN_DISBURSEMENT_STEP_UP_REQUIRED"
            ),
            current_user["username"],
            json.dumps(
                {
                    "transaction_reference": transaction_reference,
                    "recipient_last4": recipient_msisdn[-4:],
                    "amount": normalized_amount,
                    "risk_level": risk_assessment["risk_level"],
                    "risk_score": risk_assessment["risk_score"],
                },
                sort_keys=True,
            ),
        )
        return {
            "status": (
                "BLOCKED_BY_FRAUD_MODEL"
                if high_risk
                else "STEP_UP_REQUIRED"
            ),
            "reference_id": None,
            "transaction_reference": transaction_reference,
            "recipient_msisdn": recipient_msisdn,
            "currency": get_mtn_disbursement_configuration_status()["currency"],
            "risk_assessment": risk_assessment,
            "message": (
                "Blocked by the fraud model. No transfer was sent."
                if high_risk
                else (
                    "Additional verification is required. Live transfer is "
                    "unavailable until step-up verification is implemented."
                )
            ),
        }

    try:
        create_audit_log(
            None,
            "MTN_DISBURSEMENT_TRANSFER_INITIATED",
            current_user["username"],
            json.dumps(
                {
                    "transaction_reference": transaction_reference,
                    "recipient_last4": recipient_msisdn[-4:],
                    "amount": normalized_amount,
                    "risk_level": risk_assessment["risk_level"],
                    "risk_score": risk_assessment["risk_score"],
                },
                sort_keys=True,
            ),
        )
        transfer_kwargs = {
            "amount": normalized_amount,
            "recipient_msisdn": recipient_msisdn,
            "external_id": transaction_reference,
        }
        if "payer_message" in body:
            transfer_kwargs["payer_message"] = body["payer_message"]
        if "payee_note" in body:
            transfer_kwargs["payee_note"] = body["payee_note"]
        transfer = get_mtn_disbursement_client().transfer(**transfer_kwargs)
        return {
            **transfer,
            "transaction_reference": transaction_reference,
            "risk_assessment": risk_assessment,
            "message": "MTN accepted the transfer request. Check its status before retrying.",
        }
    except MtnDisbursementError as error:
        logger.warning("MTN Disbursement transfer failed: %s", error)
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error


@app.get("/api/momo/mtn/disbursement/transfer/{reference_id}")
def mtn_disbursement_transfer_status(
    reference_id: str,
    _current_user: dict = Depends(require_live_disbursement_admin),
):
    _require_mtn_live_operations()
    try:
        return get_mtn_disbursement_client().get_transfer_status(reference_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except MtnDisbursementError as error:
        logger.warning("MTN Disbursement status check failed: %s", error)
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error


@app.get("/api/momo/telemetry")
def momo_get_telemetry(request: Request):
    """Return coarse browser classification without exposing the observed IP."""
    client_ip = _client_ip(request)
    user_agent = request.headers.get("User-Agent", "")

    os_name = "Desktop OS"
    device_category = "DESKTOP"
    if "Android" in user_agent:
        os_name = "Android Mobile"
        device_category = "MOBILE"
    elif "iPhone" in user_agent:
        os_name = "Apple iOS (iPhone)"
        device_category = "MOBILE"
    elif "iPad" in user_agent:
        os_name = "Apple iPadOS"
        device_category = "TABLET"
    elif "Windows" in user_agent:
        os_name = "Windows PC"
        device_category = "DESKTOP"
    elif "Macintosh" in user_agent:
        os_name = "macOS"
        device_category = "DESKTOP"
    elif "Linux" in user_agent:
        os_name = "Linux Workstation"
        device_category = "DESKTOP"

    browser = "Web Browser"
    if "Chrome" in user_agent and "Edg" not in user_agent:
        browser = "Chrome"
    elif "Safari" in user_agent and "Chrome" not in user_agent:
        browser = "Safari"
    elif "Firefox" in user_agent:
        browser = "Firefox"
    elif "Edg" in user_agent:
        browser = "Microsoft Edge"

    device_label = f"{os_name} · {browser}"

    return {
        "is_local": client_ip in ("127.0.0.1", "localhost", "::1"),
        "device_id": None,
        "device_label": device_label,
        "device_category": device_category,
        "os": os_name,
        "browser": browser,
        "location": None,
        "country": None,
        "carrier": None,
        "coordinates": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/momo/transaction/initiate")
def momo_transaction_initiate(
    request: Request,
    body: dict = Body(...),
    authorization: str | None = Header(default=None),
):
    try:
        live_mtn = bool(body.get("live_mtn", False))
        customer_ref = body.get("customer_ref")
        claims = None
        parts = authorization.split() if isinstance(authorization, str) else []
        if len(parts) == 2 and parts[0].lower() == "bearer":
            claims = auth.decode_access_token(parts[1])
        is_customer_session = bool(
            claims and claims.get("role") == "momo_customer"
        )
        if live_mtn and body.get("transaction_type") != "TRANSFER":
            raise HTTPException(
                status_code=400,
                detail="MTN sandbox disbursement supports wallet-to-wallet transfers only.",
            )
        if is_customer_session:
            subscriber = _require_sandbox_customer(authorization)
            customer_ref = subscriber.get("phone_number")
            live_mtn = body.get("transaction_type") == "TRANSFER"
        if _momo_simulation_only():
            live_mtn = False
        client_ip = _client_ip(request)
        if live_mtn and get_mtn_disbursement_configuration_status()["environment"] != "sandbox":
            raise HTTPException(
                status_code=403,
                detail="Demo wallet transfers are restricted to MTN sandbox.",
            )
        if live_mtn and has_pending_mtn_transfer(customer_ref):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A previous MTN transfer is still unresolved. Check its "
                    "status before submitting another transfer."
                ),
            )
        location = _validate_customer_location(body)
        device_id = _validate_device_id(body.get("device_id"))
        if is_customer_session and not device_id:
            raise HTTPException(
                status_code=400,
                detail="Device security context is unavailable. Reload the page and retry.",
            )
        session = momo_initiate(
            customer_ref=customer_ref,
            transaction_type=body.get("transaction_type"),
            amount=body.get("amount"),
            recipient=body.get("recipient"),
            device_id=device_id,
            ip_address=client_ip,
            location_latitude=(
                location[0] if location else None
            ),
            location_longitude=(
                location[1] if location else None
            ),
            scenario=(
                body.get("scenarios")
                if body.get("scenarios") is not None
                else body.get("scenario", "NORMAL")
            ),
            live_mtn=live_mtn,
            otp_sms_sender=send_transaction_otp_sms,
        )
        if is_customer_session:
            record_customer_security_signal(
                customer_ref=customer_ref,
                session_id=session["session_id"],
                event_type="TRANSACTION_INITIATED",
                device_id=device_id,
                ip_address=client_ip,
                location_latitude=location[0] if location else None,
                location_longitude=location[1] if location else None,
                location_consent=body.get("location_consent") is True,
            )
            for private_signal in (
                "ip_address",
                "device_id",
                "location",
                "location_latitude",
                "location_longitude",
            ):
                session.pop(private_signal, None)
        return session
    except HTTPException:
        raise
    except OtpDeliveryError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("MoMo transaction initiation failed")
        raise HTTPException(
            status_code=500, detail="Unable to initiate transaction."
        ) from error


@app.post("/api/momo/transaction/verify")
def momo_transaction_verify(
    body: dict = Body(...),
    authorization: str | None = Header(default=None),
):
    if not body.get("session_id") or body.get("code") is None:
        raise HTTPException(
            status_code=400, detail="session_id and code are required."
        )
    try:
            session = get_momo_session(body["session_id"])
            if session is None:
                raise HTTPException(
                    status_code=404,
                    detail="Transaction session not found or already expired.",
                )
            claims = None
            parts = authorization.split() if isinstance(authorization, str) else []
            if len(parts) == 2 and parts[0].lower() == "bearer":
                claims = auth.decode_access_token(parts[1])
            if session.get("live_mtn") or (
                claims and claims.get("role") == "momo_customer"
            ):
                subscriber = _require_sandbox_customer(authorization)
                owned_refs = {
                    str(subscriber.get("phone_number") or ""),
                    str(subscriber.get("customer_ref") or ""),
                }
                if str(session.get("customer_ref")) not in owned_refs:
                    raise HTTPException(
                        status_code=403,
                        detail="This sandbox transaction belongs to a different wallet.",
                    )
            return momo_verify(
            body["session_id"],
            body["code"],
            step_up=bool(body.get("step_up", False)),
            step_up_method=body.get("step_up_method", "EMAIL_OTP"),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OtpDeliveryError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("MoMo transaction verification failed")
        raise HTTPException(status_code=500, detail="Unable to verify transaction.") from error


@app.post("/api/momo/transaction/{session_id}/status")
def momo_transaction_status(
    session_id: str,
    authorization: str | None = Header(default=None),
):
    session = get_momo_session(session_id)
    if not session or not session.get("live_mtn"):
        raise HTTPException(status_code=404, detail="MTN transfer session not found.")
    subscriber = _require_sandbox_customer(authorization)
    owned_refs = {
        str(subscriber.get("phone_number") or ""),
        str(subscriber.get("customer_ref") or ""),
    }
    if str(session.get("customer_ref")) not in owned_refs:
        raise HTTPException(
            status_code=403,
            detail="This sandbox transaction belongs to a different wallet.",
        )
    try:
        return momo_check_mtn_transfer_status(session_id)
    except MtnDisbursementError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail="Unable to confirm the MTN transfer status. Please retry the status check.",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("MTN transfer status reconciliation failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to reconcile the MTN transfer status.",
        ) from error


@app.post("/api/momo/customer/email/challenge")
def momo_customer_email_challenge(authorization: str | None = Header(default=None)):
    subscriber = _require_sandbox_customer(authorization)
    email_address = str(subscriber.get("email") or "").strip().lower()
    local, separator, domain = email_address.partition("@")
    if not separator or not local or "." not in domain:
        raise HTTPException(
            status_code=400,
            detail="No valid email address is saved on this wallet. Contact support before retrying.",
        )
    if subscriber.get("email_verified"):
        return {
            "email_verified": True,
            "masked_email": _mask_customer_email(email_address),
        }

    customer_key = str(subscriber.get("phone_number") or "")
    now = datetime.now(timezone.utc)
    current = _EMAIL_VERIFICATION_CHALLENGES.get(customer_key)
    if current and current["resend_after"] > now:
        raise HTTPException(
            status_code=429,
            detail="Wait before requesting another email verification code.",
        )
    code = f"{secrets.randbelow(1_000_000):06d}"
    if not send_email_verification_email(email_address, code):
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not send the verification email. Check the Render SMTP "
                "settings, confirm SMTP_FROM is a verified Brevo sender, and "
                "review Brevo transactional logs and Render service logs."
            ),
        )
    _EMAIL_VERIFICATION_CHALLENGES[customer_key] = {
        "email": email_address,
        "code_hash": hash_one_time_code(
            code, f"email-verification:{customer_key}"
        ),
        "expires_at": now + timedelta(minutes=10),
        "resend_after": now + timedelta(seconds=60),
        "attempts": 0,
    }
    return {
        "message": (
            "A verification code was sent to "
            f"{_mask_customer_email(email_address)}. Check your inbox and spam folder."
        ),
        "masked_email": _mask_customer_email(email_address),
    }


@app.post("/api/momo/customer/email/verify")
def momo_customer_email_verify(
    body: dict = Body(...),
    authorization: str | None = Header(default=None),
):
    subscriber = _require_sandbox_customer(authorization)
    customer_key = str(subscriber.get("phone_number") or "")
    challenge = _EMAIL_VERIFICATION_CHALLENGES.get(customer_key)
    if not challenge or challenge["expires_at"] <= datetime.now(timezone.utc):
        _EMAIL_VERIFICATION_CHALLENGES.pop(customer_key, None)
        raise HTTPException(
            status_code=400,
            detail="Email verification code is missing or expired. Request a new code.",
        )
    code = str(body.get("code") or "").strip()
    email_address = str(subscriber.get("email") or "").strip().lower()
    if email_address != challenge["email"]:
        _EMAIL_VERIFICATION_CHALLENGES.pop(customer_key, None)
        raise HTTPException(
            status_code=400,
            detail="The wallet email changed. Request a new verification code.",
        )
    if not verify_one_time_code(
        code,
        challenge["code_hash"],
        f"email-verification:{customer_key}",
    ):
        challenge["attempts"] += 1
        if challenge["attempts"] >= 5:
            _EMAIL_VERIFICATION_CHALLENGES.pop(customer_key, None)
        raise HTTPException(status_code=400, detail="Incorrect email verification code.")

    try:
        if get_sandbox_customer_profile(customer_key):
            update_sandbox_customer_email(customer_key, email_address)
        else:
            kyc_update_email(
                subscriber.get("customer_ref") or customer_key,
                email_address,
                verified=True,
            )
        momo_update_email(customer_key, email_address)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("Could not save verified sandbox email")
        raise HTTPException(
            status_code=500, detail="Could not save the verified email address."
        ) from error
    _EMAIL_VERIFICATION_CHALLENGES.pop(customer_key, None)
    return {
        "email_verified": True,
        "masked_email": _mask_customer_email(email_address),
    }


@app.get("/api/momo/email-config")
def momo_email_configuration():
    config = get_smtp_config()
    masked_user = ""
    if config["user"]:
        u = config["user"]
        masked_user = (u[0] + "***@" + u.partition("@")[2]) if "@" in u else (u[:2] + "***")
    return {
        "configured": config["configured"],
        "host": config["host"],
        "port": config["port"],
        "from_address": config["from_address"],
        "user": masked_user,
        "use_tls": config["use_tls"],
    }


@app.get("/api/momo/outbox")
def momo_outbox_items(current_user: dict = Depends(require_admin)):
    return {"outbox": get_recent_outbox(20)}


@app.get("/api/momo/kyc/registry")
def momo_kyc_registry():
    """Return all subscribers from the Ghana National ID / Passport Registry CSV."""
    return {"subscribers": get_all_subscribers(mask_sensitive=True)}


@app.get("/api/momo/kyc/lookup")
def momo_kyc_lookup(query: str = ""):
    """Lookup a subscriber by phone number, wallet ref, or ID number."""
    sub = find_subscriber(query)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found in Ghana KYC Registry.")
    copy = dict(sub)
    id_num = copy.get("id_number", "")
    copy["id_number_masked"] = (id_num[:4] + "***" + id_num[-2:]) if len(id_num) > 6 else id_num
    copy.pop("pin", None)
    return {"subscriber": copy}


@app.post("/api/momo/kyc/verify-id")
def momo_kyc_verify_id(body: dict = Body(...)):
    """Verify Ghana Card or Passport number against subscriber record."""
    phone_or_ref = body.get("phone_or_ref")
    id_number = body.get("id_number")
    valid, message = verify_id_document(phone_or_ref, id_number)
    if not valid:
        raise HTTPException(status_code=400, detail=message)
    return {"verified": True, "message": message}


@app.post("/api/momo/account/freeze")
def momo_account_freeze(body: dict = Body(...)):
    if not body.get("customer_ref") or body.get("code") is None:
        raise HTTPException(status_code=400, detail="customer_ref and code are required.")
    try:
        return momo_freeze(body["customer_ref"], body["code"])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("MoMo account freeze failed")
        raise HTTPException(status_code=500, detail="Unable to freeze account.") from error


@app.post("/api/momo/account/unfreeze")
def momo_account_unfreeze(body: dict = Body(...)):
    if not body.get("customer_ref") or body.get("code") is None:
        raise HTTPException(status_code=400, detail="customer_ref and code are required.")
    try:
        return momo_unfreeze(body["customer_ref"], body["code"])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("MoMo account unfreeze failed")
        raise HTTPException(status_code=500, detail="Unable to unfreeze account.") from error


@app.get("/api/admin/analytics", dependencies=[Depends(require_viewer)])
def admin_analytics():
    try:
        return get_analytics_data()
    except Exception as error:
        logger.exception("Unable to retrieve analytics")
        raise HTTPException(status_code=500, detail="Unable to load analytics data.") from error


@app.get("/api/admin/monitoring", dependencies=[Depends(require_viewer)])
def admin_monitoring():
    try:
        return build_monitoring()
    except Exception as error:
        logger.exception("Unable to build model monitoring")
        raise HTTPException(
            status_code=500, detail="Unable to build monitoring metrics."
        ) from error


@app.post("/api/admin/model/baseline")
@app.post("/api/admin/model/retrain", include_in_schema=False)
def admin_model_baseline(
    payload: dict = Body(default=None),
    current_user: dict = Depends(require_admin),
):
    try:
        username = current_user.get("username", "admin")
        if payload and payload.get("triggered_by"):
            username = f"{username}:{payload.get('triggered_by')}"
        return record_drift_baseline(triggered_by=username)
    except Exception as error:
        logger.exception("Unable to capture model drift baseline")
        raise HTTPException(
            status_code=500, detail="Failed to capture model drift baseline."
        ) from error


@app.post("/api/transactions/score")
def score_transaction(
    transaction: dict = Body(...),
    current_user: dict = Depends(require_analyst),
):
    required_fields = [
        "transaction_reference",
        "amount",
        "transaction_type",
        "destination_account",
    ]
    missing_fields = [field for field in required_fields if field not in transaction]
    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail={"error": "Missing required fields.", "missing_fields": missing_fields},
        )

    try:
        destination = transaction["destination_account"]
        origin = transaction.get("origin_account")
        recipient_history = (
            get_customer_recipient_history(origin, destination)
            if origin
            else get_recipient_history(destination)
        )
        assessment = fraud_detector.assess_transaction(transaction, recipient_history)
        transaction_id = create_transaction(transaction)
        create_risk_assessment(transaction_id, assessment)

        alert_id = None
        case_id = None
        if assessment["risk_level"] in ("MEDIUM", "HIGH"):
            alert_id = create_fraud_alert(
                transaction_id, assessment["risk_level"], assessment["reasons"]
            )
        if assessment["risk_level"] == "HIGH":
            case_id = create_fraud_case(transaction_id, alert_id)
            create_audit_log(
                transaction_id,
                "FRAUD_CASE_CREATED",
                "SYSTEM",
                f"Case ID: {case_id}; Alert ID: {alert_id}; "
                f"Risk score: {assessment['risk_score']:.4f}",
            )

        events.publish("stats", {"reason": "transaction scored"})
        events.publish("audit", {"reason": "logged"})
        if alert_id:
            events.publish("alert", {"reason": "created", "alert_id": alert_id})
        if case_id:
            events.publish("case", {"reason": "created", "case_id": case_id})

        return {
            "transaction_id": transaction_id,
            "transaction_reference": transaction["transaction_reference"],
            "recipient_history": recipient_history,
            "risk_score": assessment["risk_score"],
            "risk_level": assessment["risk_level"],
            "decision": assessment["decision"],
            "isolation_forest_score": assessment["isolation_forest_score"],
            "rule_score": assessment["rule_score"],
            "reasons": assessment["reasons"],
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Transaction scoring failed")
        raise HTTPException(
            status_code=500, detail="Unable to score transaction."
        ) from error


@app.get("/api/admin/fraud-cases", dependencies=[Depends(require_viewer)])
def list_fraud_cases():
    try:
        cases = get_fraud_cases(include_resolved=True)
        return {"total": len(cases), "cases": cases}
    except Exception as error:
        logger.exception("Unable to retrieve fraud cases")
        raise HTTPException(
            status_code=500, detail="Unable to retrieve fraud cases."
        ) from error


@app.patch("/api/admin/fraud-cases/{case_id}")
def update_case(
    case_id: int,
    updates: dict = Body(...),
    current_user: dict = Depends(require_analyst),
):
    if not updates:
        raise HTTPException(
            status_code=400, detail="Provide at least one field to update."
        )
    try:
        transaction_id = update_fraud_case(case_id, updates)
        if transaction_id is None:
            raise HTTPException(status_code=404, detail="Fraud case not found.")
        create_audit_log(
            transaction_id,
            "FRAUD_CASE_UPDATED",
            current_user.get("username", "ANALYST"),
            f"Case ID: {case_id}; Updates: {updates}",
        )
        events.publish("case", {"reason": "updated", "case_id": case_id})
        events.publish("audit", {"reason": "logged"})
        return {
            "message": "Fraud case updated successfully.",
            "case_id": case_id,
            "updates": updates,
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Fraud case update failed")
        raise HTTPException(status_code=500, detail="Unable to update fraud case.") from error


@app.post("/api/admin/fraud-cases/{case_id}/contact")
def record_case_contact(
    case_id: int,
    body: dict = Body(...),
    current_user: dict = Depends(require_analyst),
):
    try:
        result = record_fraud_case_contact(
            case_id, body.get("channel"), body.get("outcome")
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Fraud case not found.")
        create_audit_log(
            result["transaction_id"],
            "SIMULATED_CUSTOMER_CONTACT_RECORDED",
            current_user.get("username", "ANALYST"),
            f"Case ID: {case_id}; Channel: {result['contact_channel']}; "
            f"Outcome: {result['contact_outcome']}; No real call or SMS was sent.",
        )
        events.publish("case", {"reason": "contact_recorded", "case_id": case_id})
        events.publish("audit", {"reason": "logged"})
        return {
            "message": "Simulated contact outcome recorded.",
            "contact_simulated": True,
            **result,
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Fraud case contact recording failed")
        raise HTTPException(
            status_code=500, detail="Unable to record customer contact."
        ) from error


@app.post("/api/admin/fraud-cases/{case_id}/freeze-account")
def freeze_case_account(
    case_id: int,
    current_user: dict = Depends(require_admin),
):
    try:
        result = freeze_account_for_fraud_case(case_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Fraud case not found.")
        create_audit_log(
            result["transaction_id"],
            "ACCOUNT_FROZEN_AFTER_FRAUD_CONFIRMATION",
            current_user.get("username", "ADMIN"),
            f"Case ID: {case_id}; Customer: {result['customer_ref']}; "
            f"Risk level: {result['risk_level']}; "
            f"Simulated contact outcome: {result['contact_outcome'] or 'not recorded'}.",
        )
        events.publish("case", {"reason": "account_frozen", "case_id": case_id})
        events.publish("audit", {"reason": "logged"})
        events.publish("stats", {"reason": "account_frozen"})
        return {
            "message": "Demo account frozen; investigation status is unchanged.",
            "customer_ref": result["customer_ref"],
            "account_frozen": result["account_frozen"],
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Fraud case account freeze failed")
        raise HTTPException(
            status_code=500, detail="Unable to freeze customer account."
        ) from error


@app.post("/api/admin/fraud-cases/{case_id}/unfreeze-account")
def unfreeze_case_account(
    case_id: int,
    current_user: dict = Depends(require_admin),
):
    try:
        result = unfreeze_account_for_fraud_case(case_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Fraud case not found.")
        create_audit_log(
            result["transaction_id"],
            "ACCOUNT_UNFROZEN_BY_ADMIN",
            current_user.get("username", "ADMIN"),
            f"Case ID: {case_id}; Customer: {result['customer_ref']}.",
        )
        events.publish("case", {"reason": "account_unfrozen", "case_id": case_id})
        events.publish("audit", {"reason": "logged"})
        events.publish("stats", {"reason": "account_unfrozen"})
        return {
            "message": "Demo account unfrozen; investigation status is unchanged.",
            "customer_ref": result["customer_ref"],
            "account_frozen": result["account_frozen"],
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Fraud case account unfreeze failed")
        raise HTTPException(
            status_code=500, detail="Unable to unfreeze customer account."
        ) from error


@app.get(
    "/api/admin/accounts/frozen",
    dependencies=[Depends(require_viewer)],
)
def frozen_demo_accounts():
    try:
        accounts = [
            {
                "customer_ref": account["customer_ref"],
                "name": account["name"],
                "reason": account["reason"],
            }
            for account in get_admin_demo_accounts()
            if account["account_frozen"]
        ]
        return {"total": len(accounts), "accounts": accounts}
    except Exception as error:
        logger.exception("Unable to retrieve frozen demo accounts")
        raise HTTPException(
            status_code=500, detail="Unable to retrieve frozen demo accounts."
        ) from error


@app.get(
    "/api/admin/accounts",
    dependencies=[Depends(require_viewer)],
)
def admin_demo_accounts():
    try:
        accounts = get_admin_demo_accounts()
        return {"total": len(accounts), "accounts": accounts}
    except Exception as error:
        logger.exception("Unable to retrieve demo accounts")
        raise HTTPException(
            status_code=500, detail="Unable to retrieve demo accounts."
        ) from error


def get_admin_demo_accounts():
    accounts_by_ref = {
        customer["ref"]: {
            "customer_ref": customer["ref"],
            "name": customer["name"],
            "account_frozen": bool(customer.get("account_frozen")),
            "reason": customer.get("account_frozen_reason"),
        }
        for customer in momo_customers()
    }

    for case in get_fraud_cases(include_resolved=True):
        customer_ref = case.get("customer_ref")
        if not customer_ref or customer_ref in accounts_by_ref:
            continue

        account_status = get_account_status(customer_ref)
        accounts_by_ref[customer_ref] = {
            "customer_ref": customer_ref,
            "name": "Case-associated wallet",
            "account_frozen": account_status["frozen"],
            "reason": account_status["reason"],
        }

    return list(accounts_by_ref.values())


@app.post("/api/admin/accounts/{customer_ref}/freeze")
def freeze_demo_account(
    customer_ref: str,
    current_user: dict = Depends(require_admin),
):
    try:
        account = next(
            (
                item
                for item in get_admin_demo_accounts()
                if item["customer_ref"] == customer_ref
            ),
            None,
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Demo wallet not found.")
        if account["account_frozen"]:
            raise HTTPException(status_code=409, detail="Demo wallet is already frozen.")

        set_account_frozen(customer_ref, "ADMIN_MANUAL")
        create_audit_log(
            None,
            "ACCOUNT_FROZEN_BY_ADMIN",
            current_user.get("username", "ADMIN"),
            f"Admin-initiated demo freeze for {customer_ref}.",
        )
        events.publish(
            "case",
            {"reason": "account_frozen", "customer_ref": customer_ref},
        )
        events.publish("audit", {"reason": "logged"})
        events.publish("stats", {"reason": "account_frozen"})
        return {
            "message": "Demo wallet frozen by admin; investigation cases are unchanged.",
            "customer_ref": customer_ref,
            "account_frozen": True,
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Admin demo account freeze failed")
        raise HTTPException(
            status_code=500, detail="Unable to freeze demo wallet."
        ) from error


@app.post("/api/admin/accounts/{customer_ref}/unfreeze")
def unfreeze_demo_account(
    customer_ref: str,
    current_user: dict = Depends(require_admin),
):
    try:
        account = next(
            (
                item
                for item in get_admin_demo_accounts()
                if item["customer_ref"] == customer_ref
            ),
            None,
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Demo wallet not found.")
        if not account["account_frozen"]:
            raise HTTPException(status_code=409, detail="Demo wallet is not frozen.")

        set_account_unfrozen(customer_ref)
        create_audit_log(
            None,
            "ACCOUNT_UNFROZEN_BY_ADMIN",
            current_user.get("username", "ADMIN"),
            f"Admin cleared the demo wallet freeze for {customer_ref}.",
        )
        events.publish(
            "case",
            {"reason": "account_unfrozen", "customer_ref": customer_ref},
        )
        events.publish("audit", {"reason": "logged"})
        events.publish("stats", {"reason": "account_unfrozen"})
        return {
            "message": "Demo wallet unfrozen; investigation cases are unchanged.",
            "customer_ref": customer_ref,
            "account_frozen": False,
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Admin demo account unfreeze failed")
        raise HTTPException(
            status_code=500, detail="Unable to unfreeze demo wallet."
        ) from error


@app.get("/api/admin/audit-logs", dependencies=[Depends(require_viewer)])
def audit_logs():
    try:
        logs = get_audit_logs()
        return {"total": len(logs), "logs": logs}
    except Exception as error:
        logger.exception("Unable to retrieve audit logs")
        raise HTTPException(status_code=500, detail="Unable to retrieve audit logs.") from error


@app.get("/api/admin/dashboard-stats", dependencies=[Depends(require_viewer)])
def dashboard_stats():
    try:
        return get_dashboard_stats()
    except Exception as error:
        logger.exception("Unable to retrieve dashboard statistics")
        raise HTTPException(
            status_code=500, detail="Unable to retrieve dashboard statistics."
        ) from error


@app.get("/api/admin/fraud-alerts", dependencies=[Depends(require_viewer)])
def fraud_alerts():
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT a.id AS alert_id, a.transaction_id, t.transaction_reference,
                   t.ip_address, t.device_id, a.severity, a.status, a.reason,
                   a.created_at
            FROM fraud_alerts a
            JOIN transactions t ON t.id = a.transaction_id
            ORDER BY a.created_at DESC
            """
        )
        alerts = [dict(row) for row in cursor.fetchall()]
        for alert in alerts:
            reason = alert.get("reason")
            if reason:
                try:
                    alert["reason"] = json.loads(reason)
                except (json.JSONDecodeError, TypeError):
                    alert["reason"] = [str(reason)]
            else:
                alert["reason"] = []
        return {"total": len(alerts), "alerts": alerts}
    except Exception as error:
        logger.exception("Unable to retrieve fraud alerts")
        raise HTTPException(status_code=500, detail="Unable to retrieve fraud alerts.") from error
    finally:
        connection.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=5001, log_level="info")
