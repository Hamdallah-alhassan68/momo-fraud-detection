"""
Simulated Mobile Money (MoMo) core layer.

This module acts as the "MoMo system" for the demo: customers hold wallet
balances, transactions are initiated and confirmed with a PIN (wallet-to-wallet)
or an OTP (wallet-to-bank / bank-to-wallet). The fraud detection system sits as
an extra layer between verification and completion: it scores every transaction in
real time and can approve, demand step-up verification, or block it entirely.

The balances and credentials here are DEMO simulation data only.
"""

import ipaddress
import re
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from database.database import (
    create_momo_session,
    get_momo_session,
    update_momo_session_status,
    create_transaction,
    create_risk_assessment,
    create_fraud_alert,
    create_fraud_case,
    create_audit_log,
    get_customer_recipient_history,
    count_velocity_sessions,
    get_customer_baseline,
    get_account_status,
    set_account_frozen,
    set_account_unfrozen,
)
from services import events
from services.fraud_detector import fraud_detector
from services.input_validation import (
    is_valid_simulated_recipient,
    is_valid_wallet_reference,
    validate_ip_address,
)

GHANA_TIMEZONE = ZoneInfo("Africa/Accra")

# ==========================================================
# DEMO DATA
# ==========================================================

CUSTOMERS = [
    {
        "ref": "WALLET-1001",
        "name": "Kwame Mensah",
        "phone": "0241001001",
        "email": "kwame.mensah@example.test",
        "email_verified": True,
        "balance": 5000.0,
        "pin": "2233",
        "base_ip": "192.168.1.22",
        "devices": ["MOBILE-AND-001", "MOBILE-AND-002"],
    },
    {
        "ref": "WALLET-1002",
        "name": "Ama Serwaa",
        "phone": "0242002002",
        "email": "ama.serwaa@example.test",
        "email_verified": True,
        "balance": 11600.0,
        "pin": "7788",
        "base_ip": "197.210.120.45",
        "devices": ["APPLE-IOS-013"],
    },
    {
        "ref": "WALLET-1003",
        "name": "Yaw Boateng",
        "phone": "0243003003",
        "email": "yaw.boateng@example.test",
        "email_verified": True,
        "balance": 2400.0,
        "pin": "9911",
        "base_ip": "41.220.77.21",
        "devices": ["MOBILE-AND-005", "MOBILE-AND-006"],
    },
]

RECIPIENTS = [
    {"ref": "WALLET-2001", "name": "Abena Ofori", "balance": 1600.0},
    {"ref": "MERCHANT-5001", "name": "Melcom Online Store", "balance": 8400.0},
    {"ref": "WALLET-2003", "name": "Kojo Antwi", "balance": 700.0},
]

BANK_ACCOUNT = {
    "ref": "BANK-ACC-7001",
    "name": "GCB Bank (Demo)",
    "balance": 200000.0,
}

TRANSACTION_TYPES = {
    "TRANSFER": {
        "label": "Wallet to Wallet",
        "verification": "PIN",
        "direction": "send",
    },
    "CASH_OUT": {
        "label": "Wallet to Bank / Cash Out",
        "verification": "OTP",
        "direction": "send",
    },
    "CASH_IN": {
        "label": "Bank to Wallet / Cash In",
        "verification": "OTP",
        "direction": "receive",
    },
}

SIMULATION_SCENARIOS = {
    "NORMAL",
    "RECENT_SIM_SWAP",
    "DEVICE_CHANGE",
    "IP_MISMATCH",
    "VELOCITY_SPIKE",
    "FREQUENCY_SPIKE",
    "AMOUNT_DEVIATION",
    "UNUSUAL_HOUR",
}

# A single synthetic event may combine several risk signals, so the simulator
# accepts more than one scenario per transaction. SCENARIO_ORDER also gives a
# stable storage/display order.
SCENARIO_ORDER = [
    "RECENT_SIM_SWAP",
    "DEVICE_CHANGE",
    "IP_MISMATCH",
    "VELOCITY_SPIKE",
    "FREQUENCY_SPIKE",
    "AMOUNT_DEVIATION",
    "UNUSUAL_HOUR",
]

