"""Read controller lock ownership and incrementally consume its journal."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import threading


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def active_controller(logs_dir, proc_root=Path("/proc")):
    logs_dir = Path(logs_dir).resolve()
    try:
        stat = (logs_dir / "execution.lock").stat()
    except FileNotFoundError:
        return {"state": "idle", "reason": "no_controller", "alive": False}
    except OSError:
        return {"state": "unknown", "reason": "lock_unavailable", "alive": None}
    try:
        for row in (proc_root / "locks").read_text().splitlines():
            fields = row.split()
            if len(fields) < 8 or fields[1:4] != ["FLOCK", "ADVISORY", "WRITE"]:
                continue
            major, minor, inode = fields[5].split(":")
            if (int(major, 16), int(minor, 16), int(inode)) != (os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino):
                continue
            pid = int(fields[4])
            process = proc_root / str(pid)
            args = (process / "cmdline").read_bytes().split(b"\0")
            if b"pump_console.device_status" in args:
                return {"state": "checking", "reason": "read_only_status_refresh", "alive": False}
            if b"lab_automation" not in args or b"--execute" not in args:
                return {"state": "unknown", "reason": "unrecognized_lock_owner", "alive": None}
            for descriptor in (process / "fd").iterdir():
                try:
                    target = descriptor.resolve(strict=True)
                    if target.parent == logs_dir and target.name.startswith("run-") and target.suffix == ".jsonl":
                        return {"state": "active", "reason": "controller_journal", "alive": True, "journal": target}
                except OSError:
                    continue
            return {"state": "starting", "reason": "journal_not_open", "alive": True}
        return {"state": "idle", "reason": "no_controller", "alive": False}
    except (OSError, ValueError):
        return {"state": "unknown", "reason": "controller_unavailable", "alive": None}


class RuntimeMonitor:
    def __init__(self, logs_dir, stale_after_seconds=15, controller_reader=None, now=None):
        self.logs_dir, self.stale_after = Path(logs_dir), stale_after_seconds
        self.controller_reader = controller_reader or (lambda: active_controller(self.logs_dir))
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self._reset(None)

    def _reset(self, identity):
        self.identity, self.offset, self.partial = identity, 0, b""
        self.plan, self.sequence_id, self.current, self.terminal = {}, None, None, None
        self.completed, self.finished, self.faults = set(), set(), set()
        self.last_at = None

    def _read(self, path):
        stat = path.stat()
        identity = (str(path), stat.st_dev, stat.st_ino)
        if identity != self.identity or stat.st_size < self.offset:
            self._reset(identity)
        with path.open("rb") as file:
            file.seek(self.offset)
            raw = file.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Journal update too large")
            self.offset = file.tell()
        lines = (self.partial + raw).split(b"\n")
        self.partial = lines.pop()
        if len(self.partial) > 262144:
            raise ValueError("Journal line too large")
        for line in lines:
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or not timestamp(event.get("time")):
                    continue
                kind, self.last_at = event.get("event"), event["time"]
                if kind == "configuration_snapshot":
                    self.plan = event.get("plan", {})
                    self.sequence_id = event.get("sequence", {}).get("id")
                elif kind == "sequence_started":
                    self.sequence_id = event.get("sequence")
                elif kind == "step_started" and isinstance(event.get("step"), dict):
                    self.current = event
                elif kind == "step_completed":
                    self.completed.add(event.get("id"))
                    if self.current and self.current["step"].get("id") == event.get("id"):
                        self.finished.add(self.current["step"].get("device"))
                        self.current = None
                elif kind in ("serial_error", "stop_failed"):
                    self.faults.add(event.get("device"))
                elif kind in ("sequence_completed", "sequence_aborted"):
                    self.terminal = event
            except (ValueError, TypeError, AttributeError):
                continue

    def snapshot(self):
        with self.lock:
            now, owner = self.now(), self.controller_reader()
            result = dict(state=owner["state"], reason=owner["reason"], source="controller_journal",
                          controller_alive=owner["alive"], run_id=None, sequence_id=None, step_count=0,
                          completed_steps=0, active_step=None, devices={}, stale_after_seconds=self.stale_after,
                          updated_at=now.isoformat(), last_event_at=None, hardware_feedback_independent=False)
            if owner["state"] != "active":
                return result
            path = Path(owner["journal"])
            try:
                self._read(path)
            except (OSError, ValueError):
                return {**result, "state": "unknown", "reason": "journal_unavailable"}
            result.update(run_id=path.stem, sequence_id=self.sequence_id,
                          step_count=len(self.plan.get("steps", [])), completed_steps=len(self.completed),
                          last_event_at=self.last_at)
            if self.terminal:
                result.update(state="completed" if self.terminal["event"] == "sequence_completed" else "aborted",
                              reason=self.terminal["event"])
                return result
            last = timestamp(self.last_at)
            age = (now - last).total_seconds() if last else None
            stale = age is None or age > self.stale_after or age < -5
            result.update(state="unresponsive" if stale else "running",
                          reason="journal_stale" if stale else "controller_journal")
            for step in self.plan.get("steps", []):
                result["devices"][step.get("device")] = {"state": "completed" if step.get("device") in self.finished else "waiting"}
            if not self.current:
                return result
            step = self.current["step"]
            elapsed = max(0, (now - timestamp(self.current["time"])).total_seconds())
            duration = step.get("duration_s") or step.get("estimated_movement_s") or step.get("seconds")
            if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
                duration = None
            if step.get("action") == "wait" and duration and elapsed <= duration + self.stale_after:
                stale = False
                result.update(state="running", reason="controller_wait")
            active = {key: step[key] for key in ("id", "index", "device", "action", "operation", "fluid", "volume_ml", "route", "channel") if key in step}
            active.update(started_at=self.current["time"], elapsed_seconds=round(elapsed, 2), estimated_seconds=duration,
                          progress_fraction=min(elapsed / duration, 0.99) if duration else None,
                          progress_is_estimate=True, volume_measured=False, state="unknown" if stale else "executing")
            if step.get("device") in self.faults:
                active["state"] = "error"
                result.update(state="error", reason="controller_error")
            result["active_step"] = active
            result["devices"][step.get("device")] = active
            return result
