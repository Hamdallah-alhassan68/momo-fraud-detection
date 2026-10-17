import hashlib
import hmac as hmac_module
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import json

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_PATH = BASE_DIR / "fraud.db"
GHANA_TIMEZONE = ZoneInfo("Africa/Accra")
logger = logging.getLogger(__name__)

# ==========================================================
# AUDIT CHAIN (tamper-evident append-only log)
# ==========================================================
#
# Every audit entry stores a SHA-256 hash over its own contents plus the hash
# of the previous entry (a hash chain), and an HMAC-SHA256 signature over that
# hash using a server-side secret. Recomputing the chain detects any insertion,
# reordering, or modification of signed rows. Rows written before this feature
# (or by direct SQL in tests) carry NULL hashes and are reported as "legacy
# unsigned" entries rather than silently treated as verified.

AUDIT_GENESIS = "GENESIS"

# Key used to sign the chain. Prefer MOMO_AUDIT_HMAC_KEY; fall back to
# MOMO_AUTH_SECRET; otherwise use a random key that changes on restart (the
# chain is still internally consistent for the current process lifetime).
def _audit_hmac_key():
    configured = os.getenv("MOMO_AUDIT_HMAC_KEY") or os.getenv("MOMO_AUTH_SECRET")
    if configured:
        return configured.encode("utf-8")
    if not hasattr(_audit_hmac_key, "_ephemeral_key"):
        _audit_hmac_key._ephemeral_key = secrets.token_hex(32).encode("utf-8")
    return _audit_hmac_key._ephemeral_key


def _audit_canonical(transaction_id, action, performed_by, details, timestamp, prev_hash):
    """Deterministic string used to compute the entry hash for the chain."""
    return "|".join(
        [
            "" if transaction_id is None else str(transaction_id),
            "" if action is None else str(action),
            "" if performed_by is None else str(performed_by),
            "" if details is None else str(details),
            "" if timestamp is None else str(timestamp),
            "" if prev_hash is None else str(prev_hash),
        ]
    )


def _audit_entry_hash(transaction_id, action, performed_by, details, timestamp, prev_hash):
    return hashlib.sha256(
        _audit_canonical(
            transaction_id, action, performed_by, details, timestamp, prev_hash
        ).encode("utf-8")
    ).hexdigest()


