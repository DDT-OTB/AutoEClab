"""Validate JSON and compile an offline execution plan."""
from __future__ import annotations

import json
import math
from pathlib import Path


class ConfigurationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ConfigurationError(message)


def number(value, name, minimum=0, maximum=None, positive=False, integer=False):
    require(type(value) in (int, float) and math.isfinite(value), f"{name}: 需要有限数值")
    require(value > minimum if positive else value >= minimum, f"{name}: 数值过小")
    if maximum is not None:
        require(value <= maximum, f"{name}: 超过上限 {maximum}")
    if integer:
        require(type(value) is int, f"{name}: 需要整数")
    return value


def keys(obj, allowed, required, name):
    require(isinstance(obj, dict), f"{name}: 需要 JSON 对象")
    require(not (set(obj) - set(allowed)), f"{name}: 未知字段 {set(obj) - set(allowed)}")
    require(not (set(required) - set(obj)), f"{name}: 缺少字段 {set(required) - set(obj)}")


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"{path}: 重复 JSON 字段 {key}")
            result[key] = value
        return result

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"{path}: {error}") from error


COMMON = {"kind", "label", "port", "baudrate", "address", "response_timeout_s",
          "completion_timeout_s", "poll_interval_s", "stop_timeout_s", "serial", "notes"}
KINDS = {
    "peristaltic": {"liquid", "rpm", "direction", "max_rpm", "max_run_seconds", "calibration"},
    "syringe": {"liquid", "capacity_ml", "stroke_steps", "speed_steps_s",
                "max_speed_steps_s", "expected_serial_number", "valve_timeout_s",
                "move_timeout_factor", "move_timeout_margin_s"},
    "valve": {"channels", "routes"},
}

OPERATION_NAMES = {
    "dose": "inject",
    "drain": "withdraw",
    "syringe_transfer": "inject",
    "switch_valve": "switch_route",
    "wait": "wait",
}


def validate_devices(document):
    keys(document, {"schema_version", "devices"}, {"schema_version", "devices"}, "devices.json")
    require(type(document["schema_version"]) is int and document["schema_version"] == 1,
            "不支持的设备配置版本")
    devices = document["devices"]
    require(isinstance(devices, dict) and devices, "devices 不能为空")
    ports = set()
    for name, dev in devices.items():
        require(isinstance(dev, dict), f"{name}: 需要设备对象")
        kind = dev.get("kind")
        require(isinstance(kind, str) and kind in KINDS, f"{name}: 不支持的设备类型 {kind}")
        keys(dev, COMMON | KINDS[kind],
             (COMMON - {"notes"}) | (KINDS[kind] - {"notes"}), name)
        require(isinstance(dev["label"], str) and dev["label"], f"{name}: label 不能为空")
        port = dev["port"]
        require(isinstance(port, str) and port.startswith("/dev/"), f"{name}: 需要绝对串口路径")
        require(port not in ports, f"{name}: 串口路径重复")
        ports.add(port)
        require(type(dev["baudrate"]) is int and dev["baudrate"] in
                (9600, 19200, 38400, 57600, 115200), f"{name}: 不支持的波特率")
        for field in ("response_timeout_s", "completion_timeout_s", "poll_interval_s",
                      "stop_timeout_s"):
            number(dev[field], f"{name}.{field}", positive=True)
        serial = dev["serial"]
        serial_keys = {"data_bits", "parity", "stop_bits", "rts", "dtr",
                       "rtscts", "dsrdtr", "xonxoff", "read_timeout_s"}
        keys(serial, serial_keys, serial_keys, f"{name}.serial")
        require(serial["data_bits"] == 8 and serial["parity"] == "N"
                and serial["stop_bits"] == 1, f"{name}: 说明书协议需要 8N1")
        for field in ("rts", "dtr", "rtscts", "dsrdtr", "xonxoff"):
            require(type(serial[field]) is bool, f"{name}.serial.{field}: 需要布尔值")
        number(serial["read_timeout_s"], f"{name}.serial.read_timeout_s",
               positive=True, maximum=dev["response_timeout_s"])
        require(dev["poll_interval_s"] <= dev["completion_timeout_s"],
                f"{name}: 轮询间隔大于完成超时")
        if kind == "syringe":
            for field in ("valve_timeout_s", "move_timeout_factor", "move_timeout_margin_s"):
                number(dev[field], f"{name}.{field}", positive=True)
            require(isinstance(dev["address"], str) and len(dev["address"]) == 1 and
                    0x31 <= ord(dev["address"]) <= 0x3F, f"{name}: 需要单设备 ASCII 地址字符")
            require(dev["baudrate"] in (9600, 38400), f"{name}: 注射泵仅支持 9600/38400")
            number(dev["capacity_ml"], f"{name}.capacity_ml", positive=True)
            number(dev["stroke_steps"], f"{name}.stroke_steps", positive=True, integer=True)
            number(dev["max_speed_steps_s"], f"{name}.max_speed_steps_s", positive=True, integer=True)
            number(dev["speed_steps_s"], f"{name}.speed_steps_s", positive=True, integer=True,
                   maximum=dev["max_speed_steps_s"])
            serial = dev["expected_serial_number"]
            require(serial is None or (isinstance(serial, str) and serial), f"{name}: 序列号格式错误")
        else:
            number(dev["address"], f"{name}.address", maximum=127, integer=True)
        if kind == "peristaltic":
            number(dev["max_rpm"], f"{name}.max_rpm", maximum=65535, positive=True, integer=True)
            number(dev["rpm"], f"{name}.rpm", maximum=dev["max_rpm"], positive=True, integer=True)
            number(dev["max_run_seconds"], f"{name}.max_run_seconds", positive=True)
            require(dev["direction"] in ("CW", "CCW"), f"{name}: direction 必须是 CW/CCW")
            cal = dev["calibration"]
            keys(cal, {"rpm", "direction", "flow_ml_min", "measured_at", "notes",
                       "basis", "source_file"},
                 {"rpm", "direction", "flow_ml_min"}, f"{name}.calibration")
            number(cal["rpm"], f"{name}.calibration.rpm", positive=True, integer=True,
                   maximum=dev["max_rpm"])
            require(cal["direction"] in ("CW", "CCW"), f"{name}: 标定方向错误")
            if cal["flow_ml_min"] is not None:
                number(cal["flow_ml_min"], f"{name}.calibration.flow_ml_min", positive=True)
        if kind == "valve":
            number(dev["channels"], f"{name}.channels", maximum=65535, positive=True, integer=True)
            require(isinstance(dev["routes"], dict) and dev["routes"], f"{name}: routes 不能为空")
            for route, channel in dev["routes"].items():
                number(channel, f"{name}.routes.{route}", minimum=1,
                       maximum=dev["channels"], integer=True)
    return devices


