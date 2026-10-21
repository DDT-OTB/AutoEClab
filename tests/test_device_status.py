"""All serial operations below use fakes; no hardware is accessed."""
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import test_api

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from pump_console.device_status import ReadOnlySerial, query_device, scan, port_in_use
from pump_console.status_refresh import StatusRefresh, RefreshBusy

NOW = lambda: datetime.now(timezone.utc).isoformat()


class FakeLink:
    def __init__(self, status=0, ready=True, identity="00001", timeout=False):
        self.status, self.ready, self.identity, self.timeout = status, ready, identity, timeout
        self.queries_sent, self.closed, self.calls = 0, False, []

    def custom(self, command):
        self.calls.append(command)
        self.queries_sent += 1
        if self.timeout:
            raise TimeoutError()
        return SimpleNamespace(status=self.status, value=6)

    def ascii(self, command):
        self.calls.append(command)
        self.queries_sent += 1
        if self.timeout:
            raise TimeoutError()
        return SimpleNamespace(error=0, ready=self.ready, payload={"?202":self.identity, "?":"1200", "?6":"i"}.get(command, ""))

    def close(self):
        self.closed = True


class ReadOnlyQueryTests(unittest.TestCase):
    def config(self, kind):
        return {"kind":kind, "channels":6, "routes":{"waste_1":5,"waste_2":6},
                "expected_serial_number":"00001", "stroke_steps":12000}

    def query(self, kind, link):
        return query_device("test_device", self.config(kind), link_factory=lambda *args:link, check_port=False)

    def test_allowlist_rejects_every_motion_command_before_serial_io(self):
        link = object.__new__(ReadOnlySerial)
        link.request = Mock()
        for command in (0x44, 0x47, 0x48, 0x49, 0x4B, 0x3F):
            with self.assertRaises(ValueError):
                link.custom(command)
        for command in ("ZR", "T", "IR", "OR", "A100R", "v100V100A0R"):
            with self.assertRaises(ValueError):
                link.ascii(command)
        link.request.assert_not_called()

    def test_peristaltic_idle_and_busy_use_only_status_query(self):
        for code, state in ((0,"idle"), (4,"busy"), (254,"not_ready")):
            link = FakeLink(status=code)
            result = self.query("peristaltic", link)
            self.assertEqual(result["state"], state)
            self.assertEqual(link.calls, [0x4A])
            self.assertTrue(link.closed)

    def test_valve_channel_is_read_without_switching(self):
        link = FakeLink()
        result = self.query("valve", link)
        self.assertEqual(result["channel"], 6)
        self.assertEqual(result["route"], "waste_2")
        self.assertEqual(link.calls, [0x4A, 0x3E])

    def test_syringe_identity_position_and_valve_are_read_without_movement(self):
        link = FakeLink()
        result = self.query("syringe", link)
        self.assertEqual(result["state"], "idle")
        self.assertEqual(result["position_steps"], 1200)
        self.assertEqual(result["valve_position"], "i")
        self.assertEqual(link.calls, ["Q", "?202", "?", "?6"])

    def test_busy_or_wrong_syringe_identity_does_not_query_position(self):
        link = FakeLink(ready=False)
        self.assertEqual(self.query("syringe", link)["state"], "busy")
        self.assertEqual(link.calls, ["Q", "?202"])
        link = FakeLink(identity="different")
        self.assertEqual(self.query("syringe", link)["reason"], "identity_mismatch")
        self.assertEqual(link.calls, ["Q", "?202"])

    def test_query_timeout_is_reported_and_port_is_closed(self):
        link = FakeLink(timeout=True)
        result = self.query("peristaltic", link)
        self.assertEqual(result["state"], "no_response")
        self.assertEqual(result["motion_commands_sent"], 0)
        self.assertTrue(link.closed)

    def test_missing_and_unsupported_ports_never_open(self):
        factory = Mock(side_effect=AssertionError("Serial should not be opened"))
        result = query_device("missing", {"kind":"peristaltic","port":"/nonexistent/pump-console-test"}, factory)
        self.assertEqual(result["state"], "port_missing")
        result = query_device("other", {"kind":"unknown"}, factory)
        self.assertEqual(result["state"], "unsupported")
        factory.assert_not_called()

    def test_controller_execution_lock_prevents_all_device_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)/"logs/automation"
            logs.mkdir(parents=True)
            query = Mock()
            with (logs/"execution.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = scan(directory, {"pump":{"device":{}}}, query=query)
            self.assertEqual(result["state"], "skipped")
            self.assertEqual(result["queries_sent"], 0)
            query.assert_not_called()

    def test_open_serial_descriptor_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = root/"serial"
            port.touch()
            fd = root/"proc/99999999/fd"
            fd.mkdir(parents=True)
            (fd/"4").symlink_to(port)
            self.assertTrue(port_in_use(port, root/"proc"))


def result_for(templates, age=0):
    when = (datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
    return {"state":"completed","sampled_at":when,"queries_sent":len(templates),"motion_commands_sent":0,
            "devices":[{"id":name,"state":"idle","queried_at":when,"queries_sent":1,"motion_commands_sent":0} for name in templates]}


class RefreshCacheTests(unittest.TestCase):
    def test_polling_is_passive_and_repeated_refresh_is_rate_limited(self):
        templates = {"pump":{"device":{}}}
        launch = Mock(return_value=subprocess.CompletedProcess([],0,json.dumps(result_for(templates))))
        cache = StatusRefresh("/unused", {"enabled":True}, launch)
        self.assertEqual(cache.snapshot()["state"], "never")
        launch.assert_not_called()
        self.assertEqual(cache.refresh(templates)["state"], "completed")
        self.assertEqual(cache.refresh(templates)["state"], "cached")
        self.assertEqual(launch.call_count, 1)
        self.assertFalse(cache.snapshot()["devices"]["pump"]["stale"])

    def test_expired_status_is_marked_stale(self):
        templates = {"pump":{"device":{}}}
        launch = Mock(return_value=subprocess.CompletedProcess([],0,json.dumps(result_for(templates,age=31))))
        cache = StatusRefresh("/unused", {"enabled":True}, launch)
        self.assertTrue(cache.refresh(templates)["devices"]["pump"]["stale"])

    def test_overlapping_refresh_and_disabled_queries_do_not_launch_workers(self):
        launch = Mock()
        cache = StatusRefresh("/unused", {"enabled":True}, launch)
        cache.work_lock.acquire()
        with self.assertRaises(RefreshBusy):
            cache.refresh({})
        cache.work_lock.release()
        cache.policy["enabled"] = False
        with self.assertRaises(ValueError):
            cache.refresh({})
        launch.assert_not_called()

    def test_timeout_clears_results_and_releases_refresh_lock(self):
        launch = Mock(side_effect=subprocess.TimeoutExpired("status",10))
        cache = StatusRefresh("/unused", {"enabled":True}, launch)
        result = cache.refresh({})
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["devices"], {})
        self.assertFalse(result["in_progress"])
        self.assertFalse(cache.work_lock.locked())


class RefreshApiTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    request = test_api.ApiTests.request

    def enable_fake(self):
        self.repo.device_status.policy["enabled"] = True
        self.repo.device_status.launcher = Mock(return_value=subprocess.CompletedProcess([],0,
            json.dumps(result_for(self.repo.templates()))))

    def test_admin_refresh_updates_all_cards_without_loading_serial_in_web_process(self):
        self.enable_fake()
        code, result = self.request("POST","/api/status/refresh",{})
        self.assertEqual(code,200)
        self.assertEqual(len(result["status"]["devices"]),5)
        self.assertTrue(all(device["hardware"]["state"]=="idle" for device in result["status"]["devices"]))
        self.assertEqual(result["status"]["motion_commands_sent"],0)
        self.assertNotIn("lab_automation.transport",sys.modules)

    def test_anonymous_viewer_and_missing_csrf_cannot_trigger_queries(self):
        self.enable_fake()
        self.assertEqual(self.request("POST","/api/status/refresh",{},headers={"Cookie":""})[0],401)
        self.assertEqual(self.request("POST","/api/status/refresh",{},headers={"X-CSRF-Token":""})[0],403)
        self.auth.create_user("observer","Read only passphrase 2026",role="viewer",temporary=False)
        token, session = self.auth.login("observer","Read only passphrase 2026","127.0.0.1")
        self.assertEqual(self.request("POST","/api/status/refresh",{},headers={
            "Cookie":"pump_console_session="+token,"X-CSRF-Token":session["csrf_token"]})[0],403)
        self.repo.device_status.launcher.assert_not_called()

    def test_request_cannot_supply_ports_or_device_commands(self):
        self.enable_fake()
        self.assertEqual(self.request("POST","/api/status/refresh",{"command":"ZR"})[0],400)
        self.repo.device_status.launcher.assert_not_called()

    def test_query_feature_is_disabled_by_default(self):
        self.assertEqual(self.request("POST","/api/status/refresh",{})[0],403)

    def test_starting_a_new_run_invalidates_older_hardware_readings(self):
        self.enable_fake()
        self.assertEqual(self.request("POST","/api/status/refresh",{})[0],200)
        logs = self.repo.logs_dir
        logs.mkdir(parents=True,exist_ok=True)
        (logs/"run-new.jsonl").write_text(json.dumps({"event":"sequence_started","sequence":"new_run","time":NOW()})+"\n")
        _, status = self.request("GET","/api/status")
        self.assertTrue(all(device["hardware"]["stale"] for device in status["devices"]))
        self.assertTrue(all(device["state"]=="unknown" for device in status["devices"]))