def _audit_sign(entry_hash):
    return hmac_module.new(
        _audit_hmac_key(), entry_hash.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _last_audit_hash(connection):
    row = connection.execute(
        "SELECT entry_hash FROM audit_logs WHERE entry_hash IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None or not row["entry_hash"]:
        return AUDIT_GENESIS
    return row["entry_hash"]


def verify_audit_chain():
    """Walk the audit log and verify the hash chain + HMAC signatures.

    Rows with a NULL `entry_hash` are legacy/unsigned rows written before the
    chain existed (or by direct SQL); they are counted but not verified. The
    chain is considered valid when every signed row recomputes to its stored
    hash and signature, and each signed row links to the previous signed hash.
    """
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, transaction_id, action, performed_by, details, timestamp,
                   prev_hash, entry_hash, hmac_signature
            FROM audit_logs
            ORDER BY id ASC
            """
        ).fetchall()

        signed_count = 0
        legacy_count = 0
        expected_prev_hash = AUDIT_GENESIS
        first_broken_id = None
        broken_reason = None

        for row in rows:
            entry_hash = row["entry_hash"]
            if not entry_hash:
                legacy_count += 1
                continue

            signed_count += 1
            if row["prev_hash"] != expected_prev_hash:
                first_broken_id = row["id"]
                broken_reason = "prev_hash_mismatch"
                break

            recomputed = _audit_entry_hash(
                row["transaction_id"],
                row["action"],
                row["performed_by"],
                row["details"],
                row["timestamp"],
                expected_prev_hash,
            )
            if recomputed != entry_hash:
                first_broken_id = row["id"]
                broken_reason = "hash_mismatch"
                break
            if row["hmac_signature"] != _audit_sign(entry_hash):
                first_broken_id = row["id"]
                broken_reason = "signature_mismatch"
                break
            expected_prev_hash = entry_hash

        return {
            "valid": first_broken_id is None,
            "signed_count": signed_count,
            "legacy_unsigned_count": legacy_count,
            "first_broken_id": first_broken_id,
            "broken_reason": broken_reason,
            "chain_head": expected_prev_hash if first_broken_id is None else None,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        connection.close()



def get_connection():
    """
    Create a connection to the SQLite database.
    """

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = sqlite3.Row

    return connection


def _ensure_column(connection, table, column, definition):
    """Add a column to an existing table when it is missing (migration)."""
    columns = {
        row["name"]
        for row in connection.execute(
            f"PRAGMA table_info({table})"
        )
    }

    if column not in columns:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def initialize_database():
    """
    Create all database tables if they do not already exist.
    """

    logger.debug("Using SQLite database at %s", DATABASE_PATH.resolve())
    connection = get_connection()

    cursor = connection.cursor()

    # ==================================================
    # TRANSACTIONS
    # ==================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            transaction_reference TEXT UNIQUE NOT NULL,

            amount REAL NOT NULL,

            transaction_type TEXT NOT NULL,

            origin_account TEXT,

            destination_account TEXT,

            old_balance_origin REAL DEFAULT 0,

            new_balance_origin REAL DEFAULT 0,

            old_balance_destination REAL DEFAULT 0,

            new_balance_destination REAL DEFAULT 0,

            recipient_new INTEGER DEFAULT 0,

            device_change INTEGER DEFAULT 0,

            ip_network_mismatch INTEGER DEFAULT 0,

            recent_sim_swap INTEGER DEFAULT 0,

            velocity_spike INTEGER DEFAULT 0,

            frequency_spike INTEGER DEFAULT 0,

            scenario TEXT DEFAULT 'NORMAL',

            timestamp TEXT NOT NULL,

            created_at TEXT NOT NULL
        )
        """
    )
    
        # ==================================================
    # FRAUD CASES
    # ==================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fraud_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            transaction_id INTEGER NOT NULL,

            alert_id INTEGER,

            case_status TEXT DEFAULT 'OPEN',

            assigned_to TEXT,

            analyst_decision TEXT,

            analyst_notes TEXT,

            contact_channel TEXT,

            contact_outcome TEXT,

            contact_at TEXT,

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL,

            resolved_at TEXT,

            FOREIGN KEY (
                transaction_id
            )
            REFERENCES transactions(id),

            FOREIGN KEY (
                alert_id
            )
            REFERENCES fraud_alerts(id)
        )
        """
    )

    # ==================================================
    # RISK ASSESSMENTS
    # ==================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            transaction_id INTEGER NOT NULL,

            risk_score REAL NOT NULL,

            risk_level TEXT NOT NULL,

            decision TEXT NOT NULL,

            isolation_forest_score REAL,

            rule_score REAL,

            reasons TEXT,

            model_version TEXT DEFAULT 'V3',

            created_at TEXT NOT NULL,

            FOREIGN KEY (
                transaction_id
            )
            REFERENCES transactions(id)
        )
        """
    )

    # ==================================================
    # FRAUD ALERTS
    # ==================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fraud_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            transaction_id INTEGER NOT NULL,

            severity TEXT NOT NULL,

            status TEXT DEFAULT 'OPEN',

            reason TEXT,

            created_at TEXT NOT NULL,

            resolved_at TEXT,

            FOREIGN KEY (
                transaction_id
            )
            REFERENCES transactions(id)
        )
        """
    )

    # ==================================================
    # AUDIT LOGS
    # ==================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            transaction_id INTEGER,

            action TEXT NOT NULL,

            performed_by TEXT,

            details TEXT,

            timestamp TEXT NOT NULL,

            prev_hash TEXT,

            entry_hash TEXT,

            hmac_signature TEXT,

            FOREIGN KEY (
                transaction_id
            )
            REFERENCES transactions(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS audit_logs_reject_update
        BEFORE UPDATE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'audit_logs are append-only');
        END
        """
    )

    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS audit_logs_reject_delete
        BEFORE DELETE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'audit_logs are append-only');
        END
        """
    )

    # ==================================================
    # MODEL VERSIONS
    # ==================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS model_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            version TEXT UNIQUE NOT NULL,

            model_type TEXT NOT NULL,

            roc_auc REAL,

            pr_auc REAL,

            precision_score REAL,

            recall_score REAL,

            f1_score REAL,

            status TEXT DEFAULT 'ACTIVE',

            samples_count INTEGER DEFAULT 0,

            created_at TEXT NOT NULL
        )
        """
    )

    try:
        cursor.execute("ALTER TABLE model_versions ADD COLUMN samples_count INTEGER DEFAULT 0")
    except Exception:
        pass

    # ==================================================
    # MOMO SESSIONS (simulated mobile-money flow)
    # ==================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS momo_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            session_id TEXT UNIQUE NOT NULL,

            customer_ref TEXT NOT NULL,

            transaction_type TEXT NOT NULL,

            amount REAL NOT NULL,

            recipient TEXT NOT NULL,

            verification_method TEXT NOT NULL,

            otp_code TEXT,

            device_id TEXT,

            ip_address TEXT,

            status TEXT DEFAULT 'PENDING',

            step_up_count INTEGER DEFAULT 0,

            reason TEXT,

            risk_level TEXT,

            risk_score REAL,

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL
        )
        """
    )
    # ==================================================
    # INDEXES
    # ==================================================

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_transactions_destination
        ON transactions(destination_account)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_transactions_reference
        ON transactions(transaction_reference)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_risk_transaction
        ON risk_assessments(transaction_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_alert_status
        ON fraud_alerts(status)
        """
    )

    # --------------------------------------------------
    # MIGRATIONS (add columns to pre-existing databases)
    # --------------------------------------------------

    _ensure_column(
        connection, "transactions", "ip_address", "TEXT"
    )
    _ensure_column(
        connection, "transactions", "device_id", "TEXT"
    )
    _ensure_column(
        connection, "transactions", "status", "TEXT DEFAULT 'SCORED'"
    )
    _ensure_column(connection, "transactions", "scenario", "TEXT DEFAULT 'NORMAL'")
    _ensure_column(connection, "transactions", "frequency_spike", "INTEGER DEFAULT 0")
    _ensure_column(connection, "fraud_cases", "contact_channel", "TEXT")
    _ensure_column(connection, "fraud_cases", "contact_outcome", "TEXT")
    _ensure_column(connection, "fraud_cases", "contact_at", "TEXT")
    _ensure_column(connection, "momo_sessions", "scenario", "TEXT DEFAULT 'NORMAL'")
    _ensure_column(connection, "audit_logs", "prev_hash", "TEXT")
    _ensure_column(connection, "audit_logs", "entry_hash", "TEXT")
    _ensure_column(connection, "audit_logs", "hmac_signature", "TEXT")

    # --------------------------------------------------
    # ADMIN USERS (named-user authentication + roles)
    # --------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            username TEXT UNIQUE NOT NULL,

            display_name TEXT,

            password_hash TEXT NOT NULL,

            role TEXT NOT NULL DEFAULT 'viewer',

            is_active INTEGER DEFAULT 1,

            failed_login_count INTEGER DEFAULT 0,

            locked_until TEXT,

            last_login_at TEXT,

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL
        )
        """
    )

    # --------------------------------------------------
    # ADAPTIVE FRAUD INTELLIGENCE (monitoring snapshots)
    # --------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS account_status (
            customer_ref TEXT PRIMARY KEY,

            frozen INTEGER DEFAULT 0,

            reason TEXT,

            frozen_at TEXT,

            updated_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS model_monitoring (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            day TEXT UNIQUE NOT NULL,

            total_transactions INTEGER DEFAULT 0,

            anomaly_rate REAL DEFAULT 0,

            high_rate REAL DEFAULT 0,

            medium_rate REAL DEFAULT 0,

            avg_risk REAL DEFAULT 0,

            avg_amount REAL DEFAULT 0,

            psi_amount REAL DEFAULT 0,

            psi_risk REAL DEFAULT 0,

            app_decisions TEXT,

            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS model_drift_baseline (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            samples TEXT NOT NULL,
            transaction_count INTEGER NOT NULL DEFAULT 0,
            recorded_by TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )

    connection.commit()

    connection.close()


# ======================================================
# TRANSACTION HISTORY
# ======================================================

def get_recipient_history(destination_account):
    """
    Return the number of previous transactions
    involving this destination account.
    """

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE destination_account = ?
        """,
        (destination_account,)
    )

    result = cursor.fetchone()

    connection.close()

    return int(result["count"])



