"""Sequential execution, whole-sequence preflight, logging, and fault cleanup."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from .config import ConfigurationError
from .devices import DEVICE_CLASSES, Syringe
from .transport import DeviceError, SerialLink


class Journal:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.file = Path(path).open("x", encoding="utf-8")

    def __call__(self, name, **fields):
        entry = dict(time=datetime.now(timezone.utc).isoformat(), event=name, **fields)
        self.file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


class Runner:
    def __init__(self, devices, plan, event, link_factory=SerialLink,
                 clock=time.monotonic, sleep=time.sleep):
        self.configs, self.plan, self.event = devices, plan, event
        self.link_factory, self.clock, self.sleep = link_factory, clock, sleep
        self.devices = {}
        self.links = []

    def cleanup_event(self, name, **fields):
        try:
            self.event(name, **fields)
        except Exception as error:
            print(f"日志写入失败，继续停止与关闭设备：{error}", file=sys.stderr)

    def run(self):
        if self.plan["unresolved"]:
            raise ConfigurationError("; ".join(self.plan["unresolved"]))
        used = list(dict.fromkeys(step["device"] for step in self.plan["steps"]))
        resolved = [os.path.realpath(self.configs[name]["port"]) for name in used]
        if len(set(resolved)) != len(resolved):
            raise ConfigurationError("多个设备别名指向同一串口，流程尚未开始")
        try:
            for name in used:
                config = self.configs[name]
                link = self.link_factory(name, config, self.event)
                self.links.append(link)
                self.devices[name] = DEVICE_CLASSES[config["kind"]](
                    name, config, link, self.event, clock=self.clock, sleep=self.sleep)
            # All participating devices must pass before the first movement.
            for name, device in self.devices.items():
                if isinstance(device, Syringe):
                    device.preflight([step for step in self.plan["steps"]
                                      if step["device"] == name and step["action"] == "syringe_transfer"])
                else:
                    device.preflight()
                self.event("preflight_passed", device=name)
            self.event("sequence_started", sequence=self.plan["sequence_id"])
            for step in self.plan["steps"]:
                self.event("step_started", step=step)
                device = self.devices[step["device"]]
                action = step["action"]
                if action == "drain":
                    route = step["requires_route"]
                    valve = self.devices[route["device"]]
                    if not valve.normal() or valve.channel() != step["required_channel"]:
                        raise DeviceError("抽液前回读的废液通道与计划不一致，未启动抽液泵")
                    device.run(step)
                elif action == "dose":
                    device.run(step)
                elif action == "switch_valve":
                    device.goto(step["channel"])
                elif action == "syringe_transfer":
                    device.transfer(step)
                elif action == "wait":
                    self.sleep(step["seconds"])
                else:
                    raise ConfigurationError(f"未实现动作 {action}")
                self.event("step_completed", id=step["id"])
            self.event("sequence_completed", sequence=self.plan["sequence_id"])
        except BaseException as error:
            self.cleanup_event("sequence_aborted", exception=type(error).__name__, message=str(error))
            for name, device in reversed(list(self.devices.items())):
                if device.motion_pending:
                    device.event = self.cleanup_event
                    if hasattr(device.link, "event"):
                        device.link.event = self.cleanup_event
                    try:
                        device.stop()
                    except Exception as stop_error:
                        self.cleanup_event("stop_failed", device=name, message=str(stop_error),
                                           motion_state="unknown")
            raise
        finally:
            for link in reversed(self.links):
                try:
                    link.close()
                except Exception as error:
                    self.cleanup_event("close_failed", message=str(error))