SCENARIO_LABELS = {
    "NORMAL": "Normal activity",
    "RECENT_SIM_SWAP": "Recent SIM swap signal",
    "DEVICE_CHANGE": "New device signal",
    "IP_MISMATCH": "IP/network mismatch signal",
    "VELOCITY_SPIKE": "Velocity spike signal",
    "FREQUENCY_SPIKE": "Unusual daily frequency",
    "AMOUNT_DEVIATION": "Amount deviates from usual pattern",
    "UNUSUAL_HOUR": "Unusual transaction time",
}


def _normalize_scenarios(scenario):
    """Accept one scenario name or a list and return a canonical list.

    ``NORMAL`` is exclusive: if it is combined with any real scenario it is
    dropped. Unknown names raise ValueError so the API fails closed.
    """
    if scenario is None:
        raw = []
    elif isinstance(scenario, (list, tuple, set, frozenset)):
        raw = list(scenario)
    else:
        raw = [scenario]

    cleaned = []
    for item in raw:
        token = str(item or "").strip().upper()
        if not token:
            continue
        if token not in SIMULATION_SCENARIOS:
            raise ValueError(
                "Unsupported simulation scenario. Choose NORMAL, RECENT_SIM_SWAP, "
                "DEVICE_CHANGE, IP_MISMATCH, VELOCITY_SPIKE, FREQUENCY_SPIKE, "
                "AMOUNT_DEVIATION, or UNUSUAL_HOUR."
            )
        if token not in cleaned:
            cleaned.append(token)

    if not cleaned or cleaned == ["NORMAL"]:
        return ["NORMAL"]

    return [token for token in SCENARIO_ORDER if token in cleaned]


def _parse_scenarios(value):
    """Rebuild a scenario list from the stored comma-joined session value."""
    if isinstance(value, (list, tuple, set)):
        tokens = list(value)
    else:
        tokens = str(value or "").split(",")

    cleaned = [token.strip().upper() for token in tokens if token and token.strip()]
    if not cleaned:
        return ["NORMAL"]

    return [token for token in SCENARIO_ORDER + ["NORMAL"] if token in cleaned]


def _join_scenarios(scenarios):
    """Deterministic TEXT storage for one or many scenarios."""
    return ",".join(scenarios or ["NORMAL"])


BALANCES = {}


def _rebuild_balances():
    global BALANCES
    BALANCES = {customer["ref"]: customer["balance"] for customer in CUSTOMERS}
    BALANCES.update({recipient["ref"]: recipient["balance"] for recipient in RECIPIENTS})
    BALANCES[BANK_ACCOUNT["ref"]] = BANK_ACCOUNT["balance"]


_rebuild_balances()


# ==========================================================
# CUSTOM / USER-ENTERED WALLETS
# ==========================================================
# The simulator ships with three demo wallets, but the UI lets the user type
# their own wallet reference or Ghana mobile number. An unknown reference is
# registered as a "custom" demo wallet so the whole flow (balance, PIN/OTP,
# fraud scoring) keeps working end to end.

CUSTOM_WALLET_STARTING_BALANCE = 10000.0
CUSTOM_WALLET_PIN = "0000"
CUSTOM_WALLET_BASE_IP = "10.10.10.10"
CUSTOM_WALLET_DEVICE = "CUSTOM-DEVICE-001"
CUSTOM_WALLET_EMAIL = "custom.wallet@example.test"


def _ensure_customer(customer_ref):
    """Resolve a wallet reference, registering a custom wallet when unknown."""
    ref = str(customer_ref or "").strip()
    cleaned_phone = re.sub(r"[\s\-()]", "", ref)
    if re.fullmatch(r"(?:02|05|03)\d{8}|\+233(?:2|5|3)\d{8}", cleaned_phone):
        ref = cleaned_phone
    else:
        ref = ref.upper()

    customer = next((c for c in CUSTOMERS if c["ref"].upper() == ref), None)
    if customer is not None:
        return customer

    if not is_valid_wallet_reference(ref):
        raise ValueError(
            "Enter a demo wallet (WALLET-1001, WALLET-1002, WALLET-1003), "
            "a Ghana mobile number (e.g. 0241234567), or your own custom wallet ID "
            "such as WALLET-3001 or MY-WALLET."
        )

    customer = {
        "ref": ref,
        "name": f"Custom wallet ({ref})",
        "phone": ref if ref.replace("+", "").isdigit() else "0240000000",
        "email": CUSTOM_WALLET_EMAIL,
        "email_verified": True,
        "balance": CUSTOM_WALLET_STARTING_BALANCE,
        "pin": CUSTOM_WALLET_PIN,
        "base_ip": CUSTOM_WALLET_BASE_IP,
        "devices": [CUSTOM_WALLET_DEVICE],
        "custom": True,
    }
    CUSTOMERS.append(customer)
    BALANCES[ref] = CUSTOM_WALLET_STARTING_BALANCE
    return customer