def create_transaction(transaction):
    """
    Store a transaction safely in the database.
    """
    connection = get_connection()

    try:
        cursor = connection.cursor()

        now = datetime.now(timezone.utc).isoformat()

        cursor.execute(
            """
            INSERT INTO transactions (
                transaction_reference,
                amount,
                transaction_type,
                origin_account,
                destination_account,
                old_balance_origin,
                new_balance_origin,
                old_balance_destination,
                new_balance_destination,
                recipient_new,
                device_change,
                ip_network_mismatch,
                recent_sim_swap,
                velocity_spike,
                frequency_spike,
                scenario,
                timestamp,
                created_at,
                ip_address,
                device_id,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction["transaction_reference"],
                transaction["amount"],
                transaction["transaction_type"],
                transaction.get("origin_account"),
                transaction.get("destination_account"),
                transaction.get("old_balance_origin", 0),
                transaction.get("new_balance_origin", 0),
                transaction.get("old_balance_destination", 0),
                transaction.get("new_balance_destination", 0),
                int(transaction.get("recipient_new", False)),
                int(transaction.get("device_change", False)),
                int(transaction.get("ip_network_mismatch", False)),
                int(transaction.get("recent_sim_swap", False)),
                int(transaction.get("velocity_spike", False)),
                int(transaction.get("frequency_spike", False)),
                transaction.get("scenario", "NORMAL"),
                transaction.get("timestamp", now),
                now,
                transaction.get("ip_address"),
                transaction.get("device_id"),
                transaction.get("status", "SCORED")
            )
        )

        transaction_id = cursor.lastrowid
        connection.commit()

        return transaction_id

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# ======================================================
# RISK ASSESSMENT
# ======================================================

def create_risk_assessment(
    transaction_id,
    assessment
):
    """
    Store the model's risk assessment.
    """

    import json

    connection = get_connection()

    cursor = connection.cursor()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    cursor.execute(
        """
        INSERT INTO risk_assessments (
            transaction_id,
            risk_score,
            risk_level,
            decision,
            isolation_forest_score,
            rule_score,
            reasons,
            model_version,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            assessment["risk_score"],
            assessment["risk_level"],
            assessment["decision"],
            assessment[
                "isolation_forest_score"
            ],
            assessment["rule_score"],
            json.dumps(
                assessment["reasons"]
            ),
            "V3",
            now
        )
    )

    assessment_id = cursor.lastrowid

    connection.commit()

    connection.close()

    return assessment_id


# ======================================================
# FRAUD ALERT
# ======================================================

def create_fraud_alert(
    transaction_id,
    severity,
    reasons
):
    """
    Create an alert for medium/high-risk
    transactions.
    """

    import json

    connection = get_connection()

    cursor = connection.cursor()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    cursor.execute(
        """
        INSERT INTO fraud_alerts (
            transaction_id,
            severity,
            status,
            reason,
            created_at
        )
        VALUES (?, ?, 'OPEN', ?, ?)
        """,
        (
            transaction_id,
            severity,
            json.dumps(reasons),
            now
        )
    )

    alert_id = cursor.lastrowid

    connection.commit()

    connection.close()

    return alert_id


# ======================================================
# FRAUD CASE
# ======================================================

def create_fraud_case(
    transaction_id,
    alert_id=None
):
    """
    Create an open fraud investigation case.
    """

    connection = get_connection()

    cursor = connection.cursor()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    cursor.execute(
        """
        INSERT INTO fraud_cases (
            transaction_id,
            alert_id,
            case_status,
            created_at,
            updated_at
        )
        VALUES (?, ?, 'OPEN', ?, ?)
        """,
        (
            transaction_id,
            alert_id,
            now,
            now
        )
    )

    case_id = cursor.lastrowid

    connection.commit()

    connection.close()

    return case_id
# ======================================================
# AUDIT LOG
# ======================================================

def create_audit_log(
    transaction_id,
    action,
    performed_by,
    details
):
    """
    Store an audit event in the append-only audit log.

    Each entry is chained to the previous entry with a SHA-256 hash and signed
    with an HMAC-SHA256 signature so modifications are detectable (see
    `verify_audit_chain`). Legacy rows written before this feature have NULL
    hash columns.
    """

    connection = get_connection()

    cursor = connection.cursor()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    prev_hash = _last_audit_hash(connection)
    entry_hash = _audit_entry_hash(
        transaction_id, action, performed_by, details, now, prev_hash
    )
    signature = _audit_sign(entry_hash)

    cursor.execute(
        """
        INSERT INTO audit_logs (
            transaction_id,
            action,
            performed_by,
            details,
            timestamp,
            prev_hash,
            entry_hash,
            hmac_signature
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            action,
            performed_by,
            details,
            now,
            prev_hash,
            entry_hash,
            signature,
        )
    )

    connection.commit()

    connection.close()


def get_fraud_cases(include_resolved=False):
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT
                c.id AS case_id,
                c.transaction_id,
                t.transaction_reference,
                t.amount,
                t.transaction_type,
                t.origin_account,
                t.destination_account,
                CASE
                    WHEN t.transaction_type = 'CASH_IN'
                        THEN t.destination_account
                    ELSE t.origin_account
                END AS customer_ref,
                COALESCE(account_status.frozen, 0) AS account_frozen,
                account_status.reason AS account_freeze_reason,
                account_status.frozen_at AS account_frozen_at,
                t.ip_address,
                t.device_id,
                r.risk_score,
                r.risk_level,
                a.severity,
                a.reason AS fraud_reasons,
                c.case_status,
                c.assigned_to,
                c.analyst_decision,
                c.analyst_notes,
                c.contact_channel,
                c.contact_outcome,
                c.contact_at,
                c.created_at,
                c.updated_at,
                c.resolved_at
            FROM fraud_cases c
            JOIN transactions t
                ON t.id = c.transaction_id
            LEFT JOIN risk_assessments r
                ON r.transaction_id = t.id
            LEFT JOIN fraud_alerts a
                ON a.id = c.alert_id
            LEFT JOIN account_status
                ON account_status.customer_ref = CASE
                    WHEN t.transaction_type = 'CASH_IN'
                        THEN t.destination_account
                    ELSE t.origin_account
                END
            WHERE (? = 1 OR c.case_status IN ('OPEN', 'INVESTIGATING'))
            ORDER BY c.created_at DESC
        """, (int(include_resolved),))

        cases = []

        for row in cursor.fetchall():
            case = dict(row)
            case["account_frozen"] = bool(case["account_frozen"])

            raw_reasons = case.get("fraud_reasons")

            if raw_reasons:
                try:
                    case["fraud_reasons"] = json.loads(raw_reasons)
                except (json.JSONDecodeError, TypeError):
                    case["fraud_reasons"] = [str(raw_reasons)]
            else:
                case["fraud_reasons"] = []

            cases.append(case)

        return cases

    finally:
        connection.close()

