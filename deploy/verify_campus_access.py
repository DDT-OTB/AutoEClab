"""Verify real TLS, Nginx, accounts and SSE using an isolated temporary backend.

Run with sudo on the server after installing Nginx and generating the campus TLS
material. Only a temporary loopback listener is opened. No production account,
experiment, serial interface or runtime journal is modified.
"""
import argparse
import http.client
import ipaddress
import json
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from pump_console.auth import AuthStore, SECURE_COOKIE_NAME
from pump_console.repository import LabRepository
from pump_console.server import make_server


class LoopbackTLSConnection(http.client.HTTPSConnection):
    def connect(self):
        plain = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._context.wrap_socket(plain, server_hostname=self.host)


def main():
    parser = argparse.ArgumentParser(description="Verify an isolated HTTPS stack with synthetic accounts and configuration")
    parser.add_argument("--host", required=True, type=ipaddress.ip_address)
    parser.add_argument("--tls-dir", type=Path, default=Path("/etc/pump-console/tls"))
    args = parser.parse_args()
    host = str(args.host)
    tls_directory = args.tls_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="pump-campus-qa-") as temporary:
        directory = Path(temporary)
        lab = directory / "lab"
        shutil.copytree(ROOT / "tests/fixtures/demo-lab", lab)
        auth = AuthStore(directory / "security/auth.sqlite3")
        admin_password, viewer_password, changed_password, reset_password = ["a1" + secrets.token_urlsafe(24) for _ in range(4)]
        auth.create_user("qa_admin", admin_password, temporary=False)
        repository = LabRepository(lab, directory / "data")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            tls_port = reservation.getsockname()[1]
        origin = "https://" + host + ":" + str(tls_port)
        backend = make_server("127.0.0.1", 0, repository, ROOT / "frontend/dist", auth,
                              {"https_origins": [origin], "trusted_proxy_addresses": ["127.0.0.1"]})
        worker = threading.Thread(target=backend.serve_forever, daemon=True)
        worker.start()
        configuration = (ROOT / "deploy/pump-console-nginx.conf").read_text()
        configuration = configuration.replace("192.0.2.10", host)
        configuration = configuration.replace("/etc/pump-console/tls/", str(tls_directory) + "/")
        configuration = configuration.replace("listen " + host + ":443 ssl", "listen 127.0.0.1:" + str(tls_port) + " ssl")
        configuration = configuration.replace("proxy_set_header Host " + host + ";", "proxy_set_header Host " + host + ":" + str(tls_port) + ";")
        configuration = configuration.replace("127.0.0.1:8765", "127.0.0.1:" + str(backend.server_port))
        configuration = configuration.replace("/var/log/nginx/pump-console-access.log", str(directory / "access.log"))
        configuration = configuration.replace("/var/log/nginx/pump-console-error.log", str(directory / "error.log"))
        config_file = directory / "nginx.conf"
        config_file.write_text("pid " + str(directory / "nginx.pid") + ";\nerror_log " + str(directory / "master.log") + ";\nevents { worker_connections 128; }\nhttp {\n" + configuration + "\n}\n")
        nginx = subprocess.Popen(["nginx", "-p", str(directory) + "/", "-c", str(config_file), "-g", "daemon off; master_process off;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        context = ssl.create_default_context(cafile=str(tls_directory / "ca.crt"))
        context.minimum_version = ssl.TLSVersion.TLSv1_2

        def request(method, path, body=None, cookie=None, csrf=None, extra=None):
            headers = {"Origin": origin, "X-Pump-Console": "1"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if cookie:
                headers["Cookie"] = cookie
            if csrf:
                headers["X-CSRF-Token"] = csrf
            headers.update(extra or {})
            connection = LoopbackTLSConnection(host, tls_port, context=context, timeout=6)
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            raw = response.read()
            payload = json.loads(raw) if "json" in response.getheader("Content-Type", "") else raw.decode()
            result = response.status, payload, dict(response.getheaders())
            connection.close()
            return result

        def login(username, password):
            code, session, headers = request("POST", "/api/auth/login", {"username": username, "password": password}, extra={"X-Forwarded-For": "192.0.2.123"})
            assert code == 200
            cookie = headers["Set-Cookie"]
            assert cookie.startswith(SECURE_COOKIE_NAME + "=") and "; Secure" in cookie and "; HttpOnly" in cookie
            return cookie.split(";", 1)[0], session["csrf_token"], session

        try:
            for _ in range(40):
                if nginx.poll() is not None:
                    raise RuntimeError("Isolated Nginx did not start: " + (directory / "master.log").read_text())
                try:
                    if request("GET", "/")[0] == 200:
                        break
                except (OSError, http.client.HTTPException):
                    time.sleep(.1)
            else:
                raise RuntimeError("Isolated HTTPS endpoint not ready")
            assert request("GET", "/api/status")[0] == 401
            assert request("GET", "/api/admin/users")[0] == 401
            admin, admin_csrf, _ = login("qa_admin", admin_password)
            assert request("POST", "/api/admin/users", {"username": "qa_viewer", "password": viewer_password, "role": "viewer"}, admin, admin_csrf)[0] == 201
            viewer, viewer_csrf, session = login("qa_viewer", viewer_password)
            assert session["user"]["must_change_password"]
            assert request("GET", "/api/status", cookie=viewer)[0] == 403
            assert request("POST", "/api/auth/password", {"current_password": viewer_password, "new_password": changed_password}, viewer, viewer_csrf)[0] == 200
            viewer, viewer_csrf, session = login("qa_viewer", changed_password)
            assert not session["user"]["must_change_password"]
            code, status, _ = request("GET", "/api/status", cookie=viewer)
            assert code == 200 and status["motion_commands_sent"] == 0 and status["serial_commands_sent"] == 0
            assert request("GET", "/api/admin/users", cookie=viewer)[0] == 403
            assert request("POST", "/api/admin/users", {"username": "forged_admin", "password": viewer_password, "role": "admin"}, viewer, viewer_csrf)[0] == 403
            assert request("POST", "/api/admin/users/reset-password", {"username": "qa_viewer", "password": reset_password}, admin, admin_csrf)[0] == 200
            assert request("GET", "/api/status", cookie=viewer)[0] == 401
            assert request("POST", "/api/auth/register", {}, admin, admin_csrf)[0] == 404
            assert request("POST", "/api/execute", {}, admin, admin_csrf)[0] == 403
            assert request("POST", "/api/preview", {}, admin, admin_csrf, {"Origin": "http://" + host})[0] == 403
            stream = LoopbackTLSConnection(host, tls_port, context=context, timeout=6)
            stream.request("GET", "/api/events", headers={"Cookie": admin, "Origin": origin})
            response = stream.getresponse()
            assert response.status == 200
            frames, observed = [], []
            while len(frames) < 2:
                line = response.readline().decode().strip()
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:])); observed.append(time.monotonic())
            response.close(); stream.close()
            assert frames[0]["sampled_at"] != frames[1]["sampled_at"] and observed[1] - observed[0] < 4
            with auth.db() as database:
                peers = [row[0] for row in database.execute("SELECT peer FROM audit WHERE event='login_succeeded'")]
            assert peers and set(peers) == {"127.0.0.1"}
            print(json.dumps({"isolated_tls_nginx": "passed", "secure_cookie": True, "administrator_provisioning": True,
                "viewer_denied": True, "registration_disabled": True, "reset_revokes_sessions": True,
                "sse_frames": len(frames), "sse_interval_seconds": round(observed[1] - observed[0], 3),
                "proxy_replaces_forged_client_ip": True, "production_accounts_modified": False,
                "device_queries_sent": 0, "motion_commands_sent": 0}))
        finally:
            nginx.terminate()
            try:
                nginx.wait(timeout=5)
            except subprocess.TimeoutExpired:
                nginx.kill(); nginx.wait(timeout=5)
            backend.shutdown(); backend.server_close(); worker.join(timeout=5)


if __name__ == "__main__":
    main()