def compile_plan(devices_document, sequence):
    devices = validate_devices(devices_document)
    keys(sequence, {"schema_version", "id", "description", "steps", "notes"},
         {"schema_version", "id", "description", "steps"}, "sequence")
    require(type(sequence["schema_version"]) is int and sequence["schema_version"] == 1,
            "不支持的动作序列版本")
    require(isinstance(sequence["id"], str) and sequence["id"], "sequence.id 不能为空")
    require(isinstance(sequence["description"], str), "sequence.description 需要文字说明")
    require(isinstance(sequence["steps"], list) and sequence["steps"], "steps 不能为空")
    unresolved, compiled, seen = [], [], set()
    totals = {"dose_ml_by_device": {}, "syringe_ml_by_device": {}, "drain_nominal_ml": 0,
              "drain_equivalent_ml": 0}
    current_routes = {}
    for index, step in enumerate(sequence["steps"], 1):
        keys(step, {"id", "device", "action", "description", "volume_ml",
                    "extra_time_fraction", "route", "requires_route", "seconds",
                    "operation", "source", "destination", "fluid"},
             {"id", "device", "action", "description"}, f"step {index}")
        require(isinstance(step["id"], str) and step["id"] and step["id"] not in seen,
                f"step {index}: id 为空或重复")
        seen.add(step["id"])
        require(isinstance(step["description"], str), f"step {index}: description 需要文字")
        require(isinstance(step["device"], str) and step["device"] in devices,
                f"step {index}: 未知设备")
        dev = devices[step["device"]]
        action = step["action"]
        require(isinstance(action, str) and
                action in ("dose", "drain", "switch_valve", "syringe_transfer", "wait"),
                f"step {index}: 不支持的动作 {action}")
        if "operation" in step:
            require(step["operation"] == OPERATION_NAMES[action],
                    f"step {index}: 注入/抽出标识与设备动作不一致")
        for field in ("source", "destination"):
            if field in step:
                require(isinstance(step[field], str) and step[field],
                        f"step {index}: {field} 需要流向说明")
        if "fluid" in step:
            require(isinstance(step["fluid"], str) and step["fluid"] and step["fluid"].isascii(),
                    f"step {index}: fluid must be nonempty English text")
        appropriate = {
            "dose": {"volume_ml"}, "drain": {"volume_ml", "extra_time_fraction", "requires_route"},
            "switch_valve": {"route"}, "syringe_transfer": {"volume_ml"}, "wait": {"seconds"},
        }[action]
        extra = set(step) - {"id", "device", "action", "description",
                            "operation", "source", "destination", "fluid"} - appropriate
        require(not extra, f"step {index}: 动作 {action} 不支持字段 {extra}")
        require(appropriate <= set(step), f"step {index}: 动作参数不完整")
        item = dict(step, index=index, device_label=dev["label"], port=dev["port"])
        if action == "switch_valve":
            require(dev["kind"] == "valve", f"step {index}: 需要切换阀")
            require(isinstance(step["route"], str) and step["route"] in dev["routes"],
                    f"step {index}: 未定义的流路")
            item["channel"] = dev["routes"][step["route"]]
            current_routes[step["device"]] = step["route"]
        elif action in ("dose", "drain"):
            require(dev["kind"] == "peristaltic", f"step {index}: 需要蠕动泵")
            number(step["volume_ml"], f"step {index}.volume_ml", positive=True)
            factor = 1
            if action == "drain":
                number(step["extra_time_fraction"], f"step {index}.extra_time_fraction",
                       maximum=1)
                factor += step["extra_time_fraction"]
                route = step["requires_route"]
                keys(route, {"device", "route"}, {"device", "route"}, f"step {index}.requires_route")
                require(isinstance(route["device"], str) and isinstance(route["route"], str),
                        f"step {index}: 流路设备与名称需要文字")
                valve = devices.get(route["device"])
                require(valve is not None and valve["kind"] == "valve", f"step {index}: 抽液流路需要阀")
                require(route["route"] in valve["routes"], f"step {index}: 抽液流路未定义")
                require(current_routes.get(route["device"]) == route["route"],
                        f"step {index}: 抽液前必须先显式切换到 {route['route']}")
                item["required_channel"] = valve["routes"][route["route"]]
                totals["drain_nominal_ml"] += step["volume_ml"]
                totals["drain_equivalent_ml"] += step["volume_ml"] * factor
            else:
                amounts = totals["dose_ml_by_device"]
                amounts[step["device"]] = amounts.get(step["device"], 0) + step["volume_ml"]
            item.update(rpm=dev["rpm"], direction=dev["direction"],
                        timing_equivalent_ml=step["volume_ml"] * factor, duration_s=None,
                        flow_ml_min=dev["calibration"]["flow_ml_min"],
                        calibration_basis=dev["calibration"].get("basis", "unspecified"))
            cal = dev["calibration"]
            if cal["flow_ml_min"] is None:
                unresolved.append(f"{step['device']}: 缺少当前管路、液体、转速及方向的实测流量 flow_ml_min")
            elif cal["rpm"] != dev["rpm"] or cal["direction"] != dev["direction"]:
                unresolved.append(f"{step['device']}: 标定转速/方向与运行设置不一致")
            else:
                duration = item["timing_equivalent_ml"] * 60 / cal["flow_ml_min"]
                require(duration <= dev["max_run_seconds"],
                        f"step {index}: 计划时间 {duration:.3f}s 超过设备上限")
                item["duration_s"] = duration
        elif action == "syringe_transfer":
            require(dev["kind"] == "syringe", f"step {index}: 需要注射泵")
            number(step["volume_ml"], f"step {index}.volume_ml", positive=True,
                   maximum=dev["capacity_ml"])
            delta = round(step["volume_ml"] / dev["capacity_ml"] * dev["stroke_steps"])
            require(delta >= 1, f"step {index}: 请求体积小于注射泵一步")
            item.update(delta_steps=delta, speed_steps_s=dev["speed_steps_s"],
                        achievable_volume_ml=delta * dev["capacity_ml"] / dev["stroke_steps"],
                        estimated_movement_s=2 * delta / dev["speed_steps_s"],
                        phases=["aspirate via inlet I", "dispense via outlet O"])
            amounts = totals["syringe_ml_by_device"]
            amounts[step["device"]] = amounts.get(step["device"], 0) + step["volume_ml"]
        else:
            number(step["seconds"], f"step {index}.seconds", positive=True)
        compiled.append(item)
    return {"sequence_id": sequence["id"], "description": sequence["description"],
            "steps": compiled, "totals": totals,
            "unresolved": list(dict.fromkeys(unresolved)),
            "ready_for_hardware": not unresolved}