def update_fraud_case(case_id, updates):
    allowed_fields = {
        "case_status",
        "assigned_to",
        "analyst_decision",
        "analyst_notes"
    }

    allowed_statuses = {
        "OPEN",
        "INVESTIGATING",
        "RESOLVED"
    }

    if not updates:
        raise ValueError("At least one field must be provided.")

    invalid_fields = set(updates.keys()) - allowed_fields
    if invalid_fields:
        raise ValueError(
            f"Unsupported fields: {', '.join(sorted(invalid_fields))}"
        )

    if "case_status" in updates:
        if updates["case_status"] not in allowed_statuses:
            raise ValueError(
                "case_status must be OPEN, INVESTIGATING, or RESOLVED."
            )

    if "analyst_decision" in updates:
        allowed_decisions = {
            "FRAUD_CONFIRMED",
            "LEGITIMATE",
            "INCONCLUSIVE",
        }
        if updates["analyst_decision"] not in allowed_decisions:
            raise ValueError(
                "analyst_decision must be FRAUD_CONFIRMED, "
                "LEGITIMATE, or INCONCLUSIVE."
            )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            "SELECT transaction_id FROM fraud_cases WHERE id = ?",
            (case_id,)
        )
        case = cursor.fetchone()

        if case is None:
            return None

        transaction_id = case["transaction_id"]

        fields = list(updates.keys())
        assignments = [f"{field} = ?" for field in fields]
        values = [updates[field] for field in fields]

        now = datetime.now(timezone.utc).isoformat()
        assignments.append("updated_at = ?")
        values.append(now)

        if "case_status" in updates:
            if updates["case_status"] == "RESOLVED":
                assignments.append("resolved_at = ?")
                values.append(now)
            else:
                assignments.append("resolved_at = NULL")

        values.append(case_id)

        cursor.execute(
            f"""
            UPDATE fraud_cases
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            values
        )
        connection.commit()
        return transaction_id

    finally:
        connection.close()


def record_fraud_case_contact(case_id, channel, outcome):
    allowed_channels = {"CALL", "SMS"}
    allowed_outcomes = {"CONFIRMED", "DECLINED", "NO_ANSWER"}
    if channel not in allowed_channels:
        raise ValueError("channel must be CALL or SMS.")
    if outcome not in allowed_outcomes:
        raise ValueError(
            "outcome must be CONFIRMED, DECLINED, or NO_ANSWER."
        )

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT c.transaction_id, c.case_status, c.analyst_decision
            FROM fraud_cases c
            WHERE c.id = ?
            """,
            (case_id,),
        )
        case = cursor.fetchone()
        if case is None:
            return None
        if case["case_status"] == "RESOLVED":
            raise ValueError("Resolved cases cannot record further contact.")
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            UPDATE fraud_cases
            SET contact_channel = ?, contact_outcome = ?, contact_at = ?,
                updated_at = ?, case_status = 'INVESTIGATING'
            WHERE id = ?
            """,
            (channel, outcome, now, now, case_id),
        )
        connection.commit()
        return {
            "transaction_id": case["transaction_id"],
            "contact_channel": channel,
            "contact_outcome": outcome,
            "contact_at": now,
        }
    finally:
        connection.close()


def freeze_account_for_fraud_case(case_id):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                c.transaction_id,
                c.case_status,
                c.analyst_decision,
                c.contact_outcome,
                r.risk_level,
                t.transaction_type,
                CASE
                    WHEN t.transaction_type = 'CASH_IN'
                        THEN t.destination_account
                    ELSE t.origin_account
                END AS customer_ref
            FROM fraud_cases c
            JOIN transactions t ON t.id = c.transaction_id
            LEFT JOIN risk_assessments r ON r.transaction_id = t.id
            WHERE c.id = ?
            """,
            (case_id,),
        )
        case = cursor.fetchone()
        if case is None:
            return None
        if case["analyst_decision"] != "FRAUD_CONFIRMED":
            raise ValueError(
                "Fraud must be confirmed before freezing an account."
            )
        if (
            case["risk_level"] != "HIGH"
            and case["contact_outcome"] != "CONFIRMED"
        ):
            raise ValueError(
                "Record the customer's explicit consent before freezing a non-high-risk account."
            )

        customer_ref = case["customer_ref"]
        if not customer_ref or not customer_ref.startswith(("WALLET-", "W-")):
            raise ValueError(
                "The customer wallet for this case could not be identified."
            )

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            INSERT INTO account_status (
                customer_ref, frozen, reason, frozen_at, updated_at
            )
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(customer_ref) DO UPDATE SET
                frozen = 1,
                reason = excluded.reason,
                frozen_at = excluded.frozen_at,
                updated_at = excluded.updated_at
            """,
            (customer_ref, f"ADMIN_FRAUD_CASE_{case_id}", now, now),
        )
        connection.commit()
        return {
            "transaction_id": case["transaction_id"],
            "customer_ref": customer_ref,
            "risk_level": case["risk_level"],
            "contact_outcome": case["contact_outcome"],
            "account_frozen": True,
        }
    finally:
        connection.close()


def unfreeze_account_for_fraud_case(case_id):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                c.transaction_id,
                CASE
                    WHEN t.transaction_type = 'CASH_IN'
                        THEN t.destination_account
                    ELSE t.origin_account
                END AS customer_ref
            FROM fraud_cases c
            JOIN transactions t ON t.id = c.transaction_id
            WHERE c.id = ?
            """,
            (case_id,),
        )
        case = cursor.fetchone()
        if case is None:
            return None

        customer_ref = case["customer_ref"]
        if not customer_ref or not customer_ref.startswith(("WALLET-", "W-")):
            raise ValueError(
                "The customer wallet for this case could not be identified."
            )

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            UPDATE account_status
            SET frozen = 0, reason = NULL, updated_at = ?
            WHERE customer_ref = ?
            """,
            (now, customer_ref),
        )
        connection.commit()
        return {
            "transaction_id": case["transaction_id"],
            "customer_ref": customer_ref,
            "account_frozen": False,
        }
    finally:
        connection.close()


def get_audit_logs():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT
                id,
                transaction_id,
                action,
                performed_by,
                details,
                timestamp,
                prev_hash,
                entry_hash,
                hmac_signature
            FROM audit_logs
            ORDER BY id DESC
        """)

        return [dict(row) for row in cursor.fetchall()]

    finally:
        connection.close()