# Sessions are temporarily frozen after a blocked transaction.
SESSION_FREEZE_MINUTES = 2
FROZEN_UNTIL = {}


def _mask_phone(phone):
    if len(phone) >= 6:
        return phone[:3] + "***" + phone[-2:]
    return phone


def _mask_email(email):
    local_part, separator, domain = email.partition("@")
    if not separator or not local_part or not domain:
        return "registered email"
    visible_local = local_part[0]
    if len(local_part) > 1:
        visible_local += "***" + local_part[-1]
    return f"{visible_local}@{domain}"


def get_customers():
    """Demo customers for the simulator UI (PIN never exposed)."""
    customers = []
    now_utc = datetime.now(timezone.utc)

    for customer in CUSTOMERS:
        copy = dict(customer)
        copy.pop("pin", None)
        copy.pop("email", None)
        copy.pop("email_verified", None)
        copy["balance"] = BALANCES.get(customer["ref"], customer["balance"])

        freeze_deadline = FROZEN_UNTIL.get(customer["ref"])
        still_frozen = bool(freeze_deadline and freeze_deadline > now_utc)
        copy["frozen"] = still_frozen
        copy["frozen_for"] = (
            int((freeze_deadline - now_utc).total_seconds())
            if still_frozen
            else 0
        )

        account_status = get_account_status(customer["ref"])
        copy["account_frozen"] = account_status.get("frozen", False)
        copy["account_frozen_reason"] = account_status.get("reason")

        copy["recipients"] = [
            {
                "ref": recipient["ref"],
                "name": recipient["name"],
                "balance": BALANCES.get(recipient["ref"], recipient["balance"]),
            }
            for recipient in RECIPIENTS
        ]
        customers.append(copy)
    return customers


