from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from pump_console.runtime import RuntimeMonitor, active_controller


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.journal = self.logs / "run-live.jsonl"
        self.journal.touch()
        self.time = datetime(2026, 10, 19, 9, 0, 0, tzinfo=timezone.utc)
        self.owner = {"state": "active", "reason": "controller_journal", "alive": True, "journal": self.journal}
        self.monitor = RuntimeMonitor(self.logs, controller_reader=lambda: self.owner, now=lambda: self.time)
        self.step = {"id": "step_01", "index": 1, "device": "peristaltic_1", "action": "dose",
                     "operation": "inject", "fluid": "water", "volume_ml": 10, "duration_s": 20}
        self.emit("configuration_snapshot", plan={"steps": [self.step]}, sequence={"id": "live_test"})
        self.emit("sequence_started", sequence="live_test")
        self.emit("step_started", step=self.step)

    def tearDown(self):
        self.temp.cleanup()

    def emit(self, event, **fields):
        with self.journal.open("a") as file:
            file.write(json.dumps({"event": event, "time": self.time.isoformat(), **fields}) + "\n")

    def test_active_controller_progress_and_incremental_completion(self):
        self.time += timedelta(seconds=5)
        status = self.monitor.snapshot()
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["active_step"]["progress_fraction"], .25)
        self.assertEqual(status["devices"]["peristaltic_1"]["state"], "executing")
        self.assertFalse(status["active_step"]["volume_measured"])
        self.emit("step_completed", id="step_01")
        self.assertIsNone(self.monitor.snapshot()["active_step"])
        self.assertEqual(self.monitor.snapshot()["completed_steps"], 1)
        self.emit("sequence_completed", sequence="live_test")
        self.assertEqual(self.monitor.snapshot()["state"], "completed")

    def test_no_live_owner_never_replays_an_unfinished_log(self):
        self.monitor.snapshot()
        self.owner = {"state": "idle", "reason": "no_controller", "alive": False}
        status = self.monitor.snapshot()
        self.assertEqual(status["state"], "idle")
        self.assertIsNone(status["active_step"])
        self.assertEqual(status["devices"], {})

    def test_serial_heartbeat_prevents_false_timeout_during_a_long_step(self):
        self.time += timedelta(seconds=16)
        self.assertEqual(self.monitor.snapshot()["state"], "unresponsive")
        self.emit("serial_reply", device="peristaltic_1")
        status = self.monitor.snapshot()
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["active_step"]["state"], "executing")

    def test_stale_events_do_not_show_an_executing_device(self):
        self.time += timedelta(seconds=16)
        status = self.monitor.snapshot()
        self.assertEqual(status["state"], "unresponsive")
        self.assertEqual(status["active_step"]["state"], "unknown")

    def test_progress_waits_for_completion_instead_of_reporting_100_percent(self):
        self.time += timedelta(seconds=30)
        self.emit("serial_reply", device="peristaltic_1")
        self.assertEqual(self.monitor.snapshot()["active_step"]["progress_fraction"], .99)

    def test_error_and_abort_are_not_displayed_as_running(self):
        self.emit("serial_error", device="peristaltic_1")
        self.assertEqual(self.monitor.snapshot()["devices"]["peristaltic_1"]["state"], "error")
        self.emit("sequence_aborted", message="Test communication fault")
        status = self.monitor.snapshot()
        self.assertEqual(status["state"], "aborted")
        self.assertIsNone(status["active_step"])

    def test_partial_line_is_ignored_until_complete_and_new_step_replaces_old(self):
        self.monitor.snapshot()
        self.step = {**self.step, "id": "step_02", "index": 2, "device": "peristaltic_2",
                     "action": "drain", "operation": "withdraw"}
        self.emit("step_completed", id="step_01")
        raw = json.dumps({"event": "step_started", "time": self.time.isoformat(), "step": self.step})
        with self.journal.open("a") as file:
            file.write(raw[:30])
        self.assertIsNone(self.monitor.snapshot()["active_step"])
        with self.journal.open("a") as file:
            file.write(raw[30:] + "\n")
        status = self.monitor.snapshot()
        self.assertEqual(status["active_step"]["device"], "peristaltic_2")
        self.assertEqual(status["devices"]["peristaltic_1"]["state"], "completed")

    def test_wait_action_does_not_need_a_serial_heartbeat(self):
        self.step = {**self.step, "action": "wait", "seconds": 60, "duration_s": None}
        self.emit("step_started", step=self.step)
        self.time += timedelta(seconds=40)
        self.assertEqual(self.monitor.snapshot()["state"], "running")

    def test_os_lock_owner_must_have_the_exact_journal_open(self):
        lock = self.logs / "execution.lock"
        lock.touch()
        stat = lock.stat()
        proc = self.root / "proc"
        fd = proc / "4321/fd"
        fd.mkdir(parents=True)
        (proc / "4321/cmdline").write_bytes(b"python3\0-m\0lab_automation\0--execute\0")
        identity = f"{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}:{stat.st_ino}"
        (proc / "locks").write_text(f"1: FLOCK ADVISORY WRITE 4321 {identity} 0 EOF\n")
        (fd / "5").symlink_to(self.journal)
        self.assertEqual(active_controller(self.logs, proc)["journal"], self.journal.resolve())
        (proc / "4321/cmdline").write_bytes(b"python3\0-m\0pump_console.device_status\0")
        self.assertEqual(active_controller(self.logs, proc)["state"], "checking")
        (proc / "4321/cmdline").write_bytes(b"python3\0other.py\0")
        self.assertEqual(active_controller(self.logs, proc)["state"], "unknown")
        (proc / "locks").write_text("")
        self.assertEqual(active_controller(self.logs, proc)["state"], "idle")


if __name__ == "__main__":
    unittest.main()