def get_dashboard_stats():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT COUNT(*) AS total_transactions
            FROM transactions
        """)
        total_transactions = cursor.fetchone()["total_transactions"]

        cursor.execute("""
            SELECT COUNT(*) AS high_risk_transactions
            FROM (
                SELECT transaction_id
                FROM risk_assessments
                WHERE risk_level = 'HIGH'
                GROUP BY transaction_id
            )
        """)
        high_risk_transactions = cursor.fetchone()["high_risk_transactions"]

        cursor.execute("""
            SELECT
                SUM(CASE WHEN case_status = 'OPEN' THEN 1 ELSE 0 END)
                    AS open_cases,
                SUM(CASE WHEN case_status = 'INVESTIGATING' THEN 1 ELSE 0 END)
                    AS investigating_cases,
                SUM(CASE WHEN case_status = 'RESOLVED' THEN 1 ELSE 0 END)
                    AS resolved_cases
            FROM fraud_cases
        """)
        case_stats = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT(*) AS blocked FROM transactions WHERE status = 'BLOCKED'"
        )
        blocked = cursor.fetchone()["blocked"]
        cursor.execute(
            "SELECT COUNT(*) AS frozen_accounts FROM account_status WHERE frozen = 1"
        )
        frozen_accounts = cursor.fetchone()["frozen_accounts"]
        cursor.execute(
            """
            SELECT
                SUM(
                    CASE
                        WHEN frozen = 1 AND reason GLOB 'ADMIN_*'
                            THEN 1
                        ELSE 0
                    END
                ) AS admin_frozen_accounts,
                SUM(
                    CASE
                        WHEN frozen = 1 AND reason = 'FROZEN_BY_CUSTOMER'
                            THEN 1
                        ELSE 0
                    END
                ) AS customer_frozen_accounts
            FROM account_status
            """
        )
        freeze_stats = cursor.fetchone()

        return {
            "total_transactions": total_transactions,
            "high_risk_transactions": high_risk_transactions,
            "open_cases": (
                (case_stats["open_cases"] or 0)
                + (case_stats["investigating_cases"] or 0)
            ),
            "resolved_cases": case_stats["resolved_cases"] or 0,
            "blocked": blocked,
            "frozen_accounts": frozen_accounts,
            "admin_frozen_accounts": int(
                freeze_stats["admin_frozen_accounts"] or 0
            ),
            "customer_frozen_accounts": int(
                freeze_stats["customer_frozen_accounts"] or 0
            ),
        }

    finally:
        connection.close()


# ======================================================
# MOMO SESSIONS (simulated MoMo user flow)
# ======================================================

def create_momo_session(
    session_id,
    customer_ref,
    transaction_type,
    amount,
    recipient,
    verification_method,
    otp_code,
    device_id,
    ip_address,
    scenario: str = "NORMAL",
):
    """Create an in-flight transaction session for the simulated MoMo flow."""
    connection = get_connection()
    cursor = connection.cursor()
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        """
        INSERT INTO momo_sessions (
            session_id,
            customer_ref,
            transaction_type,
            amount,
            recipient,
            verification_method,
            otp_code,
            device_id,
            ip_address,
            scenario,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """,
        (
            session_id,
            customer_ref,
            transaction_type,
            amount,
            recipient,
            verification_method,
            otp_code,
            device_id,
            ip_address,
            scenario,
            now,
            now
        )
    )

    connection.commit()
    connection.close()

    return {
        "session_id": session_id,
        "verification_method": verification_method,
        "otp_code": otp_code
    }


def get_momo_session(session_id):
    """Fetch an in-flight transaction session or None."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM momo_sessions
            WHERE session_id = ?
            """,
            (session_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        connection.close()


def update_momo_session_status(session_id, status, **fields):
    """Update the status (and optional extra fields) of a session."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        fields["status"] = status
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()

        assignments = [f"{key} = ?" for key in fields]
        values = list(fields.values())
        values.append(session_id)

        cursor.execute(
            f"""
            UPDATE momo_sessions
            SET {", ".join(assignments)}
            WHERE session_id = ?
            """,
            values
        )
        connection.commit()
    finally:
        connection.close()


def count_velocity_sessions(customer_ref, window_minutes=3):
    """Number of sessions opened by this customer in the last N minutes."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM momo_sessions
            WHERE customer_ref = ? AND created_at >= ?
            """,
            (customer_ref, since.isoformat())
        )
        result = cursor.fetchone()
        return int(result["count"])
    finally:
        connection.close()


# ======================================================
# ANALYTICS (dashboard charts + live monitoring)
# ======================================================

