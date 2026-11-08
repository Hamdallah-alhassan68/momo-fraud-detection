"""Ghana MoMo National ID / Passport KYC Registry Service.

Maintains the authoritative registry of Ghana Mobile Money subscribers linked to
their national identity documents (Ghana Card, Passport, Voter ID) and MoMo PINs.
Reads from and persists to backend/data/ghana_momo_kyc_registry.csv.
"""

import csv
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

KYC_CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "ghana_momo_kyc_registry.csv"

_REGISTRY_CACHE = {}


def _normalize_phone(phone_str):
    """Normalize phone number to local 10-digit format (e.g. 0241234567)."""
    digits = re.sub(r"\D", "", str(phone_str or ""))
    if len(digits) == 12 and digits.startswith("233"):
        return "0" + digits[3:]
    if len(digits) == 10 and digits.startswith("0"):
        return digits
    return digits


def load_kyc_registry(force_reload=False):
    """Load subscriber records from the CSV file into memory."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE and not force_reload:
        return _REGISTRY_CACHE

    if not KYC_CSV_PATH.exists():
        logger.warning("KYC CSV registry file not found at %s", KYC_CSV_PATH)
        return _REGISTRY_CACHE

    registry = {}
    try:
        with open(KYC_CSV_PATH, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ref = row.get("customer_ref", "").strip()
                phone = row.get("phone_number", "").strip()
                devices_raw = row.get("devices", "")
                devices = [d.strip() for d in devices_raw.split(",") if d.strip()]

                record = {
                    "phone_number": phone,
                    "customer_ref": ref,
                    "full_name": row.get("full_name", "").strip(),
                    "id_type": row.get("id_type", "GHANA_CARD").strip(),
                    "id_number": row.get("id_number", "").strip(),
                    "date_of_birth": row.get("date_of_birth", "").strip(),
                    "gender": row.get("gender", "").strip(),
                    "occupation": row.get("occupation", "").strip(),
                    "residential_address": row.get("residential_address", "").strip(),
                    "email": row.get("email", "").strip().lower(),
                    "email_verified": row.get("email_verified", "").strip().lower()
                    in ("true", "1", "yes", "verified"),
                    "pin": row.get("pin", "1234").strip(),
                    "balance": float(row.get("balance", 0.0) or 0.0),
                    "daily_limit": float(row.get("daily_limit", 10000.0) or 10000.0),
                    "tier": row.get("tier", "TIER_3_FULL_KYC").strip(),
                    "registration_date": row.get("registration_date", "").strip(),
                    "status": row.get("status", "ACTIVE").strip(),
                    "base_ip": row.get("base_ip", "192.168.1.1").strip(),
                    "devices": devices or ["MOBILE-AND-001"],
                }
                if ref:
                    registry[ref] = record
                if phone:
                    norm = _normalize_phone(phone)
                    registry[norm] = record
                    registry[phone] = record

        _REGISTRY_CACHE = registry
        logger.info("Loaded %d KYC subscriber entries from %s", len(registry), KYC_CSV_PATH)
    except Exception as error:
        logger.exception("Failed to load KYC registry from CSV: %s", error)

    return _REGISTRY_CACHE


def get_all_subscribers(mask_sensitive=True):
    """Return distinct subscriber records sorted by customer reference."""
    reg = load_kyc_registry()
    distinct = {}
    for item in reg.values():
        ref = item.get("customer_ref")
        if ref and ref not in distinct:
            copy = dict(item)
            if mask_sensitive:
                id_num = copy.get("id_number", "")
                if len(id_num) > 6:
                    copy["id_number_masked"] = id_num[:4] + "***" + id_num[-2:]
                else:
                    copy["id_number_masked"] = id_num
                copy.pop("pin", None)
            distinct[ref] = copy
    return list(distinct.values())


def find_subscriber(query):
    """Find a subscriber by customer_ref, phone number, or national ID number."""
    if not query:
        return None
    reg = load_kyc_registry()
    q = str(query).strip().upper()
    norm_phone = _normalize_phone(query)

    # 1. Exact match by ref or phone
    if q in reg:
        return reg[q]
    if norm_phone in reg:
        return reg[norm_phone]

    # 2. Match by id_number or partial phone
    for sub in reg.values():
        if sub.get("customer_ref", "").upper() == q:
            return sub
        if sub.get("id_number", "").upper() == q:
            return sub
        if _normalize_phone(sub.get("phone_number")) == norm_phone:
            return sub

    return None


def verify_subscriber_pin(query, pin):
    """Verify that the provided PIN matches the subscriber's registered MoMo PIN."""
    sub = find_subscriber(query)
    if not sub:
        return False, "Subscriber not found in Ghana KYC Registry."
    expected_pin = str(sub.get("pin", "")).strip()
    given_pin = str(pin or "").strip()
    if expected_pin == given_pin:
        return True, "PIN verified successfully."
    return False, "Incorrect MoMo PIN. Please check your 4-digit PIN."


def verify_id_document(query, id_number):
    """Verify subscriber identity using their Ghana Card or Passport number."""
    sub = find_subscriber(query)
    if not sub:
        return False, "Subscriber not found in Ghana KYC Registry."

    expected_id = re.sub(r"[\s\-]", "", str(sub.get("id_number", "")).upper())
    given_id = re.sub(r"[\s\-]", "", str(id_number or "").upper())

    if expected_id == given_id or expected_id.endswith(given_id) or (len(given_id) >= 4 and given_id in expected_id):
        return True, f"Verified identity: {sub.get('id_type')} matched {sub.get('full_name')}."
    return False, f"ID number did not match the registered {sub.get('id_type')} on file."


def update_subscriber_email(customer_ref, email, verified=False):
    """Update subscriber email and its explicit verification state."""
    clean_email = str(email or "").strip().lower()
    if "@" not in clean_email or "." not in clean_email.partition("@")[2]:
        raise ValueError("Enter a valid email address.")

    reg = load_kyc_registry()
    sub = find_subscriber(customer_ref)
    if not sub:
        raise ValueError(f"Subscriber {customer_ref} not found.")

    previous_email = sub.get("email", "")
    previous_verified = bool(sub.get("email_verified", False))
    sub["email"] = clean_email
    sub["email_verified"] = bool(verified)

    # Save changes back to CSV
    try:
        distinct = {}
        for item in reg.values():
            r = item.get("customer_ref")
            if r and r not in distinct:
                distinct[r] = item

        if distinct and KYC_CSV_PATH.exists():
            fieldnames = [
                "phone_number", "customer_ref", "full_name", "id_type", "id_number",
                "date_of_birth", "gender", "occupation", "residential_address",
                "email", "pin", "balance", "daily_limit", "tier", "registration_date",
                "status", "base_ip", "devices", "email_verified"
            ]
            with open(KYC_CSV_PATH, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for item in distinct.values():
                    row = dict(item)
                    if isinstance(row.get("devices"), list):
                        row["devices"] = ",".join(row["devices"])
                    writer.writerow({k: row.get(k, "") for k in fieldnames})
            logger.info("Persisted email update for %s to %s", customer_ref, KYC_CSV_PATH)
    except Exception as error:
        sub["email"] = previous_email
        sub["email_verified"] = previous_verified
        logger.exception("Could not persist updated email to CSV")
        raise RuntimeError("Could not save the verified email address.") from error

    return sub
