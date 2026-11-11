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
import json
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
    get_customer_baseline,
    get_customer_security_history,
    get_customer_velocity_summary,
    get_recipient_velocity_summary,
    get_sandbox_customer_profile,
    trust_customer_security_signal,
    get_account_status,
    update_mtn_transaction_status,
    set_account_frozen,
    set_account_unfrozen,
)
from services import events
from services.fraud_detector import fraud_detector

from services.mtn_disbursement import (
    get_mtn_disbursement_client,
    MtnDisbursementError,
    normalize_disbursement_amount,
)
from services.mtn_collection import MtnCollectionClient
from services.auth import verify_password
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
        "id_type": "GHANA_CARD",
        "id_number": "GHA-712345678-1",
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
        "id_type": "GHANA_CARD",
        "id_number": "GHA-823456789-2",
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
        "id_type": "PASSPORT",
        "id_number": "G1089234",
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
}

# Only signals without behavioral history are manually injectable. Behavioral
# signals are calculated from the simulator's transaction history and context.
SCENARIO_ORDER = [
    "RECENT_SIM_SWAP",
    "DEVICE_CHANGE",
]

SCENARIO_LABELS = {
    "NORMAL": "Normal activity",
    "RECENT_SIM_SWAP": "Recent SIM swap signal",
    "DEVICE_CHANGE": "New device signal",
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
                "or DEVICE_CHANGE. Behavioral risk signals are calculated from "
                "transaction history and network context."
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

    sandbox_profile = get_sandbox_customer_profile(ref)
    if sandbox_profile:
        customer = {
            "ref": sandbox_profile["phone_number"],
            "name": sandbox_profile["full_name"],
            "phone": sandbox_profile["phone_number"],
            "id_type": sandbox_profile["id_type"],
            "id_number": "",
            "id_hash": sandbox_profile.get("id_hash"),
            "email": sandbox_profile["email"],
            "email_verified": bool(sandbox_profile["email_verified"]),
            "balance": float(sandbox_profile["balance"]),
            "pin_hash": sandbox_profile["pin_hash"],
            "step_up_code_hash": sandbox_profile.get("step_up_code_hash"),
            "base_ip": "127.0.0.1",
            "devices": [],
            "sandbox_profile": True,
        }
        CUSTOMERS.append(customer)
        BALANCES[customer["ref"]] = customer["balance"]
        return customer

    try:
        from services.kyc_service import find_subscriber
        kyc_sub = find_subscriber(ref)
        if kyc_sub:
            assigned_ref = (
                ref
                if (ref.startswith("0") or ref.startswith("+") or not ref.upper().startswith("WALLET-"))
                else kyc_sub["customer_ref"]
            )
            customer = {
                "ref": assigned_ref,
                "name": kyc_sub["full_name"],
                "phone": kyc_sub["phone_number"],
                "id_type": kyc_sub.get("id_type", "GHANA_CARD"),
                "id_number": kyc_sub.get("id_number", ""),
                "email": kyc_sub.get("email", CUSTOM_WALLET_EMAIL),
                "email_verified": bool(kyc_sub.get("email_verified", False)),
                "balance": kyc_sub.get("balance", CUSTOM_WALLET_STARTING_BALANCE),
                "pin": kyc_sub.get("pin", CUSTOM_WALLET_PIN),
                "base_ip": kyc_sub.get("base_ip", CUSTOM_WALLET_BASE_IP),
                "devices": kyc_sub.get("devices") or [CUSTOM_WALLET_DEVICE],
                "tier": kyc_sub.get("tier", "TIER_3_FULL_KYC"),
            }
            CUSTOMERS.append(customer)
            BALANCES[customer["ref"]] = customer["balance"]
            return customer
    except Exception:
        pass

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


def remove_sandbox_customer(customer_ref):
    """Clear one sandbox customer's runtime-only profile and balance."""
    CUSTOMERS[:] = [
        customer
        for customer in CUSTOMERS
        if not (
            customer.get("sandbox_profile")
            and customer.get("ref") == customer_ref
        )
    ]
    BALANCES.pop(customer_ref, None)
    FROZEN_UNTIL.pop(customer_ref, None)


def _mask_phone(phone):
    if len(phone) >= 6:
        return phone[:3] + "***" + phone[-2:]
    return phone



def normalize_ghana_msisdn(msisdn):
    digits = re.sub(r"\D", "", str(msisdn or ""))
    if len(digits) == 10 and digits.startswith("0"):
        return "233" + digits[1:]
    return digits


def update_customer_email(customer_ref, email):
    customer = _ensure_customer(customer_ref)
    clean_email = str(email or "").strip().lower()
    if "@" not in clean_email or "." not in clean_email.partition("@")[2]:
        raise ValueError("Enter a valid email address.")
    customer["email"] = clean_email
    customer["email_verified"] = True
    return {
        "customer_ref": customer["ref"],
        "email": clean_email,
        "email_verified": True,
        "masked_email": _mask_email(clean_email),
    }


def update_customer_step_up_code(customer_ref, code_hash):
    """Refresh the cached sandbox customer's hashed MEDIUM-risk code."""
    customer = _ensure_customer(customer_ref)
    if not customer.get("sandbox_profile"):
        raise ValueError(
            "Security-code setup is available only for self-enrolled sandbox wallets."
        )
    customer["step_up_code_hash"] = code_hash


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
        copy["email"] = customer.get("email")
        copy["masked_email"] = _mask_email(customer.get("email", ""))
        copy["email_verified"] = bool(customer.get("email_verified", True))
        copy["balance"] = BALANCES.get(customer["ref"], customer["balance"])
        copy["id_type"] = customer.get("id_type", "GHANA_CARD")
        id_num = customer.get("id_number", "")
        copy["id_number_masked"] = (id_num[:4] + "***" + id_num[-2:]) if len(id_num) > 6 else id_num

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
    live_mtn: bool = False,
    location_latitude=None,
    location_longitude=None,
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

    if live_mtn:
        if transaction_type == "TRANSFER":
            norm_phone = normalize_ghana_msisdn(recipient)
            try:
                recipient = MtnCollectionClient._normalize_msisdn(norm_phone)
            except ValueError as error:
                raise ValueError("Enter a valid recipient mobile number (e.g. 233241234567).") from error
        try:
            normalize_disbursement_amount(amount)
        except ValueError as error:
            raise ValueError(str(error)) from error

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
    location = _normalize_location(location_latitude, location_longitude)

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
        live_mtn=1 if live_mtn else 0,
        location_latitude=location[0] if location else None,
        location_longitude=location[1] if location else None,
    )

    message = (
        f"An OTP has been sent to {_mask_phone(customer['phone'])}"
        if verification == "OTP"
        else "Enter your MoMo PIN to authorise this transaction"
    )
    demo_hint = otp_code or (
        None if customer.get("sandbox_profile") else customer.get("pin")
    )

    return {
        "session_id": session_id,
        "verification_method": verification,
        "message": message,
        **({"demo_hint": demo_hint} if not live_mtn and demo_hint else {}),
        "customer_name": customer["name"],
        "amount": amount,
        "recipient": recipient,
        "transaction_type": transaction_type,
        "customer_ref": customer_ref,
        "device_id": device_id,
        "ip_address": ip_address,
        "scenario": scenario_storage,
        "scenarios": scenarios,
        "live_mtn": bool(live_mtn),
        "location": (
            {"latitude": location[0], "longitude": location[1]}
            if location
            else None
        ),
    }