def get_analytics_data(granularity="minute"):
    """Aggregate data used by the real-time admin dashboard.

    ``granularity`` selects the bucket size of the fraud-rate time series:
    "minute", "hour" or "day".
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        data = {}

        # ----------------------------------------------
        # Risk level distribution
        # ----------------------------------------------
        cursor.execute(
            "SELECT risk_level, COUNT(*) AS count "
            "FROM risk_assessments GROUP BY risk_level"
        )
        data["risk_distribution"] = {
            row["risk_level"]: row["count"]
            for row in cursor.fetchall()
        }

        # ----------------------------------------------
        # Transaction type distribution
        # ----------------------------------------------
        cursor.execute(
            "SELECT transaction_type, COUNT(*) AS count "
            "FROM transactions GROUP BY transaction_type"
        )
        data["type_distribution"] = {
            row["transaction_type"]: row["count"]
            for row in cursor.fetchall()
        }

        # ----------------------------------------------
        # Alert severity distribution
        # ----------------------------------------------
        cursor.execute(
            "SELECT severity, COUNT(*) AS count "
            "FROM fraud_alerts GROUP BY severity"
        )
        data["severity_distribution"] = {
            row["severity"]: row["count"]
            for row in cursor.fetchall()
}

        # ----------------------------------------------
        # Daily transaction trend (last 14 days)
        # ----------------------------------------------
        cursor.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count
            FROM transactions
            GROUP BY day
            ORDER BY day DESC
            LIMIT 14
            """
        )
        rows = list(reversed(cursor.fetchall()))
        data["daily_trend"] = [
            {"day": row["day"], "count": row["count"]}
            for row in rows
        ]

        # ----------------------------------------------
        # Totals
        # ----------------------------------------------
        def _count(query, params=()):
            cursor.execute(query, params)
            row = cursor.fetchone()
            return int(row[0])

        cursor.execute(
            "SELECT COALESCE(AVG(risk_score), 0) FROM risk_assessments"
        )
        average_risk_score = float(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT
                SUM(
                    CASE
                        WHEN frozen = 1 AND reason GLOB 'ADMIN_*'
                            THEN 1
                        ELSE 0
                    END
                ) AS admin_frozen_accounts,
                SUM(
                    CASE
                        WHEN frozen = 1 AND reason = 'FROZEN_BY_CUSTOMER'
                            THEN 1
                        ELSE 0
                    END
                ) AS customer_frozen_accounts
            FROM account_status
            """
        )
        freeze_stats = cursor.fetchone()
        admin_frozen_accounts = int(freeze_stats["admin_frozen_accounts"] or 0)
        customer_frozen_accounts = int(
            freeze_stats["customer_frozen_accounts"] or 0
        )

        data["totals"] = {
            "transactions": _count("SELECT COUNT(*) FROM transactions"),
            "assessments": _count("SELECT COUNT(*) FROM risk_assessments"),
            "alerts": _count("SELECT COUNT(*) FROM fraud_alerts"),
            "cases": _count("SELECT COUNT(*) FROM fraud_cases"),
            "open_cases": _count(
                "SELECT COUNT(*) FROM fraud_cases "
                "WHERE case_status IN ('OPEN', 'INVESTIGATING')"
            ),
            "resolved_cases": _count(
                "SELECT COUNT(*) FROM fraud_cases WHERE case_status = 'RESOLVED'"
            ),
            "avg_risk_score": round(average_risk_score, 4),
            "blocked": _count(
                "SELECT COUNT(*) FROM transactions WHERE status = 'BLOCKED'"
            ),
            "frozen_accounts": _count(
                "SELECT COUNT(*) FROM account_status WHERE frozen = 1"
            ),
            "admin_frozen_accounts": admin_frozen_accounts,
            "customer_frozen_accounts": customer_frozen_accounts,
        }

        # ----------------------------------------------
        # Top risk reasons (rule engine output)
        # ----------------------------------------------
        cursor.execute(
            "SELECT reasons FROM risk_assessments WHERE reasons IS NOT NULL"
        )
        reason_counter = Counter()
        for (raw,) in cursor.fetchall():
            try:
                for reason in json.loads(raw):
                    if reason:
                        reason_counter[reason] += 1
            except (json.JSONDecodeError, TypeError):
                continue

        data["top_reasons"] = [
            {"reason": reason, "count": count}
            for reason, count in reason_counter.most_common(10)
        ]

        # ----------------------------------------------
        # Recent transactions (with IP + device for investigation)
        # ----------------------------------------------
        cursor.execute(
            """
            SELECT
                t.id,
                t.transaction_reference,
                t.amount,
                t.transaction_type,
                t.origin_account,
                t.destination_account,
                t.ip_address,
                t.device_id,
                t.status,
                t.created_at,
                r.risk_level,
                r.risk_score
            FROM transactions t
            LEFT JOIN risk_assessments r
                ON r.transaction_id = t.id
            ORDER BY t.created_at DESC
            LIMIT 12
            """
        )
        data["recent_transactions"] = [dict(row) for row in cursor.fetchall()]

        return data

    finally:
        connection.close()


# ======================================================
# ADMIN USERS (named-user authentication + roles)
# ======================================================

VALID_ADMIN_ROLES = {"admin", "analyst", "viewer"}


def create_admin_user(username, display_name, password_hash, role="viewer"):
    """Create an admin-console user account. Returns the new user id."""
    if role not in VALID_ADMIN_ROLES:
        raise ValueError("role must be one of: admin, analyst, viewer.")
    connection = get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO admin_users (
                username, display_name, password_hash, role, is_active,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (username, display_name, password_hash, role, now, now),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_admin_user_by_username(username):
    """Return a single admin user row as a dict, or None."""
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, username, display_name, password_hash, role, is_active,
                   failed_login_count, locked_until, last_login_at, created_at,
                   updated_at
            FROM admin_users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def get_admin_user_by_id(user_id):
    """Return a single admin user row as a dict, or None."""
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, username, display_name, password_hash, role, is_active,
                   failed_login_count, locked_until, last_login_at, created_at,
                   updated_at
            FROM admin_users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def list_admin_users():
    """Return all admin users without their password hashes."""
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, username, display_name, role, is_active,
                   failed_login_count, locked_until, last_login_at, created_at,
                   updated_at
            FROM admin_users
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def count_admin_users():
    """Return the number of configured admin-console user accounts."""
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT COUNT(*) AS n FROM admin_users"
        ).fetchone()["n"]
    finally:
        connection.close()


def update_admin_user(
    user_id,
    display_name=None,
    password_hash=None,
    role=None,
    is_active=None,
    reset_login_failures=False,
):
    """Update editable fields of an admin user. Returns True when a row changed."""
    if role is not None and role not in VALID_ADMIN_ROLES:
        raise ValueError("role must be one of: admin, analyst, viewer.")
    connection = get_connection()
    try:
        assignments = []
        values = []
        if display_name is not None:
            assignments.append("display_name = ?")
            values.append(display_name)
        if password_hash is not None:
            assignments.append("password_hash = ?")
            values.append(password_hash)
        if role is not None:
            assignments.append("role = ?")
            values.append(role)
        if is_active is not None:
            assignments.append("is_active = ?")
            values.append(1 if is_active else 0)
        if reset_login_failures:
            assignments.append("failed_login_count = 0")
            assignments.append("locked_until = NULL")
        if not assignments:
            return False
        assignments.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(user_id)
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE admin_users
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            values,
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def record_admin_login_success(user_id):
    """Reset the failure counter and record the successful login time."""
    connection = get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            UPDATE admin_users
            SET failed_login_count = 0, locked_until = NULL,
                last_login_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, user_id),
        )
        connection.commit()
    finally:
        connection.close()


def record_admin_login_failure(username, max_attempts=5, lock_minutes=15):
    """Increment the failed-login counter for a username and lock account if max attempts reached."""
    connection = get_connection()
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT id, failed_login_count FROM admin_users WHERE username = ?",
            (username,),
        )
        row = cursor.fetchone()
        if not row:
            return

        new_count = (row["failed_login_count"] or 0) + 1
        locked_until = None
        if new_count >= max_attempts:
            locked_until = (now + timedelta(minutes=lock_minutes)).isoformat()

        cursor.execute(
            """
            UPDATE admin_users
            SET failed_login_count = ?, locked_until = ?, updated_at = ?
            WHERE username = ?
            """,
            (new_count, locked_until, now_iso, username),
        )
        connection.commit()
    finally:
        connection.close()


# ======================================================
# ADAPTIVE FRAUD INTELLIGENCE (monitoring data)
# ======================================================

def get_daily_transaction_stats(days=14):
    """Daily aggregates of transactions, risk and amount for drift monitoring."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                substr(t.created_at, 1, 10) AS day,
                COUNT(t.id) AS total,
                SUM(CASE WHEN r.risk_level = 'HIGH' THEN 1 ELSE 0 END) AS high,
                SUM(CASE WHEN r.risk_level = 'MEDIUM' THEN 1 ELSE 0 END)
                    AS medium,
                COALESCE(AVG(r.risk_score), 0) AS avg_risk,
                COALESCE(AVG(t.amount), 0) AS avg_amount
            FROM transactions t
            LEFT JOIN risk_assessments r
                ON r.transaction_id = t.id
            GROUP BY day
            ORDER BY day DESC
            LIMIT ?
            """,
            (days,)
        )

        stats = list(reversed(cursor.fetchall()))
        return [dict(row) for row in stats]

    finally:
        connection.close()


def get_recent_transaction_samples(days=14, limit=5000):
    """Return transaction-level samples for drift checks."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor.execute(
            """
            SELECT t.amount, r.risk_score, t.created_at
            FROM transactions t
            LEFT JOIN risk_assessments r
                ON r.transaction_id = t.id
            WHERE t.created_at >= ?
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT ?
            """,
            (since.isoformat(), limit)
        )
        return [dict(row) for row in reversed(cursor.fetchall())]

    finally:
        connection.close()


