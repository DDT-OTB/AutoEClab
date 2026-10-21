"""Default operation is an offline preview, without importing pyserial."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import signal
import sys

from .config import ConfigurationError
from .runner import Journal, Runner
from .templates import load_configuration
from .transport import DeviceError

ROOT = Path(__file__).resolve().parents[2]


class RunInterrupted(KeyboardInterrupt):
    def __init__(self, signum):
        super().__init__(f"收到信号 {signum}，中止流程并尝试停止正在动作的设备")
        self.signum = signum


@contextmanager
def interrupt_cleanup():
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    def handler(signum, frame):
        # Avoid a second signal interrupting stop/close cleanup.
        for item in previous:
            signal.signal(item, signal.SIG_IGN)
        raise RunInterrupted(signum)

    try:
        for signum in previous:
            signal.signal(signum, handler)
        yield
    finally:
        for signum, original in previous.items():
            signal.signal(signum, original)


def show_plan(plan):
    print("离线预览：不打开串口，不发送设备指令。")
    print(f"序列：{plan['sequence_id']} — {plan['description']}")
    for step in plan["steps"]:
        detail = ""
        if step["action"] in ("dose", "drain"):
            timing = "待标定" if step["duration_s"] is None else f"{step['duration_s']:.3f} 秒"
            detail = (f"{step.get('operation', step['action'])} {step['volume_ml']:g} mL；"
                      f"{step['rpm']} rpm {step['direction']}；"
                      f"计划时间 {timing}")
            if step["action"] == "drain":
                detail += f"（含 {step['extra_time_fraction']:.0%} 额外抽液时间）"
        elif step["action"] == "switch_valve":
            detail = f"{step['route']} → 通道 {step['channel']}，动作后回读验证"
        elif step["action"] == "syringe_transfer":
            detail = (f"先吸入再排出 {step['volume_ml']:g} mL，单程 {step['delta_steps']} 步，"
                      f"速度 {step['speed_steps_s']} 步/秒")
        else:
            detail = f"等待 {step['seconds']} 秒"
        print(f"{step['index']:02d}. {step['description']}\n    {detail}")
        if "fluid" in step:
            print(f"    Fluid: {step['fluid']}")
        if "source" in step:
            print(f"    液体流向：{step['source']} → {step['destination']}")
    print("合计：" + json.dumps(plan["totals"], ensure_ascii=False))
    if plan["unresolved"]:
        for message in plan["unresolved"]:
            print("待补参数：" + message)
    else:
        print("配置参数完整；此结果仅为预览，不代表实机测试已完成。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", type=Path, default=ROOT / "config/templates",
                        help="每个泵阀的动作模板目录")
    parser.add_argument("--devices", type=Path, help="仅用于显式读取旧版 schema_version=1 配置")
    parser.add_argument("--sequence", type=Path,
                        default=ROOT / "config/sequences/demo_rinse.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="离线预览（默认）")
    mode.add_argument("--execute", action="store_true", help="实际执行；仅在用户确认后使用")
    parser.add_argument("--plan-json", type=Path, help="保存离线展开后的动作计划")
    args = parser.parse_args(argv)
    try:
        devices_doc, sequence, plan = load_configuration(
            args.sequence, args.templates, args.devices)
        if args.plan_json:
            args.plan_json.parent.mkdir(parents=True, exist_ok=True)
            args.plan_json.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.execute:
            show_plan(plan)
            return 0
        if plan["unresolved"]:
            raise ConfigurationError("; ".join(plan["unresolved"]))
        logs = ROOT / "logs/automation"
        logs.mkdir(parents=True, exist_ok=True)
        # Prevent two automation runs from competing for the same pumps.
        with (logs / "execution.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DeviceError("另一个自动化流程正在运行") from error
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            journal = Journal(logs / f"run-{stamp}.jsonl")
            try:
                journal("configuration_snapshot", devices=devices_doc, sequence=sequence, plan=plan)
                with interrupt_cleanup():
                    Runner(devices_doc["devices"], plan, journal).run()
            finally:
                journal.close()
        print("流程执行完成；详细应答保存在 logs/automation。")
        return 0
    except RunInterrupted as error:
        print(str(error), file=sys.stderr)
        return 128 + error.signum
    except KeyboardInterrupt:
        print("流程已中断。", file=sys.stderr)
        return 130
    except (ConfigurationError, DeviceError, OSError, ImportError) as error:
        print(f"流程未完成：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