def _normalize_location(latitude, longitude):
    if latitude is None and longitude is None:
        return None
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError) as error:
        raise ValueError("Location coordinates must be valid numbers.") from error
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Location coordinates are outside valid ranges.")
    return round(latitude, 2), round(longitude, 2)


def _distance_km(first, second):
    """Approximate great-circle distance between two coarse GPS points."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1 = radians(first[0]), radians(first[1])
    lat2, lon2 = radians(second[0]), radians(second[1])
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    haversine = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    return 6371.0 * 2 * asin(sqrt(min(1.0, haversine)))


def _has_impossible_travel(location, trusted_locations, now):
    if not location:
        return False
    for previous in trusted_locations:
        if not previous.get("observed_at"):
            continue
        try:
            observed = datetime.fromisoformat(
                previous["observed_at"].replace("Z", "+00:00")
            )
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        elapsed_hours = (now - observed).total_seconds() / 3600
        distance = _distance_km(
            (previous["latitude"], previous["longitude"]),
            location,
        )
        if elapsed_hours > 0 and distance >= 100 and distance / elapsed_hours > 900:
            return True
    return False


def _verify_step_up_credential(customer, session, credential, method):
    verification_method = str(method or "SECURITY_CODE").strip().upper()
    if verification_method == "SECURITY_CODE":
        code = str(credential or "").strip()
        code_hash = customer.get("step_up_code_hash")
        if not code_hash:
            raise ValueError(
                "A MEDIUM-risk security code is not set for this wallet."
            )
        if not verify_password(code, code_hash):
            raise ValueError("Incorrect security code. Please try again.")
        return
    if verification_method == "PASSPORT":
        if str(customer.get("id_type") or "").upper() != "PASSPORT":
            raise ValueError("Passport verification is not available for this wallet.")
        passport_number = re.sub(r"[\s-]", "", str(credential or "")).upper()
        id_hash = customer.get("id_hash")
        stored_passport = re.sub(
            r"[\s-]", "", str(customer.get("id_number") or "")
        ).upper()
        passport_matches = (
            verify_password(passport_number, id_hash)
            if id_hash
            else bool(stored_passport) and passport_number == stored_passport
        )
        if not passport_matches:
            raise ValueError("Incorrect passport number. Please try again.")
        return
    raise ValueError("Choose your security code or passport verification.")


def verify_transaction(
    session_id, code, step_up=False, step_up_method="SECURITY_CODE"
):
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
    live_mtn = bool(session.get("live_mtn", 0))

    if get_account_status(customer["ref"]).get("frozen"):
        raise ValueError(
            "Your account is frozen at your request (security freeze). "
            "Unfreeze it from the security menu before sending money."
        )

    if step_up:
        if session["status"] != "STEP_UP":
            raise ValueError(
                "This transaction does not require step-up verification."
            )
        try:
            _verify_step_up_credential(customer, session, code, step_up_method)
        except ValueError:
            attempts = int(session.get("step_up_count") or 0) + 1
            update_momo_session_status(
                session_id,
                "BLOCKED" if attempts >= 5 else "STEP_UP",
                step_up_count=attempts,
                reason=(
                    "Too many incorrect step-up attempts"
                    if attempts >= 5
                    else "Incorrect step-up credential"
                ),
            )
            if attempts >= 5:
                raise ValueError(
                    "Too many incorrect verification attempts. Start a new transaction."
                )
            raise
    else:
        method = session["verification_method"]
        if method == "PIN" and customer.get("pin_hash"):
            code_matches = verify_password(
                str(code).strip(), customer["pin_hash"]
            )
        else:
            expected = (
                customer.get("pin")
                if method == "PIN"
                else session.get("otp_code")
            )
            code_matches = str(code).strip() == str(expected)
        if not code_matches:
            raise ValueError("Incorrect PIN/OTP. Please try again.")
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
    baseline = get_customer_baseline(customer["ref"])
    baseline_count = int(baseline.get("count") or 0)
    security_history = get_customer_security_history(customer["ref"])
    known_devices = set(customer["devices"]) | security_history["device_ids"]
    device_change = bool(
        device_id not in known_devices or "DEVICE_CHANGE" in scenarios
    )
    known_ips = security_history["ip_addresses"] or {customer["base_ip"]}
    provided_address = ipaddress.ip_address(ip_address)
    expected_networks = [
        ipaddress.ip_network(
            f"{known_ip}/{24 if ipaddress.ip_address(known_ip).version == 4 else 64}",
            strict=False,
        )
        for known_ip in known_ips
    ]
    ip_network_mismatch = not any(
        provided_address.version == network.version
        and provided_address in network
        for network in expected_networks
    )
    location = (
        (session.get("location_latitude"), session.get("location_longitude"))
        if session.get("location_latitude") is not None
        and session.get("location_longitude") is not None
        else None
    )
    location_mismatch = bool(
        location
        and security_history["locations"]
        and min(
            _distance_km(location, previous)
            for previous in security_history["locations"]
        ) > 50
    )
    now_utc = datetime.now(timezone.utc)
    impossible_travel = _has_impossible_travel(
        location,
        security_history.get("trusted_locations", []),
        now_utc,
    )
    device_first_seen = security_history.get("device_first_seen", {}).get(device_id)
    account_created_at = security_history.get("account_created_at")
    try:
        account_age_days = max(
            0.0,
            (now_utc - datetime.fromisoformat(
                account_created_at.replace("Z", "+00:00")
            )).total_seconds() / 86400,
        ) if account_created_at else 365.0
    except (TypeError, ValueError):
        account_age_days = 0.0
    try:
        device_age_days = max(
            0.0,
            (now_utc - datetime.fromisoformat(
                device_first_seen.replace("Z", "+00:00")
            )).total_seconds() / 86400,
        ) if device_first_seen else (
            account_age_days if device_id in customer["devices"] else 0.0
        )
    except (TypeError, ValueError):
        device_age_days = 0.0
    cold_start = baseline_count < 5
    new_account = account_age_days < 1

    velocity = get_customer_velocity_summary(customer["ref"])
    recipient_velocity = get_recipient_velocity_summary(
        customer["ref"], recipient
    )
    velocity_spike = velocity["5m_count"] >= 3
    if baseline.get("count", 0) >= 5:
        robust_amount_limit = (
            float(baseline["median_amount"])
            + max(
                3 * 1.4826 * float(baseline["median_absolute_deviation"]),
                float(baseline["median_amount"]) * 0.5,
            )
        )
        velocity_spike = velocity_spike or (
            velocity["5m_count"] >= 2
            and velocity["5m_amount"] > robust_amount_limit * 2
        )
        average_daily_count = float(
            baseline.get("avg_transactions_per_day") or 0
        )
        expected_hourly_count = average_daily_count / 24
        velocity_spike = velocity_spike or (
            velocity["1h_count"] >= max(5, expected_hourly_count * 3)
            and velocity["1h_amount"] > robust_amount_limit * 3
        )
    recipient_burst = recipient_velocity["count"] >= 3
    average_daily_frequency = float(
        baseline.get("avg_transactions_per_day") or 0.0
    )
    historical_days = baseline.get("transactions_per_day", {})
    today_local = datetime.now(GHANA_TIMEZONE).date().isoformat()
    today_count = int(historical_days.get(today_local, 0))
    frequency_spike = bool(
        baseline_count >= 5
        and today_count >= max(5, average_daily_frequency * 2)
    )

    # ------------------------------------------------
    # Customer behavioural baseline (historical pattern)
    # ------------------------------------------------
    baseline_median_amount = float(baseline.get("median_amount") or 0.0)
    baseline_mad = float(baseline.get("median_absolute_deviation") or 0.0)
    amount_deviation = bool(
        baseline_count >= 5
        and amount
        > baseline_median_amount
        + max(3 * 1.4826 * baseline_mad, baseline_median_amount * 0.5)
    )

    hour_local = datetime.now(GHANA_TIMEZONE).hour
    usual_hours = baseline.get("usual_hours", [])
    unusual_hour = bool(
        baseline_count >= 5
        and usual_hours
        and hour_local not in usual_hours
    )

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
        "location_mismatch": location_mismatch,
        "impossible_travel": impossible_travel,
        "recent_sim_swap": recent_sim_swap,
        "scenario": _join_scenarios(scenarios),
        "velocity_spike": velocity_spike,
        "velocity_5m_count": velocity["5m_count"],
        "velocity_5m_amount": velocity["5m_amount"],
        "velocity_1h_count": velocity["1h_count"],
        "velocity_1h_amount": velocity["1h_amount"],
        "recipient_burst": recipient_burst,
        "recipient_5m_count": recipient_velocity["count"],
        "recipient_5m_amount": recipient_velocity["amount"],
        "cold_start": cold_start,
        "new_account": new_account,
        "device_age_days": round(device_age_days, 3),
        "frequency_spike": frequency_spike,
        "amount_deviation": amount_deviation,
        "unusual_hour": unusual_hour,
        "ip_address": ip_address,
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if location:
        transaction["location_latitude"], transaction["location_longitude"] = location

    assessment = fraud_detector.assess_transaction(
        transaction, dest_previous_count
    )
    risk_level = assessment["risk_level"]

    reference = f"MOMO-{uuid.uuid4().hex[:8].upper()}"
    transaction["transaction_reference"] = reference

    signals = {
        "device_change": device_change,
        "device_first_seen": not bool(device_first_seen),
        "device_age_days": round(device_age_days, 3),
        "new_account": new_account,
        "account_age_days": round(account_age_days, 3),
        "cold_start": cold_start,
        "ip_network_mismatch": ip_network_mismatch,
        "location_mismatch": location_mismatch,
        "impossible_travel": impossible_travel,
        "recipient_novel": recipient_novel_flag,
        "velocity_spike": velocity_spike,
        "velocity_5m_count": velocity["5m_count"],
        "velocity_5m_amount": round(velocity["5m_amount"], 2),
        "velocity_1h_count": velocity["1h_count"],
        "velocity_1h_amount": round(velocity["1h_amount"], 2),
        "recipient_burst": recipient_burst,
        "recipient_5m_count": recipient_velocity["count"],
        "recipient_5m_amount": round(recipient_velocity["amount"], 2),
        "frequency_spike": frequency_spike,
        "recent_sim_swap": recent_sim_swap,
        "amount_deviation": amount_deviation,
        "unusual_hour": unusual_hour,
        "scenarios": scenarios,
    }
    transaction["risk_signals"] = signals
    transaction["new_account"] = new_account
    transaction["device_age_days"] = round(device_age_days, 3)

    transaction["live_mtn"] = live_mtn
    outcome = _resolve_outcome(
        session_id, risk_level, assessment, reference, transaction,
        signals, amount, dest_ref, origin_ref, origin_new, dest_new,
        session["status"], session.get("otp_code"), ip_address, device_id,
        live_mtn=live_mtn,
        customer=customer,
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
                      live_mtn=False, customer=None):
    """Apply the fraud engine decision to the transaction session."""

    if risk_level == "LOW" or session_status == "STEP_UP":
        return _apply_outcome(
            session_id, risk_level, assessment, reference, transaction,
            signals, amount, dest_ref, origin_ref, origin_new, dest_new,
            ip_address, device_id, live_mtn=live_mtn, customer=customer
        )

    if risk_level == "MEDIUM":
        security_code_available = bool(
            customer and customer.get("step_up_code_hash")
        )
        passport_available = bool(
            customer
            and str(customer.get("id_type") or "").upper() == "PASSPORT"
            and (customer.get("id_hash") or customer.get("id_number"))
        )
        if not security_code_available and not passport_available:
            _finalize(
                "BLOCKED", "STEP_UP_UNAVAILABLE", transaction, assessment,
                ip_address, device_id
            )
            update_momo_session_status(
                session_id,
                "BLOCKED",
                risk_level=risk_level,
                risk_score=assessment["risk_score"],
                reason="No usable security code or passport verification method is available",
            )
            return {
                "status": "BLOCKED",
                "message": (
                    "This transaction was blocked because no verification "
                    "method is available. Set a security code or use an eligible "
                    "passport wallet. Money has NOT left your wallet."
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

        update_momo_session_status(
            session_id, "STEP_UP",
            reason="Additional verification required",
            otp_code=None,
            risk_level=risk_level, risk_score=assessment["risk_score"],
            risk_signals=json.dumps(signals, sort_keys=True),
            assessment_reasons=json.dumps(assessment["reasons"]),
        )
        events.publish(
            "transaction",
            {"reason": "step_up_required", "session_id": session_id},
        )
        events.publish("stats", {"reason": "step_up_required"})
        return {
            "status": "STEP_UP_REQUIRED",
            "message": "Suspicious activity detected. Verify with your security code or passport number.",
            "session_id": session_id,
            "step_up": True,
            "verification_method": "SECURITY_CODE_OR_PASSPORT",
            "security_code_available": security_code_available,
            "passport_available": passport_available,
            "reasons": assessment["reasons"],
            "risk_score": assessment["risk_score"],
            "signals": signals,
            "live_mtn": live_mtn,
            "id_type": (customer or {}).get("id_type", "GHANA_CARD"),
            "id_masked": (
                ((customer.get("id_number")[:4] + "***" + customer.get("id_number")[-2:])
                 if (customer and len(customer.get("id_number", "")) > 6)
                 else (customer or {}).get("id_number", ""))
            ),
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

    signal_names = [
        key for key, value in transaction.get("risk_signals", {}).items()
        if value is True
    ]
    audit_details = (
        f"Risk score: {assessment['risk_score']:.4f}; "
        f"decision: {assessment['decision']}; "
        f"signal flags: {', '.join(signal_names) or 'none'}"
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
                    ip_address, device_id, live_mtn=False, customer=None):
    """Bookkeeping after an APPROVED outcome (balance update + audit)."""

    mtn_transfer = None
    transfer_uncertain = False
    if live_mtn:
        try:
            target_phone = dest_ref
            matching_cust = next((c for c in CUSTOMERS if c["ref"] == dest_ref), None)
            if matching_cust and matching_cust.get("phone"):
                target_phone = matching_cust["phone"]
            mtn_recipient = normalize_ghana_msisdn(target_phone)
            if not mtn_recipient or len(mtn_recipient) < 9:
                raise ValueError("The recipient does not have a valid MTN wallet number.")
            mtn_client = get_mtn_disbursement_client()
            mtn_transfer = mtn_client.transfer(
                amount=format(amount, ".2f"),
                recipient_msisdn=mtn_recipient,
                external_id=reference,
                payer_message=f"MoMo {transaction.get('transaction_type', 'TRANSFER')} from {(customer or {}).get('name', 'Customer')}",
                payee_note="MoMo Sentinel Transfer",
            )
            transaction["mtn_reference_id"] = mtn_transfer.get("reference_id")
            transaction["mtn_financial_id"] = mtn_transfer.get("financialTransactionId")
        except MtnDisbursementError as err:
            if (
                err.status_code == 503
                and err.reference_id is None
                and "not configured" in str(err).lower()
            ):
                _finalize(
                    "NOT_SUBMITTED",
                    "MTN_TRANSFER_NOT_SUBMITTED",
                    transaction,
                    assessment,
                    ip_address,
                    device_id,
                )
                update_momo_session_status(
                    session_id,
                    "NOT_SUBMITTED",
                    risk_level=risk_level,
                    risk_score=assessment["risk_score"],
                    reason="MTN configuration is incomplete; no request was sent",
                )
                return {
                    "status": "NOT_SUBMITTED",
                    "message": (
                        "The transfer was not sent to MTN because sandbox "
                        f"configuration is incomplete. {err} No money was moved."
                    ),
                    "transaction_reference": reference,
                    "risk_score": assessment["risk_score"],
                    "risk_level": risk_level,
                    "decision": "MTN CONFIGURATION REQUIRED",
                    "reasons": assessment["reasons"],
                    "signals": signals,
                    "approved": False,
                    "live_mtn": True,
                }
            if not (err.status_code >= 500 and err.reference_id):
                _finalize(
                    "FAILED", "MTN_TRANSFER_FAILED", transaction, assessment,
                    ip_address, device_id
                )
                update_momo_session_status(
                    session_id, "FAILED",
                    risk_level=risk_level,
                    risk_score=assessment["risk_score"],
                    reason="MTN did not accept the transfer request",
                )
                return {
                    "status": "FAILED",
                    "message": f"MTN did not accept the transfer. {err}",
                    "transaction_reference": reference,
                    "risk_score": assessment["risk_score"],
                    "risk_level": risk_level,
                    "decision": "MTN TRANSFER FAILED",
                    "reasons": assessment["reasons"],
                    "signals": signals,
                    "approved": False,
                    "live_mtn": True,
                }
            mtn_transfer = {
                "reference_id": err.reference_id,
                "status": "PENDING",
                "amount": format(amount, ".2f"),
                "currency": "EUR",
                "recipient_msisdn": normalize_ghana_msisdn(target_phone),
            }
            transfer_uncertain = True

    if live_mtn:
        transaction["mtn_reference_id"] = mtn_transfer.get("reference_id")
        _finalize(
            "PENDING", "MTN_TRANSFER_ACCEPTED", transaction, assessment,
            ip_address, device_id
        )
        update_momo_session_status(
            session_id,
            "PENDING_MTN",
            risk_level=risk_level,
            risk_score=assessment["risk_score"],
            reason="Awaiting MTN transfer status",
            mtn_reference_id=mtn_transfer["reference_id"],
            transaction_reference=reference,
        )
        return {
            "status": "PENDING",
            "message": (
                (
                    "The MTN request outcome is uncertain. Check this transfer's "
                    "status before trying again."
                    if transfer_uncertain
                    else
                    "MTN accepted the transfer request. Its final status is pending; "
                    "check the transfer status before trying again."
                )
            ),
            "transaction_reference": reference,
            "reference_id": mtn_transfer["reference_id"],
            "amount": amount,
            "recipient": dest_ref,
            "risk_score": assessment["risk_score"],
            "risk_level": risk_level,
            "decision": assessment["decision"],
            "reasons": assessment["reasons"],
            "signals": signals,
            "approved": False,
            "step_up_completed": risk_level != "LOW",
            "live_mtn": True,
            "mtn_transfer": mtn_transfer,
        }

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
    trust_customer_security_signal(session_id)

    result = {
        "status": "APPROVED",
        "message": (
            "Transaction completed and transfer sent to MTN."
            if live_mtn
            else (
                "Transaction completed successfully."
                if risk_level == "LOW"
                else "Transaction approved after step-up verification."
            )
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
        "live_mtn": live_mtn,
    }
    return result


def check_mtn_transfer_status(session_id):
    """Reconcile a sandbox transfer and apply balance changes only on success."""
    session = get_momo_session(session_id)
    if not session or not session.get("live_mtn"):
        raise ValueError("MTN transfer session was not found.")
    if session["status"] in ("COMPLETED", "FAILED"):
        return {"status": session["status"], "reference_id": session.get("mtn_reference_id")}
    if session["status"] != "PENDING_MTN" or not session.get("mtn_reference_id"):
        raise ValueError("This transfer does not have a pending MTN status to check.")

    result = get_mtn_disbursement_client().get_transfer_status(
        session["mtn_reference_id"]
    )
    provider_status = result["status"].upper()
    if provider_status == "SUCCESSFUL":
        if not update_mtn_transaction_status(
            session["mtn_reference_id"],
            "COMPLETED",
            result.get("financialTransactionId"),
        ):
            raise RuntimeError("The accepted MTN transfer record could not be reconciled.")
        update_momo_session_status(
            session_id,
            "COMPLETED",
            reason="MTN confirmed the transfer",
        )
        trust_customer_security_signal(session_id)
        customer = _ensure_customer(session["customer_ref"])
        amount = float(session["amount"])
        recipient = session["recipient"]
        BALANCES[customer["ref"]] = max(
            0.0, BALANCES.get(customer["ref"], customer["balance"]) - amount
        )
        BALANCES[recipient] = BALANCES.get(recipient, 0.0) + amount
        return {**result, "status": "COMPLETED"}
    if provider_status in ("FAILED", "REJECTED"):
        if not update_mtn_transaction_status(session["mtn_reference_id"], "FAILED"):
            raise RuntimeError("The failed MTN transfer record could not be reconciled.")
        update_momo_session_status(
            session_id,
            "FAILED",
            reason="MTN reported that the transfer failed",
        )
        return {**result, "status": "FAILED"}
    return {**result, "status": "PENDING"}


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
