"""Run on-demand, bounded status queries; ordinary polling only reads this cache."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


class RefreshBusy(RuntimeError):
    pass


class StatusRefresh:
    def __init__(self, lab_root, policy=None, launcher=None):
        self.lab_root = Path(lab_root)
        self.policy = {"enabled": False, "cache_ttl_seconds": 30, "min_interval_seconds": 3,
                       "timeout_seconds": 10, **(policy or {})}
        self.launcher = launcher or subprocess.run
        self.work_lock, self.data_lock = threading.Lock(), threading.Lock()
        self.devices = {}
        self.last = {"state": "never", "queries_sent": 0}
        self.in_progress, self.last_refresh = False, None

    def snapshot(self):
        with self.data_lock:
            result = deepcopy(self.last)
            result.update(enabled=self.policy["enabled"], in_progress=self.in_progress,
                          cache_ttl_seconds=self.policy["cache_ttl_seconds"])
            devices = deepcopy(self.devices)
        now = datetime.now(timezone.utc)
        for device in devices.values():
            age = (now-datetime.fromisoformat(device["queried_at"])).total_seconds()
            device.update(age_seconds=round(max(0, age), 1), stale=age < -5 or age > self.policy["cache_ttl_seconds"])
        result["devices"] = devices
        return result

    def refresh(self, templates):
        if self.policy["enabled"] is not True:
            raise ValueError("Read-only status refresh is disabled")
        if not self.work_lock.acquire(blocking=False):
            raise RefreshBusy("A status refresh is already in progress")
        try:
            if self.last_refresh is not None and time.monotonic()-self.last_refresh < self.policy["min_interval_seconds"]:
                return {**self.snapshot(), "state": "cached"}
            with self.data_lock:
                self.in_progress = True
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            try:
                process = self.launcher([sys.executable, "-m", "pump_console.device_status"],
                    input=json.dumps({"lab_root": str(self.lab_root), "templates": templates}),
                    capture_output=True, text=True, timeout=self.policy["timeout_seconds"], env=environment)
                if process.returncode:
                    raise ValueError("Status worker failed")
                result = json.loads(process.stdout)
                if result.get("state") not in ("completed", "skipped") or not isinstance(result.get("devices"), list):
                    raise ValueError("Invalid status worker response")
                if result["state"] == "completed" and {item.get("id") for item in result["devices"]} != set(templates):
                    raise ValueError("Incomplete status worker response")
                for item in result["devices"]:
                    when = datetime.fromisoformat(item["queried_at"])
                    if when.tzinfo is None or not isinstance(item.get("state"), str) or item.get("motion_commands_sent") != 0:
                        raise ValueError("Invalid device status record")
                with self.data_lock:
                    if result["state"] == "completed":
                        self.devices = {item["id"]: item for item in result["devices"]}
                    self.last = {key: value for key, value in result.items() if key != "devices"}
                    self.last_refresh = time.monotonic()
            except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
                with self.data_lock:
                    self.devices = {}
                    self.last = {"state": "failed", "reason": "status_refresh_failed", "queries_sent": 0}
            finally:
                with self.data_lock:
                    self.in_progress = False
            return self.snapshot()
        finally:
            self.work_lock.release()
