"""Linux lock/journal integration using a temporary stand-in, never a pump."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from pump_console.runtime import RuntimeMonitor


@unittest.skipUnless(sys.platform == "linux", "Requires Linux /proc lock ownership")
class LinuxRuntimeTests(unittest.TestCase):
    def test_real_process_lock_journal_and_exit(self):
        worker = """
from datetime import datetime, timezone
import fcntl, json, pathlib, sys
root=pathlib.Path(sys.argv[-1])
lock=(root/"execution.lock").open("w")
fcntl.flock(lock, fcntl.LOCK_EX)
journal=(root/"run-isolated-fixture.jsonl").open("x")
step={"id":"step_01","index":1,"device":"peristaltic_1","action":"dose","operation":"inject","fluid":"water","volume_ml":10,"duration_s":20}
for event, fields in [("configuration_snapshot", {"plan":{"steps":[step]},"sequence":{"id":"isolated_test"}}), ("step_started", {"step":step})]:
    journal.write(json.dumps({"time":datetime.now(timezone.utc).isoformat(),"event":event,**fields})+"\\n")
journal.flush()
print("ready",flush=True)
sys.stdin.read()
journal.close()
lock.close()
"""
        with tempfile.TemporaryDirectory() as directory:
            # Extra argv labels exercise the real controller identity checks;
            # the child executes only the small standard-library fixture above.
            process = subprocess.Popen([sys.executable, "-c", worker, "-m", "lab_automation", "--execute", directory],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                readable, _, _ = select.select([process.stdout], [], [], 5)
                self.assertTrue(readable, "Fixture did not start")
                self.assertEqual(process.stdout.readline().strip(), "ready")
                monitor = RuntimeMonitor(directory)
                snapshot = monitor.snapshot()
                self.assertTrue(snapshot["controller_alive"])
                self.assertEqual(snapshot["state"], "running")
                self.assertEqual(snapshot["active_step"]["device"], "peristaltic_1")
                process.communicate(input="finish", timeout=5)
                snapshot = monitor.snapshot()
                self.assertEqual(snapshot["state"], "idle")
                self.assertIsNone(snapshot["active_step"])
            finally:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=5)
