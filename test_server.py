import json
import os
import tempfile
import unittest
from unittest import mock

import server


class AccountAndCountryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "NEURON_DB_PATH": os.path.join(self.temp_dir.name, "test.sqlite3"),
                "NEURON_ADMIN_EMAIL": "admin@example.com",
                "NEURON_ADMIN_PASSWORD": "A-strong-admin-password-123",
                "GOOGLE_CLIENT_ID": "",
                "GOOGLE_CLIENT_SECRET": "",
                "PUBLIC_APP_URL": "",
            },
            clear=False,
        )
        self.environment.start()
        server.initialize_database()

    def tearDown(self):
        self.environment.stop()
        self.temp_dir.cleanup()

    def register_guest(self):
        status, result, response_headers = server.handle_auth_post(
            "/api/auth/guest", {"X-Forwarded-Proto": "https"}, b"{}"
        )
        self.assertEqual(status, 200)
        return result, self.session_headers(result, response_headers)

    def session_headers(self, result, response_headers):
        cookie = next(value for name, value in response_headers if name == "Set-Cookie")
        session_cookie = cookie.split(";", 1)[0]
        headers = {
            "Cookie": session_cookie,
            "Host": "neuron.example",
            "Origin": "https://neuron.example",
            "X-CSRF-Token": result["csrf_token"],
        }
        return headers

    def test_guest_account_can_save_country_with_csrf(self):
        guest, headers = self.register_guest()
        self.assertTrue(guest["user"]["guest"])
        status, result, _ = server.handle_auth_post(
            "/api/account",
            headers,
            json.dumps({"name": "Tamu", "country": "RU"}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["user"]["country"], "RU")
        self.assertEqual(
            server.current_session(headers)["country"],
            "RU",
        )

        headers["X-CSRF-Token"] = "invalid"
        status, result, _ = server.handle_auth_post(
            "/api/account",
            headers,
            json.dumps({"name": "Penyusup", "country": "ID"}).encode("utf-8"),
        )
        self.assertEqual(status, 403)
        self.assertIn("Token keamanan", result["error"])

    def test_email_password_login_and_admin_bootstrap(self):
        status, result, _ = server.handle_auth_post(
            "/api/auth/register",
            {},
            json.dumps(
                {
                    "name": "Pelajar",
                    "email": "student@example.com",
                    "password": "a-long-student-password",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["user"]["role"], "user")
        student_id = result["user"]["id"]

        status, _, _ = server.handle_auth_post(
            "/api/auth/login",
            {},
            json.dumps(
                {"email": "student@example.com", "password": "wrong-password"}
            ).encode("utf-8"),
        )
        self.assertEqual(status, 401)

        status, result, _ = server.handle_auth_post(
            "/api/auth/login",
            {},
            json.dumps(
                {
                    "email": "STUDENT@example.com",
                    "password": "a-long-student-password",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["user"]["id"], student_id)

        with server.connect_database() as connection:
            admin = connection.execute(
                "SELECT account_id, role FROM accounts WHERE email = ?",
                ("admin@example.com",),
            ).fetchone()
        self.assertEqual(admin["role"], "admin")
        status, admin_login, _ = server.handle_auth_post(
            "/api/auth/login",
            {},
            json.dumps(
                {
                    "email": "admin@example.com",
                    "password": "A-strong-admin-password-123",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(admin_login["user"]["role"], "admin")
        self.assertEqual(admin_login["user"]["id"], admin["account_id"])

        with mock.patch.dict(
            os.environ,
            {"NEURON_ADMIN_PASSWORD": "A-new-admin-password-456"},
        ):
            server.initialize_database()
        status, _, _ = server.handle_auth_post(
            "/api/auth/login",
            {},
            json.dumps(
                {
                    "email": "admin@example.com",
                    "password": "A-strong-admin-password-123",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 401)
        status, rotated_login, _ = server.handle_auth_post(
            "/api/auth/login",
            {},
            json.dumps(
                {
                    "email": "admin@example.com",
                    "password": "A-new-admin-password-456",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(rotated_login["user"]["role"], "admin")

        status, _, _ = server.handle_auth_post(
            "/api/auth/register",
            {},
            json.dumps(
                {
                    "name": "Impersonator",
                    "email": "admin@example.com",
                    "password": "another-long-password",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 409)

    def test_admin_can_adjust_progress_and_is_excluded_from_leaderboard(self):
        status, student, student_headers_raw = server.handle_auth_post(
            "/api/auth/register",
            {},
            json.dumps(
                {
                    "name": "Pelajar Uji",
                    "email": "player@example.com",
                    "password": "a-long-player-password",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        student_headers = self.session_headers(student, student_headers_raw)

        status, admin, admin_headers_raw = server.handle_auth_post(
            "/api/auth/login",
            {},
            json.dumps(
                {
                    "email": "admin@example.com",
                    "password": "A-strong-admin-password-123",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        admin_headers = self.session_headers(admin, admin_headers_raw)

        status, initial_progress = server.handle_progress_get(student_headers)
        self.assertEqual(status, 200)
        self.assertEqual(initial_progress["progress"]["revision"], 0)
        status, saved_progress = server.handle_progress_post(
            student_headers,
            json.dumps(
                {
                    "xp": 250,
                    "rank_points": 20,
                    "study_minutes": 40,
                    "streak": 2,
                    "last_study": "2026-11-21",
                    "revision": 0,
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved_progress["progress"]["revision"], 1)

        change = json.dumps(
            {
                "account_id": student["user"]["id"],
                "level": 14,
                "rank": "Pakar",
            }
        ).encode("utf-8")
        status, result = server.handle_admin_progress_post(admin_headers, change)
        self.assertEqual(status, 200)
        self.assertEqual(result["progress"]["xp"], 1300)
        self.assertEqual(result["progress"]["rank_points"], 400)
        self.assertEqual(result["progress"]["study_minutes"], 40)

        status, updated = server.handle_progress_get(student_headers)
        self.assertEqual(status, 200)
        self.assertEqual(updated["progress"]["revision"], 2)
        self.assertEqual(updated["progress"]["xp"], 1300)
        status, stale = server.handle_progress_post(
            student_headers,
            json.dumps(
                {
                    "xp": 300,
                    "rank_points": 30,
                    "study_minutes": 45,
                    "streak": 3,
                    "last_study": "2026-11-21",
                    "revision": 1,
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 409)
        self.assertIn("berubah di server", stale["error"])

        status, _ = server.handle_admin_progress_post(
            student_headers,
            json.dumps(
                {
                    "account_id": student["user"]["id"],
                    "level": 2,
                    "rank": "SD",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 403)

        status, result = server.handle_admin_progress_post(
            admin_headers,
            json.dumps(
                {
                    "account_id": admin["user"]["id"],
                    "level": 50,
                    "rank": "Ilmuwan",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        status, leaderboard = server.handle_leaderboard(admin_headers, "rank")
        self.assertEqual(status, 200)
        leaderboard_ids = [player["id"] for player in leaderboard["players"]]
        self.assertIn(student["user"]["id"], leaderboard_ids)
        self.assertNotIn(admin["user"]["id"], leaderboard_ids)
        status, study_leaderboard = server.handle_leaderboard(admin_headers, "study")
        self.assertEqual(status, 200)
        study_ids = [player["id"] for player in study_leaderboard["players"]]
        self.assertIn(student["user"]["id"], study_ids)
        self.assertNotIn(admin["user"]["id"], study_ids)
        status, _ = server.handle_admin_accounts(student_headers)
        self.assertEqual(status, 403)

        status, accounts = server.handle_admin_accounts(admin_headers)
        self.assertEqual(status, 200)
        student_record = next(
            account
            for account in accounts["accounts"]
            if account["id"] == student["user"]["id"]
        )
        self.assertEqual(student_record["level"], 14)
        self.assertEqual(student_record["rank"], "Pakar")

    def test_ai_endpoint_uses_authenticated_country_language(self):
        _, headers = self.register_guest()
        status, profile, _ = server.handle_auth_post(
            "/api/account",
            headers,
            json.dumps({"name": "Tamu", "country": "RU"}).encode("utf-8"),
        )
        self.assertEqual(status, 200)

        captured = {}

        def fake_openrouter(messages, model_setting, json_mode=False):
            captured["prompt"] = messages[-1]["content"]
            return json.dumps(
                {
                    "subjectId": "math",
                    "question": "Сколько будет два плюс два?",
                    "choices": ["Три", "Четыре", "Пять", "Шесть"],
                    "correctIndex": 1,
                },
                ensure_ascii=False,
            )

        with mock.patch.object(server, "openrouter_messages", side_effect=fake_openrouter):
            status, result = server.handle_api_post(
                "/api/questions",
                headers,
                json.dumps(
                    {"subject_id": "math", "rank": "SD", "recent_questions": []}
                ).encode("utf-8"),
            )
        self.assertEqual(status, 200)
        self.assertIn("bahasa Rusia", captured["prompt"])
        self.assertIn("Сколько", result["q"])

    def test_batch_question_endpoint_generates_ten_distinct_questions(self):
        _, headers = self.register_guest()
        generated = {
            "questions": [
                {
                    "subjectId": "math",
                    "question": "Berapa hasil penjumlahan {} dan {}?".format(index, index + 1),
                    "choices": [
                        str(index),
                        str(index + 1),
                        str(index + 2),
                        str(index + 3),
                    ],
                    "correctIndex": 1,
                }
                for index in range(10)
            ]
        }
        captured = {}

        def fake_openrouter(messages, model_setting, json_mode=False, max_tokens=900):
            captured["prompt"] = messages[-1]["content"]
            captured["max_tokens"] = max_tokens
            return json.dumps(generated, ensure_ascii=False)

        with mock.patch.object(
            server, "openrouter_messages", side_effect=fake_openrouter
        ):
            status, result = server.handle_api_post(
                "/api/questions/batch",
                headers,
                json.dumps(
                    {
                        "subject_id": "math",
                        "rank": "SMA",
                        "count": 10,
                        "recent_questions": [],
                    }
                ).encode("utf-8"),
            )
        self.assertEqual(status, 200)
        self.assertEqual(len(result["questions"]), 10)
        self.assertEqual(captured["max_tokens"], 4500)
        self.assertIn("tepat 10 soal", captured["prompt"])

    def test_ai_requires_a_guest_or_signed_in_session(self):
        status, result = server.handle_api_post(
            "/api/questions",
            {"Host": "neuron.example", "Origin": "https://neuron.example"},
            b'{"subject_id":"math","rank":"SD"}',
        )
        self.assertEqual(status, 401)
        self.assertIn("lanjutkan sebagai tamu", result["error"])

    def test_google_login_is_disabled_without_oauth_secrets(self):
        status, result, _ = server.google_start({})
        self.assertEqual(status, 503)
        self.assertIn("belum dikonfigurasi", result["error"])

    def test_google_callback_requires_the_browser_state_cookie(self):
        with mock.patch.dict(
            os.environ,
            {
                "GOOGLE_CLIENT_ID": "test-client",
                "GOOGLE_CLIENT_SECRET": "test-secret",
                "PUBLIC_APP_URL": "https://neuron.example",
            },
        ):
            with self.assertRaisesRegex(ValueError, "browser ini"):
                server.google_callback(
                    {"state": ["state-from-another-browser"], "code": ["unused"]},
                    {},
                )

    def test_google_start_sets_a_secure_browser_bound_state_cookie(self):
        with mock.patch.dict(
            os.environ,
            {
                "GOOGLE_CLIENT_ID": "test-client",
                "GOOGLE_CLIENT_SECRET": "test-secret",
                "PUBLIC_APP_URL": "https://neuron.example",
            },
        ):
            status, result, headers = server.google_start(
                {"X-Forwarded-Proto": "https"}
            )
        self.assertEqual(status, 200)
        state = server.parse_qs(server.urlsplit(result["url"]).query)["state"][0]
        cookie = next(value for name, value in headers if name == "Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn(state, cookie)


if __name__ == "__main__":
    unittest.main()