def initiate_transaction(
    customer_ref,
    transaction_type,
    amount,
    recipient,
    device_id=None,
    ip_address=None,
    scenario: str = "NORMAL",
):
    """Start a MoMo transaction; returns a session awaiting PIN/OTP."""
    customer = _ensure_customer(customer_ref)
    customer_ref = customer["ref"]

    freeze_deadline = FROZEN_UNTIL.get(customer_ref)
    now_utc = datetime.now(timezone.utc)
    if freeze_deadline and freeze_deadline > now_utc:
        remaining = int((freeze_deadline - now_utc).total_seconds())
        raise ValueError(
            f"Your session is temporarily frozen after a blocked "
            f"transaction. Try again in about {remaining} seconds."
        )

    if get_account_status(customer_ref).get("frozen"):
        raise ValueError(
            "Your account is frozen at your request (security freeze). "
            "Unfreeze it from the security menu before sending money."
        )

    meta = TRANSACTION_TYPES.get(transaction_type)
    if meta is None:
        raise ValueError(
            "Unsupported transaction type. Choose TRANSFER, "
            "CASH_OUT, or CASH_IN."
        )

    scenarios = _normalize_scenarios(scenario)
    scenario_storage = _join_scenarios(scenarios)

    amount = float(amount)
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    if meta["direction"] == "send":
        available = BALANCES.get(customer["ref"], customer["balance"])
        if available < amount:
            raise ValueError(
                f"Insufficient funds. Available balance is GHS {available:,.2f}"
            )

    recipient = str(recipient or "").strip()
    cleaned_recipient_phone = re.sub(r"[\s\-()]", "", recipient)
    if re.fullmatch(r"(?:02|05|03)\d{8}|\+233(?:2|5|3)\d{8}", cleaned_recipient_phone):
        recipient = cleaned_recipient_phone

    if not is_valid_simulated_recipient(recipient, BALANCES):
        raise ValueError(
            "Enter a known demo recipient, a fictional wallet ID such as "
            "WALLET-3001, or a Ghana mobile number such as 0241234567 "
            "or +233241234567. For bank transfers, use BANK-ACC-7002."
        )
    if transaction_type == "TRANSFER" and recipient.startswith("BANK-ACC-"):
        raise ValueError(
            "For wallet-to-wallet transfers, enter a wallet ID or Ghana "
            "mobile number. Choose a bank transfer type for bank accounts."
        )
    if transaction_type in ("CASH_OUT", "CASH_IN") and not recipient.startswith(
        "BANK-ACC-"
    ):
        raise ValueError("Enter a bank account reference such as BANK-ACC-7001.")
    if meta["direction"] == "send" and recipient == customer_ref:
        raise ValueError("Choose a different recipient from your own wallet.")

    if meta["direction"] == "receive" and recipient not in BALANCES:
        BALANCES[recipient] = BANK_ACCOUNT["balance"]
    if meta["direction"] == "receive" and BALANCES[recipient] < amount:
        raise ValueError(
            f"Insufficient bank balance. Available balance is "
            f"GHS {BALANCES[recipient]:,.2f}"
        )

    session_id = uuid.uuid4().hex[:12].upper()
    verification = meta["verification"]
    otp_code = None
    if verification == "OTP":
        otp_code = f"{secrets.randbelow(1_000_000):06d}"

    device_id = device_id or customer["devices"][0]
    ip_address = validate_ip_address(
        str(ip_address or customer["base_ip"]).strip()
    )

    create_momo_session(
        session_id,
        customer_ref,
        transaction_type,
        amount,
        recipient,
        verification,
        otp_code,
        device_id,
        ip_address,
        scenario_storage,
    )

    message = (
        f"An OTP has been sent to {_mask_phone(customer['phone'])}"
        if verification == "OTP"
        else "Enter your MoMo PIN to authorise this transaction"
    )

    return {
        "session_id": session_id,
        "verification_method": verification,
        "message": message,
        "demo_hint": otp_code or customer["pin"],
        "customer_name": customer["name"],
        "amount": amount,
        "recipient": recipient,
        "transaction_type": transaction_type,
        "customer_ref": customer_ref,
        "device_id": device_id,
        "ip_address": ip_address,
        "scenario": scenario_storage,
        "scenarios": scenarios,
    }