def get_model_drift_baseline():
    """Return the current distribution baseline, if an operator has recorded one."""
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT samples, transaction_count, recorded_by, recorded_at
            FROM model_drift_baseline
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return None

        baseline = dict(row)
        baseline["samples"] = json.loads(baseline["samples"])
        if not isinstance(baseline["samples"], dict):
            raise ValueError("Stored model drift baseline must be a JSON object.")
        return baseline
    finally:
        connection.close()


def record_model_drift_baseline(samples, transaction_count, recorded_by):
    """Replace the reference distributions used by adaptive PSI monitoring."""
    connection = get_connection()
    try:
        recorded_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO model_drift_baseline (
                id, samples, transaction_count, recorded_by, recorded_at
            )
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                samples = excluded.samples,
                transaction_count = excluded.transaction_count,
                recorded_by = excluded.recorded_by,
                recorded_at = excluded.recorded_at
            """,
            (
                json.dumps(samples),
                transaction_count,
                recorded_by,
                recorded_at,
            ),
        )
        connection.commit()
        return {
            "samples": samples,
            "transaction_count": transaction_count,
            "recorded_by": recorded_by,
            "recorded_at": recorded_at,
        }
    finally:
        connection.close()


def get_customer_baseline(customer_ref):
    """Return historical amount, timing, frequency, and recipient patterns."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT amount, timestamp, origin_account, destination_account,
                   transaction_type
            FROM transactions
            WHERE origin_account = ?
               OR (transaction_type = 'CASH_IN' AND destination_account = ?)
            ORDER BY timestamp
            """,
            (customer_ref, customer_ref)
        )
        rows = [dict(row) for row in cursor.fetchall()]
        amounts = [float(row["amount"] or 0) for row in rows]
        hour_counts = Counter()
        recipient_counts = Counter()
        day_counts = Counter()
        for row in rows:
            timestamp = row["timestamp"]
            if timestamp:
                try:
                    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    parsed = parsed.astimezone(GHANA_TIMEZONE)
                except ValueError:
                    continue
                hour_counts[parsed.hour] += 1
                day_counts[parsed.date().isoformat()] += 1
            recipient = row["destination_account"]
            if row["transaction_type"] == "CASH_IN":
                recipient = row["origin_account"]
            if recipient:
                recipient_counts[recipient] += 1

        now = datetime.now(GHANA_TIMEZONE)
        last_24h_count = 0
        for row in rows:
            try:
                parsed = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed = parsed.astimezone(GHANA_TIMEZONE)
                if parsed >= now - timedelta(hours=24):
                    last_24h_count += 1
            except (ValueError, AttributeError):
                continue
        return {
            "count": len(rows),
            "avg_amount": sum(amounts) / len(amounts) if amounts else 0.0,
            "max_amount": max(amounts, default=0.0),
            "usual_hours": [
                hour
                for hour, count in hour_counts.items()
                if count / max(len(rows), 1) >= 0.1
            ],
            "hour_counts": dict(hour_counts),
            "recipient_counts": dict(recipient_counts),
            "transactions_per_day": dict(day_counts),
            "avg_transactions_per_day": (
                sum(day_counts.values()) / len(day_counts) if day_counts else 0.0
            ),
            "last_24h_count": last_24h_count,
            "most_common_recipients": recipient_counts.most_common(5),
        }

    finally:
        connection.close()


def get_customer_recipient_history(customer_ref, recipient_ref):
    """Count only prior transactions between this customer and recipient."""
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM transactions
            WHERE (origin_account = ? AND destination_account = ?)
               OR (transaction_type = 'CASH_IN'
                   AND destination_account = ? AND origin_account = ?)
            """,
            (customer_ref, recipient_ref, customer_ref, recipient_ref),
        ).fetchone()
        return int(row["count"])
    finally:
        connection.close()


