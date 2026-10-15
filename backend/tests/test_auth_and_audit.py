"""Tests for named-user authentication, RBAC authorization, and tamper-evident audit chaining."""

import asyncio
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import httpx

import database.database as database
from app import app
from services import auth


class PasswordAndTokenTests(unittest.TestCase):
    def test_hash_password_and_verify(self):
        pwd = "TestSecurePassword2026!"
        hashed = auth.hash_password(pwd)
        self.assertTrue(hashed.startswith("pbkdf2:sha256:100000$"))
        self.assertTrue(auth.verify_password(pwd, hashed))
        self.assertFalse(auth.verify_password("WrongPassword!", hashed))
        self.assertFalse(auth.verify_password("", hashed))
        self.assertFalse(auth.verify_password(pwd, ""))

    def test_access_token_creation_and_decoding(self):
        token = auth.create_access_token(
            user_id=42,
            username="kwame_test",
            role="analyst",
            display_name="Kwame Test",
            expires_in_seconds=3600,
        )
        payload = auth.decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], 42)
        self.assertEqual(payload["username"], "kwame_test")
        self.assertEqual(payload["role"], "analyst")
        self.assertEqual(payload["display_name"], "Kwame Test")

    def test_tampered_token_is_rejected(self):
        token = auth.create_access_token(
            user_id=1, username="admin", role="admin"
        )
        parts = token.split(".")
        tampered_sig = parts[0] + "." + "0" * 64
        self.assertIsNone(auth.decode_access_token(tampered_sig))
        tampered_body = "eyJ1c2VyIjoidGVzdCJ9." + parts[1]
        self.assertIsNone(auth.decode_access_token(tampered_body))
        self.assertIsNone(auth.decode_access_token("invalid-token-string"))

    def test_expired_token_is_rejected(self):
        token = auth.create_access_token(
            user_id=1,
            username="admin",
            role="admin",
            expires_in_seconds=-10,  # Expired in past
        )
        self.assertIsNone(auth.decode_access_token(token))


class AuditHashChainTests(unittest.TestCase):
    def setUp(self):
        self.original_db = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "test_audit.sqlite3"
        database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH = self.original_db
        self.temp_dir.cleanup()

    def test_audit_log_entries_form_valid_hash_chain(self):
        # Create multiple sequential audit entries
        database.create_audit_log(
            transaction_id=101,
            action="TEST_ACTION_1",
            performed_by="analyst_1",
            details="First action details",
        )
        database.create_audit_log(
            transaction_id=102,
            action="TEST_ACTION_2",
            performed_by="analyst_2",
            details="Second action details",
        )
        database.create_audit_log(
            transaction_id=None,
            action="TEST_ACTION_3",
            performed_by="admin",
            details="Third action details",
        )

        verification = database.verify_audit_chain()
        self.assertTrue(verification["valid"])
        self.assertEqual(verification["signed_count"], 3)
        self.assertEqual(verification["legacy_unsigned_count"], 0)
        self.assertIsNone(verification["first_broken_id"])
        self.assertIsNotNone(verification["chain_head"])
        self.assertIsNotNone(verification["verified_at"])

        logs = database.get_audit_logs()
        self.assertEqual(len(logs), 3)
        self.assertIsNotNone(logs[0]["entry_hash"])
        self.assertIsNotNone(logs[0]["hmac_signature"])

    def test_tampering_triggers_catch_sqlite_modifications(self):
        database.create_audit_log(
            transaction_id=1,
            action="INITIAL_EVENT",
            performed_by="system",
            details="initial",
        )
        conn = database.get_connection()
        # Triggers reject update and delete
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_logs SET details = 'tampered' WHERE id = 1")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_logs WHERE id = 1")
        conn.close()


class RbacAndAuthenticationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.original_db = database.DATABASE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = Path(self.temp_dir.name) / "test_rbac.sqlite3"
        database.initialize_database()
        auth.seed_default_admin_users()

    def tearDown(self):
        database.DATABASE_PATH = self.original_db
        self.temp_dir.cleanup()

    def test_seed_creates_expected_default_roles(self):
        users = database.list_admin_users()
        usernames = {u["username"] for u in users}
        self.assertIn("admin", usernames)
        self.assertIn("analyst_kwame", usernames)
        self.assertIn("viewer_ama", usernames)

        roles = {u["username"]: u["role"] for u in users}
        self.assertEqual(roles["admin"], "admin")
        self.assertEqual(roles["analyst_kwame"], "analyst")
        self.assertEqual(roles["viewer_ama"], "viewer")

    def test_user_authentication_flow_and_lockout(self):
        # 1. Correct credentials succeed
        user, err = auth.authenticate_user("analyst_kwame", "Analyst@MoMo2026!")
        self.assertIsNone(err)
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "analyst_kwame")
        self.assertEqual(user["role"], "analyst")

        # 2. Failed attempts increment counter and trigger lockout after 5 failures
        for i in range(4):
            bad_user, err = auth.authenticate_user("analyst_kwame", "WrongPassword!")
            self.assertIsNone(bad_user)
            self.assertEqual(err, "Invalid username or password.")

        # 5th attempt locks the account
        bad_user, err = auth.authenticate_user("analyst_kwame", "WrongPassword!")
        self.assertIsNone(bad_user)

        # 6th attempt is blocked by lockout
        bad_user, err = auth.authenticate_user("analyst_kwame", "Analyst@MoMo2026!")
        self.assertIsNone(bad_user)
        self.assertIn("locked", err.lower())

    def test_http_login_and_role_access_control(self):
        async def run_scenario():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # 1. Demo users list endpoint
                demo_resp = await client.get("/api/admin/demo-users")
                self.assertEqual(demo_resp.status_code, 200)
                demo_list = demo_resp.json()["demo_users"]
                self.assertGreaterEqual(len(demo_list), 3)

                # 2. Login as Viewer
                viewer_login = await client.post(
                    "/api/admin/login",
                    json={"username": "viewer_ama", "password": "Viewer@MoMo2026!"},
                )
                self.assertEqual(viewer_login.status_code, 200)
                viewer_token = viewer_login.json()["token"]
                viewer_headers = {"X-Admin-Token": viewer_token}

                # Viewer can view audit logs and verify audit chain
                verify_resp = await client.get(
                    "/api/admin/audit-logs/verify", headers=viewer_headers
                )
                self.assertEqual(verify_resp.status_code, 200)
                self.assertTrue(verify_resp.json()["valid"])

                # Viewer cannot score transactions (403 Forbidden)
                score_denied = await client.post(
                    "/api/transactions/score",
                    headers=viewer_headers,
                    json={
                        "transaction_reference": "TXN-TEST-1",
                        "amount": 100,
                        "transaction_type": "TRANSFER",
                        "destination_account": "WALLET-1002",
                    },
                )
                self.assertEqual(score_denied.status_code, 403)

                # Viewer cannot freeze accounts (403 Forbidden)
                freeze_denied = await client.post(
                    "/api/admin/accounts/WALLET-1001/freeze",
                    headers=viewer_headers,
                )
                self.assertEqual(freeze_denied.status_code, 403)

                # 3. Login as Analyst
                analyst_login = await client.post(
                    "/api/admin/login",
                    json={
                        "username": "analyst_kwame",
                        "password": "Analyst@MoMo2026!",
                    },
                )
                self.assertEqual(analyst_login.status_code, 200)
                analyst_token = analyst_login.json()["token"]
                analyst_headers = {"X-Admin-Token": analyst_token}

                # Analyst CAN score transactions
                score_allowed = await client.post(
                    "/api/transactions/score",
                    headers=analyst_headers,
                    json={
                        "transaction_reference": "TXN-ANALYST-1",
                        "amount": 100,
                        "transaction_type": "TRANSFER",
                        "destination_account": "WALLET-1002",
                    },
                )
                self.assertEqual(score_allowed.status_code, 200)

                # Analyst CANNOT directly freeze account (requires admin)
                freeze_denied_analyst = await client.post(
                    "/api/admin/accounts/WALLET-1001/freeze",
                    headers=analyst_headers,
                )
                self.assertEqual(freeze_denied_analyst.status_code, 403)

                # 4. Login as Admin
                admin_login = await client.post(
                    "/api/admin/login",
                    json={"username": "admin", "password": "Admin@MoMo2026!"},
                )
                self.assertEqual(admin_login.status_code, 200)
                admin_token = admin_login.json()["token"]
                admin_headers = {"X-Admin-Token": admin_token}

                # Admin CAN freeze account
                freeze_allowed = await client.post(
                    "/api/admin/accounts/WALLET-1001/freeze",
                    headers=admin_headers,
                )
                self.assertEqual(freeze_allowed.status_code, 200)
                self.assertTrue(freeze_allowed.json()["account_frozen"])

                # Admin CAN manage users
                user_create = await client.post(
                    "/api/admin/users",
                    headers=admin_headers,
                    json={
                        "username": "new_analyst_kofi",
                        "display_name": "Kofi Analyst",
                        "password": "SecurePassword2026!",
                        "role": "analyst",
                    },
                )
                self.assertEqual(user_create.status_code, 200)

                # Audit logs recorded user logins and freeze action by username
                audit_logs_resp = await client.get(
                    "/api/admin/audit-logs", headers=admin_headers
                )
                self.assertEqual(audit_logs_resp.status_code, 200)
                performers = [
                    log["performed_by"]
                    for log in audit_logs_resp.json()["logs"]
                ]
                self.assertIn("admin", performers)
                self.assertIn("analyst_kwame", performers)

        asyncio.run(run_scenario())


if __name__ == "__main__":
    unittest.main()