def verify_transaction(session_id, code, step_up=False):
    """Verify the PIN/OTP then run the fraud layer in real time."""

    session = get_momo_session(session_id)
    if session is None:
        raise ValueError(
            "Transaction session not found or already expired."
        )

    if session["status"] not in ("PENDING", "STEP_UP"):
        raise ValueError(
            f"Session is already resolved as {session['status']}."
        )

    customer = _ensure_customer(session["customer_ref"])

    if get_account_status(customer["ref"]).get("frozen"):
        raise ValueError(
            "Your account is frozen at your request (security freeze). "
            "Unfreeze it from the security menu before sending money."
        )

    if step_up:
        expected = session.get("otp_code")
    else:
        method = session["verification_method"]
        expected = (
            customer["pin"]
            if method == "PIN"
            else session.get("otp_code")
        )
    if str(code).strip() != str(expected):
        raise ValueError("Incorrect PIN/OTP. Please try again.")
    if step_up and session["status"] != "STEP_UP":
        raise ValueError(
            "This transaction does not require step-up verification."
        )

    transaction_type = session["transaction_type"]
    meta = TRANSACTION_TYPES.get(transaction_type, {})
    amount = float(session["amount"])
    direction = meta.get("direction", "send")
    recipient = session["recipient"]

    if direction == "send":
        origin_ref = customer["ref"]
        dest_ref = recipient
    else:
        origin_ref = recipient  # bank account dispensing funds
        dest_ref = customer["ref"]

    origin_old = BALANCES.get(origin_ref, 0.0)
    dest_old = BALANCES.get(dest_ref, 0.0)

    if direction == "send":
        origin_new = max(0.0, origin_old - amount)
        dest_new = dest_old + amount
    else:
        origin_new = max(0.0, origin_old - amount)
        dest_new = dest_old + amount

    dest_previous_count = get_customer_recipient_history(
        customer["ref"], recipient
    )
    recipient_novel_flag = (
        bool(dest_previous_count == 0)
        if direction == "send"
        else False
    )

    device_id = session.get("device_id") or customer["devices"][0]
    ip_address = session.get("ip_address") or customer["base_ip"]

    scenarios = _parse_scenarios(session.get("scenario") or "NORMAL")
    device_change = bool(
        device_id not in customer["devices"] or "DEVICE_CHANGE" in scenarios
    )
    expected_network = ipaddress.ip_network(
        f"{customer['base_ip']}/24",
        strict=False,
    )
    provided_address = ipaddress.ip_address(ip_address)
    ip_network_mismatch = bool(
        provided_address.version != expected_network.version
        or provided_address not in expected_network
        or "IP_MISMATCH" in scenarios
    )
    expected_network = str(expected_network)

    velocity_count = count_velocity_sessions(
        customer["ref"], window_minutes=3
    )
    velocity_spike = velocity_count >= 3 or "VELOCITY_SPIKE" in scenarios
    baseline = get_customer_baseline(customer["ref"])
    baseline_count = int(baseline.get("count") or 0)
    average_daily_frequency = float(
        baseline.get("avg_transactions_per_day") or 0.0
    )
    historical_days = baseline.get("transactions_per_day", {})
    today_local = datetime.now(GHANA_TIMEZONE).date().isoformat()
    today_count = int(historical_days.get(today_local, 0))
    frequency_spike = bool(
        baseline_count >= 5
        and today_count >= max(5, average_daily_frequency * 2)
    ) or "FREQUENCY_SPIKE" in scenarios

    # ------------------------------------------------
    # Customer behavioural baseline (historical pattern)
    # ------------------------------------------------
    baseline_avg_amount = float(baseline.get("avg_amount") or 0.0)
    amount_deviation = bool(
        baseline_count >= 2 and amount > baseline_avg_amount * 1.5
    ) or "AMOUNT_DEVIATION" in scenarios

    hour_local = datetime.now(GHANA_TIMEZONE).hour
    usual_hours = baseline.get("usual_hours", [])
    unusual_hour = bool(
        baseline_count >= 5
        and usual_hours
        and hour_local not in usual_hours
    ) or "UNUSUAL_HOUR" in scenarios

    recent_sim_swap = "RECENT_SIM_SWAP" in scenarios

    transaction = {
        "amount": amount,
        "transaction_type": transaction_type,
        "origin_account": origin_ref,
        "destination_account": dest_ref,
        "old_balance_origin": origin_old,
        "new_balance_origin": origin_new,
        "old_balance_destination": dest_old,
        "new_balance_destination": dest_new,
        "recipient_new": recipient_novel_flag,
        "device_change": device_change,
        "ip_network_mismatch": ip_network_mismatch,
        "recent_sim_swap": recent_sim_swap,
        "scenario": _join_scenarios(scenarios),
        "velocity_spike": velocity_spike,
        "frequency_spike": frequency_spike,
        "amount_deviation": amount_deviation,
        "unusual_hour": unusual_hour,
        "ip_address": ip_address,
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    assessment = fraud_detector.assess_transaction(
        transaction, dest_previous_count
    )
    risk_level = assessment["risk_level"]

    reference = f"MOMO-{uuid.uuid4().hex[:8].upper()}"
    transaction["transaction_reference"] = reference

    signals = {
        "device_change": device_change,
        "ip_network_mismatch": ip_network_mismatch,
        "recipient_novel": recipient_novel_flag,
        "velocity_spike": velocity_spike,
        "frequency_spike": frequency_spike,
        "recent_sim_swap": recent_sim_swap,
        "amount_deviation": amount_deviation,
        "unusual_hour": unusual_hour,
        "expected_network": expected_network,
        "ip_address": ip_address,
        "device_id": device_id,
        "scenarios": scenarios,
    }

    outcome = _resolve_outcome(
        session_id, risk_level, assessment, reference, transaction,
        signals, amount, dest_ref, origin_ref, origin_new, dest_new,
        session["status"], session.get("otp_code"), ip_address, device_id,
        step_up_email=(
            customer.get("email")
            if customer.get("email_verified") is True
            else None
        ),
    )

    if outcome.get("status") == "BLOCKED":
        FROZEN_UNTIL[customer["ref"]] = (
            datetime.now(timezone.utc) + timedelta(minutes=SESSION_FREEZE_MINUTES)
        )
        outcome["message"] += (
            f" Your session has been frozen for {SESSION_FREEZE_MINUTES} "
            "minutes."
        )

    return outcome


def _resolve_outcome(session_id, risk_level, assessment, reference, transaction,
                      signals, amount, dest_ref, origin_ref, origin_new, dest_new,
                      session_status, session_otp, ip_address, device_id,
                      step_up_email=None):
    """Apply the fraud engine decision to the transaction session."""

    if risk_level == "LOW" or session_status == "STEP_UP":
        return _apply_outcome(
            session_id, risk_level, assessment, reference, transaction,
            signals, amount, dest_ref, origin_ref, origin_new, dest_new,
            ip_address, device_id
        )

    if risk_level == "MEDIUM":
        if not step_up_email:
            _finalize(
                "BLOCKED", "STEP_UP_UNAVAILABLE", transaction, assessment,
                ip_address, device_id
            )
            update_momo_session_status(
                session_id,
                "BLOCKED",
                risk_level=risk_level,
                risk_score=assessment["risk_score"],
                reason="No verified email is available for step-up verification",
            )
            return {
                "status": "BLOCKED",
                "message": (
                    "This transaction was blocked because no verified email "
                    "is available for the additional security check. "
                    "Money has NOT left your wallet."
                ),
                "transaction_reference": reference,
                "amount": amount,
                "recipient": dest_ref,
                "risk_score": assessment["risk_score"],
                "risk_level": risk_level,
                "decision": "STEP-UP UNAVAILABLE",
                "reasons": assessment["reasons"],
                "signals": signals,
                "approved": False,
            }

        step_up_otp = f"{secrets.randbelow(1_000_000):06d}"
        update_momo_session_status(
            session_id, "STEP_UP",
            reason="Additional verification required",
            otp_code=step_up_otp,
            risk_level=risk_level, risk_score=assessment["risk_score"],
        )
        return {
            "status": "STEP_UP_REQUIRED",
            "message": (
                "Suspicious activity detected. Additional verification is "
                "required before this transfer completes."
            ),
            "session_id": session_id,
            "step_up": True,
            "verification_method": "EMAIL",
            "verification_destination": _mask_email(step_up_email),
            "delivery_status": "SIMULATED_NOT_SENT",
            "demo_hint": step_up_otp,
            "reasons": assessment["reasons"],
            "risk_score": assessment["risk_score"],
            "signals": signals,
        }

    # HIGH risk: block the transaction immediately.
    _finalize(
        "BLOCKED", "TRANSACTION_BLOCKED", transaction, assessment,
        ip_address, device_id
    )
    update_momo_session_status(
        session_id, "BLOCKED",
        risk_level=risk_level, risk_score=assessment["risk_score"],
        reason="Blocked by fraud prevention engine",
    )
    return {
        "status": "BLOCKED",
        "message": (
            "This transaction was blocked by the fraud prevention system. "
            "Money has NOT left your wallet."
        ),
        "transaction_reference": reference,
        "amount": amount,
        "recipient": dest_ref,
        "risk_score": assessment["risk_score"],
        "risk_level": risk_level,
        "decision": assessment["decision"],
        "reasons": assessment["reasons"],
        "signals": signals,
        "approved": False,
        "case_created": True,
    }


def _finalize(status, audit_action, transaction, assessment, ip_address, device_id):
    """Persist the assessed transaction and notify live dashboards."""
    transaction["status"] = status
    tx_id = create_transaction(transaction)
    create_risk_assessment(tx_id, assessment)

    audit_details = (
        f"Risk score: {assessment['risk_score']:.4f}; "
        f"decision: {assessment['decision']}; "
        f"IP: {ip_address}; Device: {device_id}"
    )

    if status == "BLOCKED":
        alert_id = create_fraud_alert(
            tx_id, assessment["risk_level"], assessment["reasons"]
        )
        case_id = create_fraud_case(tx_id, alert_id)
        create_audit_log(tx_id, "FRAUD_CASE_CREATED", "SYSTEM", audit_details)

        if case_id:
            events.publish("case", {"reason": "created", "case_id": case_id})
        if alert_id:
            events.publish("alert", {"reason": "created", "alert_id": alert_id})
    else:
        create_audit_log(tx_id, audit_action, "SYSTEM", audit_details)

    events.publish("transaction", {"reason": "created", "transaction_id": tx_id})
    events.publish("stats", {"reason": "updated"})
    return tx_id


def _apply_outcome(session_id, risk_level, assessment, reference, transaction,
                    signals, amount, dest_ref, origin_ref, origin_new, dest_new,
                    ip_address, device_id):
    """Bookkeeping after an APPROVED outcome (balance update + audit)."""
    tx_id = _finalize(
        "COMPLETED", "TRANSACTION_APPROVED", transaction, assessment,
        ip_address, device_id
    )
    update_momo_session_status(
        session_id, "COMPLETED",
        risk_level=risk_level, risk_score=assessment["risk_score"],
        reason=None if risk_level == "LOW" else "Approved after step-up verification",
    )

    BALANCES[origin_ref] = origin_new
    BALANCES[dest_ref] = dest_new

    return {
        "status": "APPROVED",
        "message": (
            "Transaction completed successfully."
            if risk_level == "LOW"
            else "Transaction approved after step-up verification."
        ),
        "transaction_reference": reference,
        "amount": amount,
        "recipient": dest_ref,
        "risk_score": assessment["risk_score"],
        "risk_level": risk_level,
        "decision": (
            assessment["decision"]
            if risk_level == "LOW"
            else "APPROVED AFTER STEP-UP"
        ),
        "reasons": assessment["reasons"],
        "signals": signals,
        "approved": True,
        "step_up_completed": risk_level != "LOW",
    }


# ==========================================================
# SELF-SERVICE ACCOUNT FREEZE (customer-initiated control)
# ==========================================================

def freeze_account(customer_ref, code):
    """Freeze the account at the customer's request (PIN confirmed)."""
    customer = next((c for c in CUSTOMERS if c["ref"] == customer_ref), None)
    if customer is None:
        raise ValueError("Unknown wallet account.")

    if str(code).strip() != str(customer["pin"]):
        raise ValueError(
            "Incorrect PIN. Your account was NOT frozen."
        )

    set_account_frozen(customer_ref, "FROZEN_BY_CUSTOMER")

    create_audit_log(
        None,
        "ACCOUNT_FROZEN_BY_USER",
        "CUSTOMER",
        f"{customer_ref} requested a self-service freeze after suspected "
        "account compromise.",
    )
    events.publish("audit", {"reason": "account frozen"})
    events.publish("stats", {"reason": "account frozen"})

    return {
        "status": "FROZEN",
        "customer_ref": customer_ref,
        "message": (
            "Your account is now frozen. No money can leave it until "
            "you unfreeze it with your PIN."
        ),
    }


def unfreeze_account(customer_ref, code):
    """Re-activate a customer-frozen account (PIN confirmed)."""
    customer = next((c for c in CUSTOMERS if c["ref"] == customer_ref), None)
    if customer is None:
        raise ValueError("Unknown wallet account.")

    if str(code).strip() != str(customer["pin"]):
        raise ValueError("Incorrect PIN. Account remains frozen.")

    set_account_unfrozen(customer_ref)

    create_audit_log(
        None,
        "ACCOUNT_UNFROZEN_BY_USER",
        "CUSTOMER",
        f"{customer_ref} re-activated their account with a valid PIN.",
    )
    events.publish("audit", {"reason": "account unfrozen"})
    events.publish("stats", {"reason": "account unfrozen"})

    return {
        "status": "ACTIVE",
        "customer_ref": customer_ref,
        "message": "Your account is active again. You can send money now.",
    }


# ==========================================================
# MODULE END
# ==========================================================
