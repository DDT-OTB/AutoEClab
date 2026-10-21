from __future__ import annotations

import argparse
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sqlite3
import time
from urllib.parse import unquote, urlsplit

from .auth import AuthStore, COOKIE_NAME, SECURE_COOKIE_NAME
from .access import AccessPolicy
from .measurement import measurement_templates
from .repository import ApiError, LabRepository, parse_json


def make_server(host, port, repository, static_root, auth, access=None):
    static_root = Path(static_root).resolve()
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("The console must bind to loopback")
    if not isinstance(auth, AuthStore):
        raise ValueError("Server-side authentication is required")
    access = AccessPolicy(access)

    class Handler(BaseHTTPRequestHandler):
        server_version = "PumpConsole"

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def version_string(self):
            return "PumpConsole"

        def log_request(self, code="-", size="-"):
            self.log_message("%s %s %s", self.command, urlsplit(self.path).path[:200], str(code))

        def security_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), usb=(), serial=()")
            self.send_header("Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            if getattr(self, "secure_transport", False):
                self.send_header("Strict-Transport-Security", "max-age=86400")

        def cookie_header(self, token=None):
            secure = getattr(self, "secure_transport", False)
            name = SECURE_COOKIE_NAME if secure else COOKIE_NAME
            return name + "=" + (token or "") + "; Path=/; HttpOnly; SameSite=Strict" + (
                "; Secure" if secure else "") + (
                "" if token else "; Max-Age=0")

        def json_response(self, status, payload, cookie=None):
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.security_headers()
            if cookie is not None:
                self.send_header("Set-Cookie", self.cookie_header(cookie))
            if status == 429:
                self.send_header("Retry-After", str(auth.policy["login_window_seconds"]))
            self.end_headers()
            self.wfile.write(raw)

        def failure(self, error):
            self.json_response(error.status, {"error": error.code, "message": str(error)},
                               cookie="" if error.code == "authentication_required" else None)

        def guard(self, mutating=False):
            if len(self.headers.get_all("Host", [])) != 1:
                raise ApiError(403, "Exactly one service address is required", "invalid_host")
            expected_origin, self.request_peer = access.resolve(self.headers, self.client_address[0])
            self.secure_transport = expected_origin.startswith("https://")
            origin = self.headers.get("Origin")
            if origin and origin != expected_origin:
                raise ApiError(403, "Cross-origin requests are disabled", "invalid_origin")
            protected = mutating or unquote(urlsplit(self.path).path).startswith("/api/")
            if protected and self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
                raise ApiError(403, "Cross-origin requests are disabled", "invalid_origin")
            if mutating and self.headers.get("X-Pump-Console") != "1":
                raise ApiError(403, "Missing request protection header", "invalid_request_origin")

        def session_token(self):
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
                name = SECURE_COOKIE_NAME if getattr(self, "secure_transport", False) else COOKIE_NAME
                return cookies[name].value if name in cookies else ""
            except CookieError:
                return ""

        def body(self, limit=262144):
            if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                raise ApiError(415, "Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ApiError(400, "Invalid content length")
            if not 0 < length <= limit:
                raise ApiError(413, "Invalid JSON body length")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ApiError(400, "Incomplete JSON body")
            return parse_json(raw)

        def do_GET(self):
            try:
                self.guard()
                path = unquote(urlsplit(self.path).path)
                if path == "/api/auth/session":
                    return self.json_response(200, auth.session(self.session_token(), allow_password_change=True))
                if path.startswith("/api/"):
                    token = self.session_token()
                    session = auth.session(token)
                    if path == "/api/events":
                        return self.status_stream(token)
                    if path == "/api/health":
                        return self.json_response(200, repository.health())
                    if path == "/api/templates":
                        return self.json_response(200, {"templates": repository.templates(), "measurement_templates": measurement_templates()})
                    if path == "/api/status":
                        return self.json_response(200, repository.status())
                    if path == "/api/experiments":
                        return self.json_response(200, {"experiments": repository.list_experiments()})
                    if path.startswith("/api/experiments/"):
                        name = path.removeprefix("/api/experiments/")
                        if name.endswith("/content"):
                            return self.json_response(200, repository.experiment_content(name.removesuffix("/content")))
                        return self.json_response(200, repository.get_experiment(name))
                    if path == "/api/runs":
                        return self.json_response(200, {"runs": repository.runs()})
                    if path == "/api/admin/users":
                        self.require_admin(session)
                        return self.json_response(200, {"users": auth.list_users()})
                    raise ApiError(404, "Unknown API route", "not_found")
                relative = path.lstrip("/") or "index.html"
                candidate = (static_root / relative).resolve()
                if not candidate.is_relative_to(static_root):
                    raise ApiError(403, "Invalid asset path")
                if relative != "index.html" and not relative.startswith("assets/"):
                    raise ApiError(404, "Not found", "not_found")
                if not candidate.is_file():
                    raise ApiError(404, "Not found", "not_found")
                raw = candidate.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.security_headers()
                self.end_headers()
                self.wfile.write(raw)
            except ApiError as error:
                self.failure(error)
            except (OSError, ValueError, sqlite3.Error):
                self.json_response(500, {"error": "server_error", "message": "请求处理失败，请稍后重试。"})

        def status_stream(self, token):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("X-Accel-Buffering", "no")
            self.security_headers()
            self.end_headers()
            try:
                self.wfile.write(b"retry: 1000\n\n")
                while True:
                    try:
                        auth.session(token)  # Background monitoring never extends idle expiry.
                    except ApiError:
                        self.wfile.write(b'event: session_expired\ndata: {"reason":"authentication_required"}\n\n')
                        self.wfile.flush()
                        self.close_connection = True
                        return
                    payload = json.dumps(repository.status(), ensure_ascii=False, allow_nan=False)
                    self.wfile.write(("event: status\ndata: " + payload + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(repository.monitoring["push_interval_seconds"])
            except (OSError, ValueError, ApiError, sqlite3.Error):
                self.close_connection = True

        def require_admin(self, session):
            if session["user"]["role"] != "admin":
                raise ApiError(403, "此操作需要管理员权限。", "permission_denied")

        def do_POST(self):
            try:
                self.guard(mutating=True)
                path = unquote(urlsplit(self.path).path)
                if path == "/api/auth/login":
                    payload = self.body(8192)
                    if not isinstance(payload, dict) or set(payload) != {"username", "password"}:
                        raise ApiError(400, "Expected username and password")
                    token, session = auth.login(payload["username"], payload["password"], self.request_peer)
                    return self.json_response(200, session, cookie=token)
                token = self.session_token()
                own_account = path in ("/api/auth/logout", "/api/auth/password", "/api/auth/touch")
                session = auth.session(token, allow_password_change=own_account)
                auth.csrf(session, self.headers.get("X-CSRF-Token"))
                if path == "/api/auth/logout":
                    auth.logout(token)
                    return self.json_response(200, {"logged_out": True}, cookie="")
                if path == "/api/auth/touch":
                    return self.json_response(200, auth.session(token, touch=True, allow_password_change=True))
                if path == "/api/auth/password":
                    payload = self.body(8192)
                    if not isinstance(payload, dict) or set(payload) != {"current_password", "new_password"}:
                        raise ApiError(400, "Expected current_password and new_password")
                    auth.change_password(token, payload["current_password"], payload["new_password"])
                    return self.json_response(200, {"changed": True, "reauthenticate": True}, cookie="")
                if path in ("/api/admin/users", "/api/admin/users/reset-password"):
                    self.require_admin(session)
                    payload = self.body(8192)
                    fields = {"username", "password", "role"} if path == "/api/admin/users" else {"username", "password"}
                    if not isinstance(payload, dict) or set(payload) != fields:
                        raise ApiError(400, "Unexpected account fields")
                    session = auth.session(token, touch=True)
                    self.require_admin(session)
                    actor = session["user"]["username"]
                    if path.endswith("reset-password"):
                        if isinstance(payload["username"], str) and payload["username"].lower() == actor:
                            raise ApiError(422, "请使用修改密码功能更新自己的密码。", "own_password_change_required")
                        auth.reset_password(payload["username"], payload["password"], actor=actor, peer=self.request_peer)
                        return self.json_response(200, {"reset": True, "must_change_password": True})
                    auth.create_user(payload["username"], payload["password"], role=payload["role"], actor=actor, peer=self.request_peer)
                    return self.json_response(201, {"created": True, "must_change_password": True})
                if path == "/api/status/refresh":
                    if session["user"]["role"] != "admin":
                        raise ApiError(403, "硬件状态刷新需要管理员权限。", "permission_denied")
                    if self.body(8192) != {}:
                        raise ApiError(400, "Status refresh does not accept device commands")
                    auth.session(token, touch=True)
                    result = repository.refresh_status()
                    auth.session(token)
                    with auth.db() as db:
                        auth.audit(db, "device_status_" + result["scan"]["state"], session["user"]["username"], self.request_peer)
                    return self.json_response(200, result)
                if path.startswith("/api/experiments/"):
                    parts = path.removeprefix("/api/experiments/").split("/")
                    if len(parts) == 2 and parts[1] in ("rename", "duplicate"):
                        self.require_admin(session)
                        payload = self.body(8192)
                        if not isinstance(payload, dict) or set(payload) != {"new_id", "expected_version"}:
                            raise ApiError(400, "Expected a new ID and the current file version")
                        auth.session(token, touch=True)
                        result = repository.rename_experiment(parts[0], payload["new_id"], payload["expected_version"], duplicate=parts[1] == "duplicate")
                        with auth.db() as db:
                            auth.audit(db, "experiment_" + parts[1] + "/" + parts[0], session["user"]["username"], self.request_peer)
                        return self.json_response(200, result)
                if any(value in path for value in ("/execute", "/command", "/probe", "/initialize", "/stop")):
                    raise ApiError(403, "Hardware execution and serial commands are disabled.", "hardware_disabled")
                if path not in ("/api/preview", "/api/experiments"):
                    raise ApiError(404, "Unknown API route", "not_found")
                if path == "/api/experiments" and session["user"]["role"] != "admin":
                    raise ApiError(403, "当前账号为只读权限，不能保存方案。", "permission_denied")
                auth.session(token, touch=True)
                payload = self.body()
                if not isinstance(payload, dict) or set(payload) - {"sequence", "expected_version"}:
                    raise ApiError(400, "Expected a sequence object and optional expected_version")
                if path == "/api/preview":
                    return self.json_response(200, repository.preview(payload.get("sequence")))
                result = repository.save_experiment(payload.get("sequence"), payload.get("expected_version"))
                with auth.db() as db:
                    auth.audit(db, "experiment_saved/" + result["id"], session["user"]["username"], self.request_peer)
                return self.json_response(200, result)
            except ApiError as error:
                self.failure(error)
            except (OSError, ValueError, sqlite3.Error):
                self.json_response(500, {"error": "server_error", "message": "请求处理失败，请稍后重试。"})

        def do_DELETE(self):
            try:
                self.guard(mutating=True)
                token = self.session_token()
                session = auth.session(token)
                auth.csrf(session, self.headers.get("X-CSRF-Token"))
                path = unquote(urlsplit(self.path).path)
                if not path.startswith("/api/experiments/"):
                    raise ApiError(405, "Deletion is not enabled for this resource", "method_not_allowed")
                self.require_admin(session)
                payload = self.body(8192)
                if not isinstance(payload, dict) or set(payload) != {"expected_version"}:
                    raise ApiError(400, "Expected the current file version")
                auth.session(token, touch=True)
                name = path.removeprefix("/api/experiments/")
                result = repository.delete_experiment(name, payload["expected_version"])
                with auth.db() as db:
                    auth.audit(db, "experiment_deleted/" + name, session["user"]["username"], self.request_peer)
                self.json_response(200, result)
            except ApiError as error:
                self.failure(error)
            except (OSError, ValueError, sqlite3.Error):
                self.json_response(500, {"error": "server_error", "message": "文件操作失败，原文件已保留或备份，请刷新后检查。"})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=root / "config/server.json")
    parser.add_argument("--lab-root", type=Path)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config.get("hardware_execution_enabled") is not False:
        raise ValueError("Hardware execution must remain disabled")
    authentication = config.get("authentication", {})
    if authentication.get("enabled") is not True or authentication.get("transport") not in ("ssh_loopback", "https_reverse_proxy"):
        raise ValueError("Authentication and a protected transport are required")
    access = config.get("access", {})
    policy = AccessPolicy(access)
    if authentication["transport"] == "https_reverse_proxy" and not policy.origins:
        raise ValueError("HTTPS proxy access requires an exact HTTPS origin")
    if authentication["transport"] == "ssh_loopback" and policy.origins:
        raise ValueError("HTTPS origins require HTTPS proxy transport")
    for key, minimum, maximum in (("idle_timeout_seconds", 60, 3600), ("absolute_timeout_seconds", 600, 43200),
                                  ("login_window_seconds", 60, 3600), ("login_max_failures", 3, 20)):
        value = authentication.get(key)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("Invalid authentication setting: " + key)
    auth = AuthStore(root / "data/security/auth.sqlite3", authentication)
    lab_root = args.lab_root or Path(config["lab_root"])
    monitoring = config.get("monitoring", {})
    for key, minimum, maximum in (("push_interval_seconds", 0.5, 5), ("stale_after_seconds", 5, 120)):
        value = monitoring.get(key, 1 if key == "push_interval_seconds" else 15)
        if not isinstance(value, (int, float)) or not minimum <= value <= maximum:
            raise ValueError("Invalid monitoring setting: " + key)
    device_status = config.get("device_status", {})
    if type(device_status.get("enabled", False)) is not bool:
        raise ValueError("Invalid device status enable flag")
    for key, default, minimum, maximum in (("cache_ttl_seconds", 30, 5, 120),
            ("min_interval_seconds", 3, 1, 60), ("timeout_seconds", 10, 2, 12)):
        value = device_status.get(key, default)
        if type(value) not in (int, float) or not minimum <= value <= maximum:
            raise ValueError("Invalid device status setting: " + key)
    repository = LabRepository(lab_root, root / "data", monitoring, device_status)
    static_root = (args.config.parent / config["static_root"]).resolve()
    server = make_server(config["host"], args.port or config["port"], repository, static_root, auth, access)
    print(f"Pump Console ready: http://{config['host']}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
