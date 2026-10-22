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
from .cell_safety import CellSafetyError, load_policy, require_safe_volumes
from .workflow import flatten_sequence, block_ranges


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
    def __init__(self, lab_root, data_root, monitoring=None, device_status=None, execution_enabled=False, automation=None):
        self.root = Path(lab_root).resolve()
        self.templates_dir = self.root / "config/templates"
        self.sequences_dir = self.root / "config/sequences"
        self.logs_dir = self.root / "logs/automation"
        self.data_root = Path(data_root)
        self.lock = threading.RLock()
        self.monitoring = {"push_interval_seconds": 1, "stale_after_seconds": 15, **(monitoring or {})}
        self.runtime = RuntimeMonitor(self.logs_dir, self.monitoring["stale_after_seconds"])
        self.device_status = StatusRefresh(self.root, device_status)
        import sys
        sys.path.insert(0, str(self.root / "src"))
        self.compiler = importlib.import_module("lab_automation.templates")
        self.config = importlib.import_module("lab_automation.config")
        from .execution import ExecutionManager
        if type(execution_enabled) is not bool:
            raise ValueError('Invalid execution enable flag')
        self.execution = ExecutionManager(self, execution_enabled, automation)
        from .block_library import BlockLibrary
        self.block_library = BlockLibrary(self)

    def templates(self):
        _, templates = self.compiler.load_templates(self.templates_dir)
        return templates

    def health(self):
        return {
            "ok": True, "service": "pump-console", "version": "0.11.0",
            "function_block_library_administrator_required": True,
            "workflow_schema_versions": [2, 3],
            "mode": "automation" if self.execution.enabled else "configuration", "hardware_execution_enabled": self.execution.enabled,
            "automation_administrator_required": True,
            "syringe_calibration_required_before_each_experiment": True,
            "cell_capacity_ml": load_policy()['cell']['capacity_ml'],
            "cell_preparation_required_before_each_experiment": True,
            "serial_probe_enabled": False, "status_mode": "controller_events",
            "read_only_status_enabled": self.device_status.policy["enabled"],
            "measurement_execution_enabled": False,
            "lab_root": str(self.root), "server_time": utc_now(),
            "ui": {"default_volume_ml": 1, "status_poll_seconds": self.monitoring["push_interval_seconds"]},
        }

    def preview(self, sequence, _templates=None):
        if not isinstance(sequence, dict):
            raise ApiError(400, "sequence 必须是 JSON 对象。")
        identifier(sequence.get("id"))
        if re.search(r"[\u3400-\u9fff]", json.dumps(sequence, ensure_ascii=False)):
            raise ApiError(422, "配置 JSON 请使用英文；界面和说明文档可以使用中文。", "english_json_required")
        try:
            flattened = flatten_sequence(sequence)
            devices, templates = _templates or self.compiler.load_templates(self.templates_dir)
            plan = compile_workflow(sequence, devices, templates, self.compiler, self.config)
        except (ValueError, KeyError, TypeError) as error:
            raise ApiError(422, str(error), "validation_error") from error
        try:
            cell_safety = require_safe_volumes(sequence, load_policy(), plan)
        except CellSafetyError as error:
            raise ApiError(422, str(error), 'cell_capacity_exceeded') from error
        timing = sum(
            step.get("duration_s") or step.get("estimated_movement_s") or step.get("estimated_measurement_s") or step.get("seconds") or 0
            for step in plan["steps"])
        warnings = []
        if any(is_measurement(step) for step in flattened["steps"]):
            warnings.append("测量参数可保存，但电化学工作站尚未接入，测量动作不能执行。")
            if any(is_measurement(step) and step["parameters"]["reference_electrode"] == "unspecified" for step in flattened["steps"]):
                warnings.append("部分测量动作尚未指定参比电极；当前电位参数仅为编排草稿。")
        return {"valid": True, "plan": plan, "warnings": warnings, "estimated_timed_seconds": timing,
                "blocks": block_ranges(sequence),
                "cell_safety": cell_safety, "safety_policy": load_policy(),
                "hardware_executed": False, "generated_at": utc_now()}

    def _sequence_path(self, name):
        path = self.sequences_dir / (identifier(name) + ".json")
        if path.is_symlink() or path.resolve().parent != self.sequences_dir.resolve():
            raise ApiError(400, "Invalid sequence path")
        return path

    def _description_path(self, path):
        companion = path.with_suffix(".md")
        if (companion.is_symlink() or companion.resolve().parent != self.sequences_dir.resolve() or
                (companion.exists() and not companion.is_file())):
            raise ApiError(400, "说明文档路径不安全，未修改文件。", "invalid_description_path")
        return companion

    def _experiment_snapshot(self, name):
        path = self._sequence_path(name)
        if not path.is_file():
            raise ApiError(404, "实验方案不存在。", "not_found")
        raw = path.read_bytes()
        sequence, issue = None, None
        try:
            sequence = parse_json(raw)
            if not isinstance(sequence, dict):
                issue = "方案内容需要是 JSON 对象。"
        except ApiError:
            issue = "文件不是有效的 JSON，可查看内容或下载后修复。"
        steps = None
        schema = sequence.get("schema_version") if isinstance(sequence, dict) else None
        try:
            steps = flatten_sequence(sequence, allow_empty=True).get('steps')
        except (ValueError, KeyError, TypeError):
            pass
        editable = isinstance(steps, list) and all(isinstance(step, dict) for step in steps)
        if issue is None and not editable:
            issue = "此文件不是可编辑的 schema_version=2 或 3 实验方案。"
        description = sequence.get("description", "") if isinstance(sequence, dict) else ""
        companion = path.with_suffix(".md")
        record = {"id": name, "filename": path.name,
                  "description": description if isinstance(description, str) else "",
                  "step_count": len(steps) if isinstance(steps, list) else 0,
                  "block_count": len(sequence['blocks']) if editable and schema == 3 else (1 if editable and steps else 0),
                  "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                  "size_bytes": len(raw), "version": hashlib.sha256(raw).hexdigest(),
                  "schema_version": schema if type(schema) is int else None,
                  "editable": editable, "file_error": issue,
                  "has_markdown": companion.is_file() and not companion.is_symlink(),
                  "contains_measurement": isinstance(steps, list) and any(is_measurement(step) for step in steps if isinstance(step, dict))}
        return path, raw, sequence, record

    def get_experiment(self, name):
        with self.lock:
            _, raw, sequence, record = self._experiment_snapshot(name)
            if not isinstance(sequence, dict):
                parse_json(raw)
                raise ApiError(422, "方案内容需要是 JSON 对象。", "invalid_sequence")
            return {**record, "sequence": sequence}

    def experiment_content(self, name):
        with self.lock:
            _, raw, _, record = self._experiment_snapshot(name)
            try:
                content = raw.decode("utf-8")
            except UnicodeError:
                raise ApiError(422, "文件不是 UTF-8 文本，无法预览。", "invalid_encoding")
            return {**record, "content": content}

    def list_experiments(self):
        with self.lock:
            records = []
            for path in sorted(self.sequences_dir.glob("*.json")):
                try:
                    records.append(self._experiment_snapshot(path.stem)[3])
                except (ApiError, OSError):
                    continue
            return records

    @staticmethod
    def _require_version(record, expected_version):
        if expected_version != record["version"]:
            raise ApiError(409, "方案已被修改，请刷新列表并检查最新内容后重试。", "version_conflict")

    def _require_unused(self, name):
        current = self.execution.snapshot()['current']
        if current and current['controller_alive'] and current['sequence_id'] == name:
            raise ApiError(409, '控制程序正在使用此方案，运行结束后才能修改文件。', 'experiment_in_use')
        runtime = self.runtime.snapshot()
        if runtime.get("controller_alive") is True and runtime.get("sequence_id") in (name, None):
            raise ApiError(409, "控制程序正在使用此方案，运行结束后才能修改文件。", "experiment_in_use")

    def _require_new_target(self, path):
        companion = self._description_path(path)
        if path.exists() or companion.exists():
            raise ApiError(409, "目标文件名已存在，请选择其他方案 ID。", "name_conflict")
        self._require_unused(path.stem)
        return companion

    def _backup_experiment(self, path, action, target=None):
        companion = self._description_path(path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        folder = self.data_root / "backups"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = folder / stamp
        backup.mkdir(mode=0o700)
        shutil.copy2(path, backup / path.name)
        if companion.exists():
            shutil.copy2(companion, backup / companion.name)
        metadata = {"action": action, "source": path.stem, "target": target, "created_at": utc_now()}
        self._atomic_write(backup / "operation.meta.json", (json.dumps(metadata, indent=2) + "\n").encode())
        return stamp

    def _apply_files(self, changes):
        original = {path: path.read_bytes() if path.exists() else None for path in changes}
        attempted = []
        try:
            for path, data in changes.items():
                attempted.append(path)
                if data is None:
                    if path.exists():
                        path.unlink()
                else:
                    self._atomic_write(path, data)
        except OSError:
            for path in reversed(attempted):
                if original[path] is None:
                    if path.exists():
                        path.unlink()
                else:
                    self._atomic_write(path, original[path])
            raise

    def save_experiment(self, sequence, expected_version=None):
        preview = self.preview(sequence)
        path = self._sequence_path(sequence["id"])
        with self.lock:
            self._require_unused(path.stem)
            companion = self._description_path(path)
            backup_id = None
            if path.exists():
                self._require_version(self._experiment_snapshot(path.stem)[3], expected_version)
                backup_id = self._backup_experiment(path, "save")
            else:
                if expected_version is not None:
                    raise ApiError(409, "原方案已被删除或重命名，请检查最新文件后另存。", "version_conflict")
                self._require_new_target(path)
            raw = (json.dumps(sequence, ensure_ascii=False, indent=2) + "\n").encode()
            description = self._description_bytes(companion, sequence, preview)
            self._apply_files({path: raw, companion: description})
            record = self.get_experiment(sequence["id"])
            return {**record, "saved": True, "backup_id": backup_id, "plan": preview["plan"], "hardware_executed": False}

    def rename_experiment(self, name, new_id, expected_version, duplicate=False):
        target = self._sequence_path(new_id)
        if name == new_id:
            raise ApiError(400, "请填写不同的新方案 ID。", "same_name")
        with self.lock:
            path, _, sequence, record = self._experiment_snapshot(name)
            self._require_version(record, expected_version)
            if not record["editable"]:
                raise ApiError(422, "此文件需要先修复格式，再重命名或复制。", "invalid_sequence")
            if not duplicate:
                self._require_unused(name)
            companion = self._description_path(path)
            target_description = self._require_new_target(target)
            renamed = {**sequence, "id": new_id}
            preview = self.preview(renamed)
            existing = companion.read_text() if companion.exists() else ""
            existing = re.sub(r"\A# " + re.escape(name) + r"(?=\r?\n|$)", "# " + new_id, existing, count=1)
            description = self._description_bytes(target_description, renamed, preview, existing=existing)
            backup_id = self._backup_experiment(path, "duplicate" if duplicate else "rename", new_id)
            changes = {target: (json.dumps(renamed, ensure_ascii=False, indent=2) + "\n").encode(), target_description: description}
            if not duplicate:
                changes[path] = None
                if companion.exists():
                    changes[companion] = None
            self._apply_files(changes)
            return {**self.get_experiment(new_id), "copied": duplicate, "renamed": not duplicate,
                    "previous_id": name, "backup_id": backup_id, "hardware_executed": False}

    def delete_experiment(self, name, expected_version):
        with self.lock:
            path, _, _, record = self._experiment_snapshot(name)
            self._require_version(record, expected_version)
            self._require_unused(name)
            companion = self._description_path(path)
            backup_id = self._backup_experiment(path, "delete")
            changes = {path: None}
            if companion.exists():
                changes[companion] = None
            self._apply_files(changes)
            return {"deleted": True, "id": name, "backup_id": backup_id, "hardware_executed": False}

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

    def _description_bytes(self, path, sequence, preview, existing=None):
        start, end = "<!-- pump-console:start -->", "<!-- pump-console:end -->"
        rows = ["| 步骤 | 设备 | 动作 | 液体 | 体积 / 流路 / 方法 |", "| --- | --- | --- | --- | --- |"]
        measurement_blocks = []
        flattened = flatten_sequence(sequence)
        for index, step in enumerate(flattened["steps"], 1):
            values = [str(index), step["device"], step["operation"], step.get("fluid", "—"),
                      str(step.get("volume_ml", step.get("route", step.get("technique", "—"))))]
            rows.append("| " + " | ".join(value.replace("|", r"\|") for value in values) + " |")
            if is_measurement(step):
                measurement_blocks.append(f"\n\n### 第 {index} 步：循环伏安法参数（尚未接入工作站）\n\n" +
                    "~~~json\n" + json.dumps(step["parameters"],ensure_ascii=False,indent=2) + "\n~~~\n" +
                    "JSON 使用 V、V/s、A 和 s；圈数为完整循环数。电位以所填参比电极为准。\n")
        groups = block_ranges(sequence)
        group_text = "\n\n功能块顺序：" + " → ".join(f"{item['id']}（动作 {item['first_step']}–{item['last_step']}）" for item in groups) if groups else ""
        block = (
            f"{start}\n## 图形化编辑器配置摘要\n\n"
            f"方案：{sequence['id']}" + group_text + "\n\n" + "\n".join(rows) + "".join(measurement_blocks) +
            "\n\n此文件由 Pump Console 保存。完成了配置校验和离线预览，未执行硬件动作。\n"
            "液体体积由现有模板换算；计时估算不包含阀动作与通讯时间。\n" + end)
        if existing is None:
            existing = path.read_text() if path.exists() else ""
        existing = existing or f"# {sequence['id']}\n\n"
        if start in existing and end in existing:
            existing = existing[:existing.index(start)] + block + existing[existing.index(end) + len(end):]
        else:
            existing = existing.rstrip() + "\n\n" + block + "\n"
        return existing.encode()

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
        execution = self.execution.snapshot()
        runtime = self.runtime.snapshot(journal_path=self.execution.journal_path())
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
                "motion_commands_sent": None if self.execution.enabled else 0,
                "hardware_execution_enabled": self.execution.enabled, "execution": execution}

    def refresh_status(self):
        if self.device_status.policy["enabled"] is not True:
            raise ApiError(403, "硬件状态读取尚未启用。", "status_refresh_disabled")
        current = self.execution.snapshot()['current']
        if current and current['controller_alive']:
            return {"scan": {"state": "skipped", "reason": "controller_active"}, "status": self.status(),
                    "health": self.health(), "runs": self.runs(), "templates": self.templates(),
                    "measurement_templates": measurement_templates()}
        try:
            scan = self.device_status.refresh(self.templates())
        except RefreshBusy:
            raise ApiError(409, "正在刷新设备状态，请稍候。", "status_refresh_busy")
        return {"scan": scan, "status": self.status(), "health": self.health(), "runs": self.runs(), "templates": self.templates(),
                "measurement_templates": measurement_templates()}
