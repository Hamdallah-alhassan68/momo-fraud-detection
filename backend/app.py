"""FastAPI application for the synthetic MoMo fraud-control prototype."""

import json
import logging
import os
import queue
import secrets
import sqlite3

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from database.database import (
    create_admin_user,
    create_audit_log,
    get_customer_recipient_history,
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
from services import auth, events
from services.adaptive_intelligence import build_monitoring, record_drift_baseline
from services.fraud_detector import fraud_detector
from services.momo_simulator import (
    freeze_account as momo_freeze,
    get_customers as momo_customers,
    initiate_transaction as momo_initiate,
    unfreeze_account as momo_unfreeze,
    verify_transaction as momo_verify,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="MoMo Sentinel API",
    description="Synthetic post-authentication mobile-money fraud controls.",
    version="V3",
)

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


@app.post("/api/momo/transaction/initiate")
def momo_transaction_initiate(body: dict = Body(...)):
    try:
        return momo_initiate(
            customer_ref=body.get("customer_ref"),
            transaction_type=body.get("transaction_type"),
            amount=body.get("amount"),
            recipient=body.get("recipient"),
            device_id=body.get("device_id"),
            ip_address=body.get("ip_address"),
            scenario=(
                body.get("scenarios")
                if body.get("scenarios") is not None
                else body.get("scenario", "NORMAL")
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("MoMo transaction initiation failed")
        raise HTTPException(
            status_code=500, detail="Unable to initiate transaction."
        ) from error


@app.post("/api/momo/transaction/verify")
def momo_transaction_verify(body: dict = Body(...)):
    if not body.get("session_id") or body.get("code") is None:
        raise HTTPException(
            status_code=400, detail="session_id and code are required."
        )
    try:
        return momo_verify(
            body["session_id"],
            body["code"],
            step_up=bool(body.get("step_up", False)),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("MoMo transaction verification failed")
        raise HTTPException(status_code=500, detail="Unable to verify transaction.") from error


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