def get_analyst_decision_counts():
    """Provisional ground truth from analyst case decisions."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT analyst_decision, COUNT(*) AS count
            FROM fraud_cases
            WHERE analyst_decision IS NOT NULL AND analyst_decision != ''
            GROUP BY analyst_decision
            """
        )
        return {
            row["analyst_decision"]: row["count"]
            for row in cursor.fetchall()
        }

    finally:
        connection.close()


def get_resolved_analyst_outcome_trend(days=14):
    """Return daily resolved case outcomes for recent analyst feedback."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor.execute(
            """
            SELECT
                substr(resolved_at, 1, 10) AS day,
                SUM(CASE WHEN analyst_decision = 'FRAUD_CONFIRMED'
                    THEN 1 ELSE 0 END) AS confirmed,
                SUM(CASE WHEN analyst_decision = 'LEGITIMATE'
                    THEN 1 ELSE 0 END) AS legitimate,
                SUM(CASE WHEN analyst_decision = 'INCONCLUSIVE'
                    THEN 1 ELSE 0 END) AS inconclusive
            FROM fraud_cases
            WHERE case_status = 'RESOLVED'
              AND analyst_decision IN (
                  'FRAUD_CONFIRMED', 'LEGITIMATE', 'INCONCLUSIVE'
              )
              AND resolved_at >= ?
            GROUP BY substr(resolved_at, 1, 10)
            ORDER BY day
            """,
            (since,),
        )
        return [dict(row) for row in cursor.fetchall()]

    finally:
        connection.close()


def model_drift_logged_today(day):
    """Has a drift alert already been logged for this UTC day?"""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM audit_logs
            WHERE action = 'MODEL_DRIFT_DETECTED' AND details LIKE ?
            """,
            (f"{day}%",)
        )
        return int(cursor.fetchone()["count"]) > 0

    finally:
        connection.close()


def record_model_monitoring_snapshot(day, values):
    """Persist one daily monitoring snapshot (idempotent per day)."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            INSERT OR IGNORE INTO model_monitoring (
                day,
                total_transactions,
                anomaly_rate,
                high_rate,
                medium_rate,
                avg_risk,
                avg_amount,
                psi_amount,
                psi_risk,
                app_decisions,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                values.get("total_transactions", 0),
                values.get("anomaly_rate", 0.0),
                values.get("high_rate", 0.0),
                values.get("medium_rate", 0.0),
                values.get("avg_risk", 0.0),
                values.get("avg_amount", 0.0),
                values.get("psi_amount", 0.0),
                values.get("psi_risk", 0.0),
                json.dumps(values.get("decisions", {})),
                now
            )
        )
        connection.commit()

    finally:
        connection.close()


def get_model_monitoring_snapshots(limit=30):
    """Recent persisted snapshots for historical monitoring views."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM model_monitoring
            ORDER BY day DESC
            LIMIT ?
            """,
            (limit,)
        )

        snapshots = []
        for row in cursor.fetchall():
            value = dict(row)
            try:
                value["app_decisions"] = json.loads(
                    value.get("app_decisions") or "{}"
                )
            except (json.JSONDecodeError, TypeError):
                value["app_decisions"] = {}
            snapshots.append(value)

        return snapshots

    finally:
        connection.close()


# ======================================================
# ACCOUNT STATUS (self-service freeze control)
# ======================================================

def get_account_status(customer_ref):
    """Return the persistent freeze status of a customer account."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT frozen, reason, frozen_at
            FROM account_status
            WHERE customer_ref = ?
            """,
            (customer_ref,)
        )
        row = cursor.fetchone()

        if row is None:
            return {
                "frozen": False,
                "reason": None,
                "frozen_at": None,
            }

        return {
            "frozen": bool(row["frozen"]),
            "reason": row["reason"],
            "frozen_at": row["frozen_at"],
        }

    finally:
        connection.close()


def set_account_frozen(customer_ref, reason):
    """Freeze an account at the customer's request (persistent)."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            INSERT INTO account_status (
                customer_ref, frozen, reason, frozen_at, updated_at
            )
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(customer_ref) DO UPDATE SET
                frozen = 1,
                reason = excluded.reason,
                frozen_at = excluded.frozen_at,
                updated_at = excluded.updated_at
            """,
            (customer_ref, reason, now, now)
        )
        connection.commit()

    finally:
        connection.close()


def set_account_unfrozen(customer_ref):
    """Re-activate a customer-frozen account."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            UPDATE account_status
            SET frozen = 0, updated_at = ?
            WHERE customer_ref = ?
            """,
            (now, customer_ref)
        )
        connection.commit()

    finally:
        connection.close()


# ======================================================
# MODEL VERSIONING & RECENT TRANSACTIONS FEED
# ======================================================

def record_model_version(version, model_type, roc_auc=None, pr_auc=None,
                         precision_score=None, recall_score=None, f1_score=None,
                         status="ACTIVE", samples_count=None):
    """Record an updated or retrained model version in the database."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        now = datetime.now(timezone.utc).isoformat()
        if status == "ACTIVE":
            cursor.execute("UPDATE model_versions SET status = 'ARCHIVED' WHERE status = 'ACTIVE'")
        cursor.execute(
            """
            INSERT INTO model_versions (
                version, model_type, roc_auc, pr_auc,
                precision_score, recall_score, f1_score, status, samples_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (version, model_type, roc_auc, pr_auc, precision_score, recall_score, f1_score, status, samples_count or 0, now)
        )
        connection.commit()
    finally:
        connection.close()


def get_latest_model_version():
    """Retrieve the active model version details."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT * FROM model_versions
            WHERE model_type != 'PrototypeBaselineMarker'
            ORDER BY id DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def get_recent_transactions_feed(limit=5):
    """Retrieve the most recent transactions with their risk assessments for live monitoring."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                t.id,
                t.transaction_reference,
                t.amount,
                t.transaction_type,
                t.origin_account,
                t.destination_account,
                t.status,
                t.created_at,
                r.risk_score,
                r.risk_level,
                r.decision
            FROM transactions t
            LEFT JOIN risk_assessments r ON r.transaction_id = t.id
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()