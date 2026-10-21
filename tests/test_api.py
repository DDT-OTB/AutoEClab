from __future__ import annotations

import copy
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from pump_console.repository import ApiError, LabRepository
from pump_console.server import make_server
from pump_console.auth import AuthStore, COOKIE_NAME


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lab = self.root / "lab"
        shutil.copytree(ROOT / "tests/fixtures/demo-lab", self.lab)
        self.repo = LabRepository(self.lab, self.root / "data")
        self.sequence = json.loads((self.lab / "config/sequences/demo_rinse_with_predrain.json").read_text())
        self.static = self.root / "dist"
        self.static.mkdir()
        (self.static / "index.html").write_text("<html>React frontend</html>")
        self.auth = AuthStore(self.root / "security/auth.sqlite3")
        self.auth.create_user("test_admin", "Test passphrase for API 2026", temporary=False)
        self.token, session = self.auth.login("test_admin", "Test passphrase for API 2026", "127.0.0.1")
        self.csrf = session["csrf_token"]
        self.server = make_server("127.0.0.1", 0, self.repo, self.static, self.auth)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        data = None if payload is None else json.dumps(payload)
        values = {"Content-Type": "application/json"} if payload is not None else {}
        values.update({"Cookie": COOKIE_NAME + "=" + self.token, "X-Pump-Console": "1", "X-CSRF-Token": self.csrf})
        values.update(headers or {})
        connection.request(method, path, body=data, headers=values)
        response = connection.getresponse()
        raw = response.read()
        result = json.loads(raw) if "json" in response.getheader("Content-Type", "") else raw.decode()
        connection.close()
        return response.status, result

    def test_health_frontend_and_passive_status(self):
        code, health = self.request("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertFalse(health["hardware_execution_enabled"])
        self.assertFalse(health["serial_probe_enabled"])
        code, body = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("React frontend", body)
        _, status = self.request("GET", "/api/status")
        self.assertEqual(len(status["devices"]), 5)
        self.assertEqual(status["serial_commands_sent"], 0)
        self.assertTrue(all(d["state"] == "unknown" for d in status["devices"]))
        self.assertNotIn("lab_automation.runner", sys.modules)
        self.assertNotIn("lab_automation.devices", sys.modules)
        self.assertNotIn("lab_automation.transport", sys.modules)

    def test_templates_and_real_compiler_preview(self):
        code, response = self.request("GET", "/api/templates")
        self.assertEqual(code, 200)
        self.assertEqual(response["templates"]["peristaltic_1"]["device"]["rpm"], 100)
        code, result = self.request("POST", "/api/preview", {"sequence": self.sequence})
        self.assertEqual(code, 200)
        self.assertEqual(len(result["plan"]["steps"]), 11)
        self.assertFalse(result["hardware_executed"])
        self.assertAlmostEqual(result["plan"]["steps"][1]["duration_s"], 3.75 * 60 / 12.0)

    def test_realtime_stream_pushes_new_status_without_a_second_request(self):
        logs = self.lab / "logs/automation"
        logs.mkdir(parents=True)
        journal = logs / "run-live-test.jsonl"
        step = {"id": "step_01", "index": 1, "device": "peristaltic_1", "action": "dose",
                "operation": "inject", "fluid": "water", "volume_ml": 10, "duration_s": 20}
        def emit(event, **fields):
            with journal.open("a") as file:
                file.write(json.dumps({"event": event, "time": datetime.now(timezone.utc).isoformat(), **fields}) + "\n")
        emit("configuration_snapshot", plan={"steps": [step]}, sequence={"id": "live_test"})
        emit("step_started", step=step)
        self.repo.runtime.controller_reader = lambda: {
            "state": "active", "reason": "controller_journal", "alive": True, "journal": journal}
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("GET", "/api/events", headers={"Cookie": COOKIE_NAME + "=" + self.token})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("text/event-stream", response.getheader("Content-Type"))
        def frame():
            while True:
                line = response.readline().decode().strip()
                if line.startswith("data: "):
                    return json.loads(line[6:])
        first = frame()
        emit("step_completed", id="step_01")
        emit("sequence_completed", sequence="live_test")
        second = frame()
        self.assertIn("runtime", first)
        self.assertEqual(first["runtime"]["active_step"]["state"], "executing")
        self.assertEqual(second["runtime"]["state"], "completed")
        self.assertNotEqual(first["sampled_at"], second["sampled_at"])
        self.assertEqual(second["serial_commands_sent"], 0)
        response.close()
        connection.close()

    def test_save_load_and_markdown_roundtrip(self):
        self.sequence["id"] = "web_roundtrip"
        code, saved = self.request("POST", "/api/experiments", {"sequence": self.sequence})
        self.assertEqual(code, 200)
        self.assertTrue(saved["saved"])
        path = self.lab / "config/sequences/web_roundtrip.json"
        self.assertEqual(json.loads(path.read_text()), self.sequence)
        self.assertTrue(path.with_suffix(".md").exists())
        code, loaded = self.request("GET", "/api/experiments/web_roundtrip")
        self.assertEqual(code, 200)
        self.assertEqual(loaded["sequence"], self.sequence)
        _, catalog = self.request("GET", "/api/experiments")
        self.assertIn("web_roundtrip", [item["id"] for item in catalog["experiments"]])

    def test_save_conflict_and_backup_preserve_prior_version(self):
        name = self.sequence["id"]
        _, original = self.request("GET", "/api/experiments/" + name)
        old = copy.deepcopy(self.sequence)
        self.sequence["description"] = "Updated through the web editor"
        code, conflict = self.request("POST", "/api/experiments", {"sequence": self.sequence})
        self.assertEqual(code, 409)
        code, saved = self.request("POST", "/api/experiments", {
            "sequence": self.sequence, "expected_version": original["version"]})
        self.assertEqual(code, 200)
        backups = list((self.root / "data/backups").glob("*/" + name + ".json"))
        self.assertEqual(json.loads(backups[0].read_text()), old)

    def test_invalid_fluid_and_wrong_route_are_rejected(self):
        invalid = copy.deepcopy(self.sequence)
        invalid["steps"][2]["fluid"] = "sulfuric_acid"
        code, response = self.request("POST", "/api/preview", {"sequence": invalid})
        self.assertEqual(code, 422)
        invalid = copy.deepcopy(self.sequence)
        invalid["steps"] = [invalid["steps"][1]]
        code, response = self.request("POST", "/api/preview", {"sequence": invalid})
        self.assertEqual(code, 422)

    def test_chinese_config_is_rejected_but_ui_messages_are_allowed(self):
        self.sequence["description"] = chr(0x6D4B) + chr(0x8BD5)
        code, response = self.request("POST", "/api/experiments", {"sequence": self.sequence})
        self.assertEqual(code, 422)
        self.assertEqual(response["error"], "english_json_required")

    def test_hardware_routes_unconditionally_denied(self):
        for path in ("/api/execute", "/api/devices/syringe_c/initialize",
                     "/api/devices/peristaltic_1/command", "/api/probe", "/api/stop"):
            code, response = self.request("POST", path, {"sequence": self.sequence})
            self.assertEqual(code, 403)
            self.assertEqual(response["error"], "hardware_disabled")

    def test_cross_origin_and_path_traversal_denied(self):
        code, _ = self.request("POST", "/api/preview", {"sequence": self.sequence},
                               {"Origin": "https://untrusted.example"})
        self.assertEqual(code, 403)
        code, _ = self.request("GET", "/api/experiments/../../secrets")
        self.assertEqual(code, 400)
        code, _ = self.request("GET", "/%2e%2e/config/server.json")
        self.assertEqual(code, 403)

    def test_invalid_request_json_and_unknown_keys_denied(self):
        code, _ = self.request("POST", "/api/preview", {"sequence": self.sequence, "command": "ZR"})
        self.assertEqual(code, 400)
        with self.assertRaises(ApiError):
            self.repo.preview({"schema_version": 2, "id": "../unsafe", "steps": []})

    def test_history_is_read_without_changing_device_state(self):
        logs = self.lab / "logs/automation"
        logs.mkdir(parents=True)
        events = [
            {"event": "sequence_started", "time": "2026-10-16T13:20:01+00:00", "sequence": "test"},
            {"event": "device_stopped", "time": "2026-10-16T13:20:20+00:00", "device": "peristaltic_1"},
            {"event": "step_completed", "id": "step_01", "time": "2026-10-16T13:20:20+00:00"},
            {"event": "sequence_completed", "time": "2026-10-16T13:20:20+00:00", "sequence": "test"},
        ]
        (logs / "run-test.jsonl").write_text("\n".join(json.dumps(e) for e in events))
        _, response = self.request("GET", "/api/runs")
        self.assertEqual(response["runs"][0]["state"], "completed")
        _, response = self.request("GET", "/api/status")
        device = next(d for d in response["devices"] if d["id"] == "peristaltic_1")
        self.assertEqual(device["state"], "unknown")
        self.assertEqual(device["last_observation"]["event"], "device_stopped")


if __name__ == "__main__":
    unittest.main()
