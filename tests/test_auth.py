"""Security behavior tests: no hardware or real credentials are used."""
import http.client
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
import test_api

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from pump_console.auth import AuthStore, COOKIE_NAME, password_hash, password_matches, validate_password
from pump_console.repository import ApiError

PASSWORD = "Test passphrase for API 2026"


class AuthenticationTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown

    def request(self, method, path, payload=None, token=None, csrf=None, extra=None, protection=True):
        headers = {}
        if protection and method == "POST":
            headers["X-Pump-Console"] = "1"
        if token:
            headers["Cookie"] = COOKIE_NAME + "=" + token
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if payload is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        body = json.loads(raw) if "json" in response.getheader("Content-Type", "") else raw.decode()
        result = response.status, body, dict(response.getheaders())
        connection.close()
        return result

    def test_anonymous_users_cannot_read_or_write_or_subscribe(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/", extra={"Sec-Fetch-Site": "cross-site"})[0], 200)
        for route in ("/api/health", "/api/status", "/api/templates", "/api/experiments",
                      "/api/experiments/demo_rinse", "/api/runs", "/api/events"):
            code, body, _ = self.request("GET", route)
            self.assertEqual(code, 401, route)
            self.assertEqual(set(body), {"error", "message"})
        for route in ("/api/experiments", "/api/preview", "/api/execute"):
            self.assertEqual(self.request("POST", route, {"sequence": self.sequence})[0], 401)
        self.assertEqual(self.request("GET", "/config/server.json")[0], 404)
        self.assertEqual(self.request("GET", "/data/security/auth.sqlite3")[0], 404)

    def test_real_login_uses_httponly_cookie_and_never_returns_session_id_in_json(self):
        code, body, headers = self.request("POST", "/api/auth/login", {"username": "test_admin", "password": PASSWORD})
        self.assertEqual(code, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain=", cookie)
        token = cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertNotIn(token, json.dumps(body))
        self.assertTrue(body["authenticated"])
        self.assertEqual(self.request("GET", "/api/auth/session", token=token)[0], 200)
        self.assertEqual(self.request("GET", "/api/status?token=" + token)[0], 401)
        self.assertEqual(self.request("GET", "/api/status", extra={"Authorization": "Bearer " + token})[0], 401)
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_invalid_login_is_generic_and_rate_limited(self):
        known = self.request("POST", "/api/auth/login", {"username": "test_admin", "password": "wrong"})
        unknown = self.request("POST", "/api/auth/login", {"username": "no_such_user", "password": "wrong"})
        self.assertEqual(known[:2], unknown[:2])
        self.auth.policy["login_max_failures"] = 3
        with patch("pump_console.auth.password_matches", return_value=False):
            self.request("POST", "/api/auth/login", {"username": "test_admin", "password": "wrong"})
            self.request("POST", "/api/auth/login", {"username": "test_admin", "password": "wrong"})
        code, body, headers = self.request("POST", "/api/auth/login", {"username": "test_admin", "password": PASSWORD})
        self.assertEqual(code, 429)
        self.assertIn("Retry-After", headers)
        self.assertEqual(body["error"], "login_throttled")

    def test_state_changes_require_csrf_and_same_origin_protection(self):
        for csrf in (None, "invalid", "n" * 43):
            code, body, _ = self.request("POST", "/api/preview", {"sequence": self.sequence}, token=self.token, csrf=csrf)
            self.assertEqual(code, 403)
            self.assertEqual(body["error"], "csrf_failed")
        for extra in ({"Origin": "https://untrusted.example"}, {"Origin": "http://localhost:1"},
                      {"Sec-Fetch-Site": "same-site"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("POST", "/api/preview", {"sequence": self.sequence},
                                          token=self.token, csrf=self.csrf, extra=extra)[0], 403)
        self.assertEqual(self.request("POST", "/api/preview", {"sequence": self.sequence},
                                      token=self.token, csrf=self.csrf, protection=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/preview", {"sequence": self.sequence},
                                      token=self.token, csrf=self.csrf)[0], 200)

    def test_viewer_cannot_save_even_if_the_client_claims_admin(self):
        self.auth.create_user("observer", "Read only test passphrase 2026", role="viewer", temporary=False)
        token, session = self.auth.login("observer", "Read only test passphrase 2026", "127.0.0.1")
        self.assertEqual(self.request("GET", "/api/status", token=token)[0], 200)
        code, body, _ = self.request("POST", "/api/experiments", {"sequence": self.sequence, "role": "admin"},
                                     token=token, csrf=session["csrf_token"], extra={"X-Role": "admin"})
        self.assertEqual(code, 403)
        self.assertEqual(body["error"], "permission_denied")

    def test_first_login_requires_password_change_and_revokes_all_old_sessions(self):
        self.auth.create_user("new_user", "Initial temporary passphrase 2026", temporary=True)
        tokens = [self.auth.login("new_user", "Initial temporary passphrase 2026", "127.0.0.1")[0] for _ in range(2)]
        session = self.auth.session(tokens[0], allow_password_change=True)
        self.assertTrue(session["user"]["must_change_password"])
        self.assertEqual(self.request("GET", "/api/status", token=tokens[0])[0], 403)
        self.assertEqual(self.request("POST", "/api/experiments", {"sequence": self.sequence},
                                      token=tokens[0], csrf=session["csrf_token"])[0], 403)
        code, body, headers = self.request("POST", "/api/auth/password",
             {"current_password": "Initial temporary passphrase 2026", "new_password": "labtest12"},
             token=tokens[0], csrf=session["csrf_token"])
        self.assertEqual(code, 200)
        self.assertTrue(body["reauthenticate"])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        for token in tokens:
            self.assertEqual(self.request("GET", "/api/status", token=token)[0], 401)
        token, new_session = self.auth.login("new_user", "labtest12", "127.0.0.1")
        self.assertFalse(new_session["user"]["must_change_password"])
        self.assertEqual(self.request("GET", "/api/status", token=token)[0], 200)

    def test_logout_revokes_an_already_open_realtime_stream(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("GET", "/api/events", headers={"Cookie": COOKIE_NAME + "=" + self.token})
        response = connection.getresponse()
        def event():
            while True:
                line = response.readline().decode().strip()
                if line.startswith("event: "):
                    return line[7:]
        self.assertEqual(event(), "status")
        code, _, headers = self.request("POST", "/api/auth/logout", token=self.token, csrf=self.csrf)
        self.assertEqual(code, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(event(), "session_expired")
        response.close()
        connection.close()
        self.assertEqual(self.request("GET", "/api/status", token=self.token)[0], 401)

    def test_background_monitoring_does_not_renew_idle_expiry(self):
        started = self.auth.now()
        self.auth.now = lambda: started + 1790
        self.assertEqual(self.request("GET", "/api/status", token=self.token)[0], 200)
        self.auth.now = lambda: started + 1801
        self.assertEqual(self.request("GET", "/api/status", token=self.token)[0], 401)
        with self.auth.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_activity_extends_idle_but_cannot_extend_absolute_expiry(self):
        started = self.auth.now()
        for elapsed in range(1200, 28800, 1200):
            self.auth.now = lambda elapsed=elapsed: started + elapsed
            self.auth.session(self.token, touch=True)
        self.auth.now = lambda: started + 28801
        with self.assertRaises(ApiError) as failure:
            self.auth.session(self.token, touch=True)
        self.assertEqual(failure.exception.status, 401)

    def test_session_survives_restart_and_ssh_reset_revokes_it(self):
        reopened = AuthStore(self.auth.path)
        self.assertTrue(reopened.session(self.token)["authenticated"])
        reopened.reset_password("test_admin", "Reset temporary test passphrase 2026")
        with self.assertRaises(ApiError):
            self.auth.session(self.token)
        _, session = reopened.login("test_admin", "Reset temporary test passphrase 2026", "127.0.0.1")
        self.assertTrue(session["user"]["must_change_password"])

    def test_no_plaintext_passwords_or_raw_sessions_and_private_permissions(self):
        raw = self.auth.path.read_bytes()
        self.assertNotIn(PASSWORD.encode(), raw)
        self.assertNotIn(self.token.encode(), raw)
        self.assertEqual(os.stat(self.auth.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.auth.path.parent).st_mode & 0o777, 0o700)
        with self.auth.db() as db:
            row = db.execute("SELECT password_hash FROM users WHERE username='test_admin'").fetchone()
            self.assertTrue(row[0].startswith("scrypt$131072$8$1$"))
            audit = [dict(row) for row in db.execute("SELECT * FROM audit")]
        self.assertNotIn(PASSWORD, json.dumps(audit))
        validate_password("只用于隔离测试的密码短语a1")
        self.assertTrue(password_matches(PASSWORD, row[0]))
        self.assertFalse(password_matches("wrong", row[0]))

    def test_access_logs_never_record_query_string_credentials(self):
        with patch.object(self.server.RequestHandlerClass, "log_message") as log:
            self.assertEqual(self.request("GET", "/api/status?token=" + self.token)[0], 401)
        self.assertNotIn(self.token, str(log.call_args_list))
        self.assertNotIn("?token=", str(log.call_args_list))


if __name__ == "__main__":
    unittest.main()
