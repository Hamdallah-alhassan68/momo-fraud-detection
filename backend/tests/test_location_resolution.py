"""Tests for consent-based customer coordinate preservation and resolution."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
import database.database as database
from services import geolocation, momo_simulator


class LocationResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "location_test.db"
        self.original_db_path = database.DATABASE_PATH
        database.DATABASE_PATH = self.db_path
        database.initialize_database()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        database.DATABASE_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_validate_customer_location_preserves_six_decimals(self):
        coords = app_module._validate_customer_location(
            {
                "location_consent": True,
                "location_latitude": 5.63581234,
                "location_longitude": -0.16123456,
            }
        )
        self.assertEqual(coords, (5.635812, -0.161235))

    def test_validate_customer_location_accuracy_requires_valid_consented_gps(self):
        body = {
            "location_consent": True,
            "location_latitude": 5.635812,
            "location_longitude": -0.161235,
            "location_accuracy_meters": 18.6,
        }
        coords = app_module._validate_customer_location(body)
        self.assertEqual(
            app_module._validate_customer_location_accuracy(body, coords),
            18.6,
        )

        for invalid_accuracy in (-1, float("nan"), float("inf"), True, "unknown"):
            invalid_body = {
                **body,
                "location_accuracy_meters": invalid_accuracy,
            }
            with self.subTest(accuracy=invalid_accuracy):
                with self.assertRaises(app_module.HTTPException):
                    app_module._validate_customer_location_accuracy(
                        invalid_body,
                        app_module._validate_customer_location(invalid_body),
                    )

    def test_validate_customer_location_rejects_non_finite_coordinates(self):
        for latitude, longitude in (
            (float("nan"), 1),
            (1, float("inf")),
            (True, 1),
            (1, False),
        ):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(app_module.HTTPException):
                    app_module._validate_customer_location(
                        {
                            "location_consent": True,
                            "location_latitude": latitude,
                            "location_longitude": longitude,
                        }
                    )

    def test_simulator_normalize_location_preserves_six_decimals(self):
        coords = momo_simulator._normalize_location(-1.2194839, 36.8851247)
        self.assertEqual(coords, (-1.219484, 36.885125))

    def test_initiated_customer_session_persists_consent_and_reported_accuracy(self):
        with patch.object(
            app_module,
            "reverse_geocode_coordinates",
            return_value="Airport Residential, Accra, Ghana",
        ):
            response = self.client.post(
                "/api/momo/transaction/initiate",
                json={
                    "customer_ref": "WALLET-1001",
                    "transaction_type": "TRANSFER",
                    "amount": 20,
                    "recipient": "WALLET-2001",
                    "device_id": "test-location-device",
                    "location_consent": True,
                    "location_latitude": 5.60372,
                    "location_longitude": -0.18696,
                    "location_accuracy_meters": 31.4,
                    "location_name": "Untrusted submitted label",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        connection = database.get_connection()
        try:
            row = connection.execute(
                """
                SELECT location_latitude, location_longitude,
                       location_accuracy_meters, location_name
                FROM momo_sessions
                WHERE session_id = ?
                """,
                (response.json()["session_id"],),
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(row["location_latitude"], 5.60372)
        self.assertEqual(row["location_longitude"], -0.18696)
        self.assertEqual(row["location_accuracy_meters"], 31.4)
        self.assertEqual(
            row["location_name"], "Airport Residential, Accra, Ghana"
        )

    def test_resolve_case_location_never_fabricates_fake_places(self):
        self.assertIsNone(
            database._resolve_case_location(
                {
                    "customer_ref": "WALLET-1001",
                    "origin_account": "WALLET-1001",
                    "ip_address": "127.0.0.1",
                }
            )
        )
        self.assertIsNone(
            database._resolve_case_location(
                {
                    "customer_ref": "0240723836",
                    "ip_address": "203.0.113.42",
                }
            )
        )

    def test_resolve_case_location_uses_gps_not_client_name_or_ip_guess(self):
        case = {
            "location_name": "Invented customer location",
            "location_latitude": 5.60372,
            "location_longitude": -0.18696,
            "ip_address": "41.220.77.21",
        }
        with patch(
            "services.geolocation.reverse_geocode_coordinates",
            return_value="Independence Avenue, Accra, Ghana",
        ) as reverse_geocode:
            self.assertEqual(
                database._resolve_case_location(case),
                "Independence Avenue, Accra, Ghana",
            )
        reverse_geocode.assert_called_once_with(5.60372, -0.18696)

        self.assertIsNone(
            database._resolve_case_location(
                {"location_name": "Invented location", "ip_address": "41.220.77.21"}
            )
        )

    def test_is_public_ip_rejects_local_private_and_doc_ranges(self):
        self.assertFalse(geolocation.is_public_ip("127.0.0.1"))
        self.assertFalse(geolocation.is_public_ip("::1"))
        self.assertFalse(geolocation.is_public_ip("192.168.1.22"))
        self.assertFalse(geolocation.is_public_ip("203.0.113.42"))
        self.assertTrue(geolocation.is_public_ip("41.220.77.21"))

    def test_fraud_alerts_and_cases_return_coordinates_and_accuracy(self):
        tx_id = database.create_transaction(
            {
                "transaction_reference": "MOMO-LOC-001",
                "amount": 500.0,
                "transaction_type": "TRANSFER",
                "origin_account": "WALLET-1001",
                "destination_account": "WALLET-2001",
                "ip_address": "127.0.0.1",
                "device_id": "browser-device-1",
                "location_latitude": 5.635812,
                "location_longitude": -0.161235,
                "location_accuracy_meters": 18.6,
                "location_name": "Boundary Road, East Legon, Accra, Ghana",
            }
        )
        alert_id = database.create_fraud_alert(tx_id, "HIGH", ["Location change"])
        database.create_fraud_case(tx_id, alert_id)

        admin_token = "a" * 32
        with (
            patch(
                "services.geolocation.reverse_geocode_coordinates",
                return_value="Boundary Road, East Legon, Accra, Ghana",
            ),
            patch.dict(os.environ, {"MOMO_ADMIN_TOKEN": admin_token}, clear=False),
        ):
            headers = {"X-Admin-Token": admin_token}
            cases_resp = self.client.get("/api/admin/fraud-cases", headers=headers)
            alerts_resp = self.client.get("/api/admin/fraud-alerts", headers=headers)

        self.assertEqual(cases_resp.status_code, 200)
        self.assertEqual(alerts_resp.status_code, 200)
        case = cases_resp.json()["cases"][0]
        alert = alerts_resp.json()["alerts"][0]
        self.assertEqual(case["location"], "Boundary Road, East Legon, Accra, Ghana")
        self.assertEqual(case["location_latitude"], 5.635812)
        self.assertEqual(case["location_longitude"], -0.161235)
        self.assertEqual(case["location_accuracy_meters"], 18.6)
        self.assertEqual(alert["location"], "Boundary Road, East Legon, Accra, Ghana")
        self.assertEqual(alert["location_latitude"], 5.635812)
        self.assertEqual(alert["location_longitude"], -0.161235)
        self.assertEqual(alert["location_accuracy_meters"], 18.6)
