from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading

from .runtime import RuntimeMonitor, timestamp
from .status_refresh import RefreshBusy, StatusRefresh
from .measurement import compile_workflow, is_measurement, measurement_templates


class ApiError(Exception):
    def __init__(self, status, message, code="invalid_request"):
        super().__init__(message)
        self.status, self.code = status, code


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_json(data):
    def unique(pairs):
        value = {}
        for key, entry in pairs:
            if key in value:
                raise ApiError(400, f"Duplicate JSON key: {key}")
            value[key] = entry
        return value
    def reject_constant(value):
        raise ApiError(400, f"Invalid JSON number: {value}")
    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=reject_constant)
    except (ValueError, UnicodeDecodeError) as error:
        raise ApiError(400, f"Invalid JSON: {error}") from error


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", value):
        raise ApiError(400, "方案 ID 仅允许英文字母、数字、下划线和短横线，且以字母开头。")
    return value


class LabRepository:
    def __init__(self, lab_root, data_root, monitoring=None, device_status=None):
        self.root = Path(lab_root).resolve()
        self.templates_dir = self.root / "config/templates"
        self.sequences_dir = self.root / "config/sequences"
        self.logs_dir = self.root / "logs/automation"
        self.data_root = Path(data_root)
        self.lock = threading.Lock()
        self.monitoring = {"push_interval_seconds": 1, "stale_after_seconds": 15, **(monitoring or {})}
        self.runtime = RuntimeMonitor(self.logs_dir, self.monitoring["stale_after_seconds"])
        self.device_status = StatusRefresh(self.root, device_status)
        import sys
        sys.path.insert(0, str(self.root / "src"))
        self.compiler = importlib.import_module("lab_automation.templates")
        self.config = importlib.import_module("lab_automation.config")

    def templates(self):
        _, templates = self.compiler.load_templates(self.templates_dir)
        return templates

    def health(self):
        return {
            "ok": True, "service": "pump-console", "version": "0.6.0",
            "mode": "configuration", "hardware_execution_enabled": False,
            "serial_probe_enabled": False, "status_mode": "controller_events",
            "read_only_status_enabled": self.device_status.policy["enabled"],
            "measurement_execution_enabled": False,
            "lab_root": str(self.root), "server_time": utc_now(),
            "ui": {"default_volume_ml": 1, "status_poll_seconds": self.monitoring["push_interval_seconds"]},
        }

    def preview(self, sequence):
        if not isinstance(sequence, dict):
            raise ApiError(400, "sequence 必须是 JSON 对象。")
        identifier(sequence.get("id"))
        if type(sequence.get("schema_version")) is not int or sequence.get("schema_version") != 2:
            raise ApiError(422, "图形化编辑器使用 schema_version=2。")
        if re.search(r"[\u3400-\u9fff]", json.dumps(sequence, ensure_ascii=False)):
            raise ApiError(422, "配置 JSON 请使用英文；界面和说明文档可以使用中文。", "english_json_required")
        try:
            devices, templates = self.compiler.load_templates(self.templates_dir)
            plan = compile_workflow(sequence, devices, templates, self.compiler, self.config)
        except (ValueError, KeyError, TypeError) as error:
            raise ApiError(422, str(error), "validation_error") from error
        timing = sum(
            step.get("duration_s") or step.get("estimated_movement_s") or step.get("estimated_measurement_s") or step.get("seconds") or 0
            for step in plan["steps"])
        warnings = []
        if any(is_measurement(step) for step in sequence["steps"]):
            warnings.append("测量参数可保存，但电化学工作站尚未接入，测量动作不能执行。")
            if any(is_measurement(step) and step["parameters"]["reference_electrode"] == "unspecified" for step in sequence["steps"]):
                warnings.append("部分测量动作尚未指定参比电极；当前电位参数仅为编排草稿。")
        return {"valid": True, "plan": plan, "warnings": warnings, "estimated_timed_seconds": timing,
                "hardware_executed": False, "generated_at": utc_now()}

    def _sequence_path(self, name):
        path = self.sequences_dir / (identifier(name) + ".json")
        if not path.resolve().is_relative_to(self.sequences_dir.resolve()):
            raise ApiError(400, "Invalid sequence path")
        return path

    def get_experiment(self, name):
        path = self._sequence_path(name)
        if not path.is_file():
            raise ApiError(404, "实验方案不存在。", "not_found")
        raw = path.read_bytes()
        return {"sequence": parse_json(raw), "version": hashlib.sha256(raw).hexdigest(),
                "filename": path.name, "modified_at": datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc).isoformat()}

    def list_experiments(self):
        records = []
        for path in sorted(self.sequences_dir.glob("*.json")):
            try:
                record = self.get_experiment(path.stem)
                sequence = record["sequence"]
                records.append({
                    "id": path.stem, "description": sequence.get("description", ""),
                    "step_count": len(sequence.get("steps", [])),
                    "modified_at": record["modified_at"], "version": record["version"]})
            except (ApiError, OSError, AttributeError):
                continue
        return records

    def save_experiment(self, sequence, expected_version=None):
        preview = self.preview(sequence)
        path = self._sequence_path(sequence["id"])
        with self.lock:
            if path.exists():
                current = hashlib.sha256(path.read_bytes()).hexdigest()
                if expected_version != current:
                    raise ApiError(409, "方案已存在或被修改，请重新加载，或使用新的方案 ID 另存。", "version_conflict")
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                backup = self.data_root / "backups" / stamp
                backup.mkdir(parents=True, exist_ok=False)
                shutil.copy2(path, backup / path.name)
                if path.with_suffix(".md").exists():
                    shutil.copy2(path.with_suffix(".md"), backup / path.with_suffix(".md").name)
            raw = (json.dumps(sequence, ensure_ascii=False, indent=2) + "\n").encode()
            self._atomic_write(path, raw)
            self._write_description(path.with_suffix(".md"), sequence, preview)
        record = self.get_experiment(sequence["id"])
        return {**record, "saved": True, "plan": preview["plan"], "hardware_executed": False}

    @staticmethod
    def _atomic_write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".pump-console-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _write_description(self, path, sequence, preview):
        start, end = "<!-- pump-console:start -->", "<!-- pump-console:end -->"
        rows = ["| 步骤 | 设备 | 动作 | 液体 | 体积 / 流路 / 方法 |", "| --- | --- | --- | --- | --- |"]
        measurement_blocks = []
        for index, step in enumerate(sequence["steps"], 1):
            values = [str(index), step["device"], step["operation"], step.get("fluid", "—"),
                      str(step.get("volume_ml", step.get("route", step.get("technique", "—"))))]
            rows.append("| " + " | ".join(value.replace("|", r"\|") for value in values) + " |")
            if is_measurement(step):
                measurement_blocks.append(f"\n\n### 第 {index} 步：循环伏安法参数（尚未接入工作站）\n\n" +
                    "~~~json\n" + json.dumps(step["parameters"],ensure_ascii=False,indent=2) + "\n~~~\n" +
                    "JSON 使用 V、V/s、A 和 s；圈数为完整循环数。电位以所填参比电极为准。\n")
        block = (
            f"{start}\n## 图形化编辑器配置摘要\n\n"
            f"方案：{sequence['id']}\n\n" + "\n".join(rows) + "".join(measurement_blocks) +
            "\n\n此文件由 Pump Console 保存。完成了配置校验和离线预览，未执行硬件动作。\n"
            "液体体积由现有模板换算；计时估算不包含阀动作与通讯时间。\n" + end)
        existing = path.read_text() if path.exists() else f"# {sequence['id']}\n\n"
        if start in existing and end in existing:
            existing = existing[:existing.index(start)] + block + existing[existing.index(end) + len(end):]
        else:
            existing = existing.rstrip() + "\n\n" + block + "\n"
        self._atomic_write(path, existing.encode())

    def runs(self, limit=8):
        records = []
        if not self.logs_dir.is_dir():
            return records
        paths = sorted(self.logs_dir.glob("run-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths[:limit]:
            events = []
            try:
                if path.stat().st_size > 4_000_000:
                    continue
                for line in path.read_text().splitlines():
                    try:
                        event = json.loads(line)
                        if event.get("event") not in ("serial_reply", "configuration_snapshot"):
                            events.append(event)
                        elif event.get("event") == "configuration_snapshot":
                            snapshot = event
                    except ValueError:
                        continue
                if not events:
                    continue
                initial = next((e for e in events if e["event"] == "sequence_started"), {})
                complete = next((e for e in events if e["event"] == "sequence_completed"), None)
                aborted = next((e for e in events if e["event"] == "sequence_aborted"), None)
                records.append({
                    "id": path.stem, "sequence_id": initial.get("sequence", "preflight"),
                    "state": "completed" if complete else "aborted" if aborted else "unknown",
                    "started_at": initial.get("time", events[0].get("time")),
                    "finished_at": (complete or aborted or {}).get("time"),
                    "completed_steps": sum(e["event"] == "step_completed" for e in events),
                    "message": (aborted or {}).get("message"), "events": events[-80:]})
            except OSError:
                continue
        return records

    def status(self):
        templates = self.templates()
        runs = self.runs()
        runtime = self.runtime.snapshot()
        scan = self.device_status.snapshot()
        latest_run = max((timestamp(run.get("started_at")) for run in runs if timestamp(run.get("started_at"))), default=None)
        for hardware in scan["devices"].values():
            if latest_run and timestamp(hardware["queried_at"]) < latest_run:
                hardware["stale"] = True
        observed = {}
        for run in reversed(runs):
            for event in run["events"]:
                name = event.get("device") or event.get("step", {}).get("device")
                if name and event["event"] in (
                        "device_stopped", "syringe_transfer_finished", "valve_position_verified", "valve_already_positioned"):
                    observed[name] = {"event": event["event"], "at": event["time"],
                                      "channel": event.get("channel")}
        devices = []
        for name, template in templates.items():
            config = template["device"]
            activity, hardware = runtime["devices"].get(name), scan["devices"].get(name)
            queried = hardware is not None and not hardware["stale"] and runtime["controller_alive"] is not True
            devices.append({
                "id": name, "label": config["label"], "kind": config["kind"],
                "fluid": template["action"].get("fluid"),
                "port": config["port"], "port_present": Path(config["port"]).exists(),
                "state": activity["state"] if activity else hardware["state"] if queried else "unknown",
                "activity": activity, "source": "controller_journal" if activity else "read_only_query" if queried else "unknown",
                "hardware": hardware,
                "last_observation": observed.get(name), "rpm": config.get("rpm"),
                "speed_steps_s": config.get("speed_steps_s"), "channels": config.get("channels")})
        return {"devices": devices, "sampled_at": utc_now(), "mode": "controller_events", "runtime": runtime,
                "device_scan": scan, "serial_commands_sent": scan.get("queries_sent", 0),
                "motion_commands_sent": 0, "hardware_execution_enabled": False}

    def refresh_status(self):
        if self.device_status.policy["enabled"] is not True:
            raise ApiError(403, "硬件状态读取尚未启用。", "status_refresh_disabled")
        try:
            scan = self.device_status.refresh(self.templates())
        except RefreshBusy:
            raise ApiError(409, "正在刷新设备状态，请稍候。", "status_refresh_busy")
        return {"scan": scan, "status": self.status(), "health": self.health(), "runs": self.runs(), "templates": self.templates(),
                "measurement_templates": measurement_templates()}
