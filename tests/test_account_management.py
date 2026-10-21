import json
import threading
import unittest

import test_api
import test_auth
from pump_console.auth import SECURE_COOKIE_NAME
from pump_console.server import make_server


class AccountManagementTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    request = test_auth.AuthenticationTests.request

    def admin(self, path, payload):
        return self.request("POST", path, payload, token=self.token, csrf=self.csrf)

    def test_no_self_registration_and_anonymous_account_management(self):
        self.assertEqual(self.request("GET", "/api/admin/users")[0], 401)
        payload = {"username": "new_viewer", "password": "temporary123", "role": "viewer"}
        self.assertEqual(self.request("POST", "/api/admin/users", payload)[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/register", payload)[0], 401)
        self.assertEqual(self.admin("/api/auth/register", payload)[0], 404)
        self.assertEqual(len(self.auth.list_users()), 1)

    def test_viewers_cannot_list_create_or_reset_accounts_even_with_forged_role(self):
        self.auth.create_user("observer", "observer123", role="viewer", temporary=False)
        token, session = self.auth.login("observer", "observer123", "127.0.0.1")
        for method, route, payload in (("GET", "/api/admin/users", None),
                ("POST", "/api/admin/users", {"username": "backdoor", "password": "temporary123", "role": "admin"}),
                ("POST", "/api/admin/users/reset-password", {"username": "test_admin", "password": "temporary123"})):
            status, result, _ = self.request(method, route, payload, token=token, csrf=session["csrf_token"], extra={"X-Role": "admin"})
            self.assertEqual(status, 403)
            self.assertEqual(result["error"], "permission_denied")
        self.assertEqual(self.request("GET", "/api/status", token=self.token)[0], 200)

    def test_admin_creates_only_temporary_accounts_and_lists_no_credentials(self):
        payload = {"username": "new_viewer", "password": "temporary123", "role": "viewer"}
        status, result, _ = self.admin("/api/admin/users", payload)
        self.assertEqual(status, 201)
        self.assertEqual(result, {"created": True, "must_change_password": True})
        status, result, _ = self.request("GET", "/api/admin/users", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual({entry["username"] for entry in result["users"]}, {"test_admin", "new_viewer"})
        for entry in result["users"]:
            self.assertEqual(set(entry), {"username", "role", "must_change_password", "temporary_password_expires_at"})
        self.assertNotIn("temporary123", json.dumps(result))
        token, session = self.auth.login("new_viewer", "temporary123", "198.51.100.19")
        self.assertTrue(session["user"]["must_change_password"])
        self.assertEqual(self.request("GET", "/api/status", token=token)[0], 403)

    def test_bad_input_and_duplicate_names_cannot_change_existing_accounts(self):
        base = {"username": "new_viewer", "password": "temporary123", "role": "viewer"}
        for change in ({"role": "owner"}, {"username": "../bad"}, {"password": "weak"}, {"temporary": False}):
            self.assertIn(self.admin("/api/admin/users", {**base, **change})[0], (400, 422))
        self.assertEqual(self.admin("/api/admin/users", base)[0], 201)
        self.assertEqual(self.admin("/api/admin/users", base)[0], 409)
        self.assertEqual(len(self.auth.list_users()), 2)

    def test_admin_reset_revokes_target_sessions_and_records_actor_without_passwords(self):
        self.auth.create_user("observer", "observer123", role="viewer", temporary=False)
        old, _ = self.auth.login("observer", "observer123", "198.51.100.19")
        status, result, _ = self.admin("/api/admin/users/reset-password", {"username": "observer", "password": "newtemporary123"})
        self.assertEqual(status, 200)
        self.assertTrue(result["must_change_password"])
        self.assertEqual(self.request("GET", "/api/status", token=old)[0], 401)
        _, session = self.auth.login("observer", "newtemporary123", "198.51.100.19")
        self.assertTrue(session["user"]["must_change_password"])
        with self.auth.db() as database:
            records = [dict(row) for row in database.execute("SELECT event,username,peer FROM audit")]
        self.assertIn({"event": "password_reset/observer", "username": "test_admin", "peer": "127.0.0.1"}, records)
        self.assertNotIn("newtemporary123", json.dumps(records))

    def test_admin_changes_require_csrf_and_own_password_uses_existing_change_flow(self):
        payload = {"username": "new_viewer", "password": "temporary123", "role": "viewer"}
        self.assertEqual(self.request("POST", "/api/admin/users", payload, token=self.token)[0], 403)
        status, result, _ = self.admin("/api/admin/users/reset-password", {"username": "test_admin", "password": "temporary123"})
        self.assertEqual((status, result["error"]), (422, "own_password_change_required"))
        self.assertEqual(len(self.auth.list_users()), 1)

    def test_https_proxy_sets_secure_cookie_and_uses_verified_client_ip(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.server = make_server("127.0.0.1", 0, self.repo, self.static, self.auth,
            {"https_origins": ["https://192.0.2.10"], "trusted_proxy_addresses": ["127.0.0.1"]})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        headers = {"Host": "192.0.2.10", "X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.19", "Origin": "https://192.0.2.10"}
        status, session, response_headers = self.request("POST", "/api/auth/login",
            {"username": "test_admin", "password": test_auth.PASSWORD}, extra=headers)
        self.assertEqual(status, 200)
        cookie = response_headers["Set-Cookie"]
        self.assertTrue(cookie.startswith(SECURE_COOKIE_NAME + "="))
        self.assertIn("; Secure", cookie)
        self.assertIn("; HttpOnly", cookie)
        self.assertNotIn("Domain=", cookie)
        token = cookie.split(";", 1)[0].split("=", 1)[1]
        headers["Cookie"] = SECURE_COOKIE_NAME + "=" + token
        self.assertEqual(self.request("GET", "/api/status", extra=headers)[0], 200)
        status, _, cleared = self.request("POST", "/api/auth/logout", csrf=session["csrf_token"], extra=headers)
        self.assertEqual(status, 200)
        self.assertIn("; Secure", cleared["Set-Cookie"])
        self.assertIn("Max-Age=0", cleared["Set-Cookie"])
        with self.auth.db() as database:
            peer = database.execute("SELECT peer FROM audit WHERE event='login_succeeded' ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(peer, "198.51.100.19")
        self.assertEqual(self.request("GET", "/api/status", token=self.token)[0], 200)
        self.assertEqual(self.request("GET", "/", extra={"Host": "192.0.2.10"})[0], 403)


if __name__ == "__main__":
    unittest.main()
