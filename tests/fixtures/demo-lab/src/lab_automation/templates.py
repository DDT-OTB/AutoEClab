"""Expand short workflow steps from device-specific JSON action templates."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from .config import OPERATION_NAMES, compile_plan, keys, load_json, number, require, validate_devices


def load_templates(directory):
    directory = Path(directory)
    require(directory.is_dir(), f"模板目录不存在：{directory}")
    templates, devices = {}, {}
    for path in sorted(directory.glob("*.json")):
        require(path.resolve().parent == directory.resolve(), f"模板不能指向目录外：{path}")
        template = load_json(path)
        keys(template, {"schema_version", "device_id", "description", "device", "action"},
             {"schema_version", "device_id", "description", "device", "action"}, str(path))
        require(type(template["schema_version"]) is int and template["schema_version"] == 1,
                f"{path}: 不支持的模板版本")
        name = template["device_id"]
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name),
                f"{path}: device_id 格式错误")
        require(name == path.stem, f"{path}: 文件名必须与 device_id 一致")
        require(name not in templates, f"设备模板重复：{name}")
        require(isinstance(template["description"], str), f"{path}: description 需要文字")
        require(isinstance(template["device"], dict), f"{path}: device 需要对象")
        action = template["action"]
        require(isinstance(action, dict), f"{path}: action 需要对象")
        kind = action.get("type")
        require(isinstance(kind, str) and kind in
                ("dose", "drain", "syringe_transfer", "switch_valve"),
                f"{path}: 不支持的模板动作 {kind}")
        fields = {"type", "operation"}
        if kind != "switch_valve":
            fields |= {"source", "destination", "fluid"}
        if kind == "drain":
            fields |= {"extra_time_fraction", "valve_device"}
        keys(action, fields, fields, f"{name}.action")
        require(action["operation"] == OPERATION_NAMES[kind],
                f"{name}: operation 必须是 {OPERATION_NAMES[kind]}")
        for field in ("source", "destination"):
            if field in action:
                require(isinstance(action[field], str) and action[field],
                        f"{name}: {field} 需要流向说明")
        if kind != "switch_valve":
            require(isinstance(action["fluid"], str) and
                    re.fullmatch(r"[a-z][a-z0-9_]*", action["fluid"]),
                    f"{name}: fluid must be a lowercase English identifier")
        expected = {"dose": "peristaltic", "drain": "peristaltic",
                    "syringe_transfer": "syringe", "switch_valve": "valve"}[kind]
        require(template["device"].get("kind") == expected,
                f"{name}: 模板动作 {kind} 与设备类型不符")
        if kind == "drain":
            number(action["extra_time_fraction"], f"{name}.extra_time_fraction", maximum=1)
            require(isinstance(action["valve_device"], str) and action["valve_device"],
                    f"{name}: 抽液模板必须声明 valve_device")
        templates[name], devices[name] = template, deepcopy(template["device"])
    require(bool(templates), f"模板目录中没有 JSON 文件：{directory}")
    document = {"schema_version": 1, "devices": devices}
    validate_devices(document)
    for name, template in templates.items():
        if template["action"]["type"] == "drain":
            valve = devices.get(template["action"]["valve_device"])
            require(valve is not None and valve["kind"] == "valve",
                    f"{name}: 抽液模板引用的切换阀不存在或类型错误")
    return document, templates


def expand_sequence(sequence, templates):
    keys(sequence, {"schema_version", "id", "description", "steps", "notes"},
         {"schema_version", "id", "description", "steps"}, "总流程")
    require(type(sequence["schema_version"]) is int and sequence["schema_version"] == 2,
            "简洁总流程需要 schema_version=2")
    require(isinstance(sequence["steps"], list) and sequence["steps"], "总流程 steps 不能为空")
    current_routes, steps = {}, []
    for index, step in enumerate(sequence["steps"], 1):
        require(isinstance(step, dict), f"步骤 {index}: 需要对象")
        device_id = step.get("device")
        require(isinstance(device_id, str) and device_id in templates,
                f"步骤 {index}: 找不到设备动作模板 {device_id}")
        template = templates[device_id]
        defaults = template["action"]
        action = defaults["type"]
        parameter = "route" if action == "switch_valve" else "volume_ml"
        allowed = {"device", "operation", parameter, "id", "description"}
        if action != "switch_valve":
            allowed.add("fluid")
        keys(step, allowed, {"device", "operation", parameter}, f"step {index}")
        require(step["operation"] == defaults["operation"],
                f"步骤 {index}: {device_id} 模板配置为{defaults['operation']}，与步骤 operation 不一致")
        expanded = {
            "id": step.get("id", f"step_{index:02d}"),
            "device": device_id,
            "action": action,
            "operation": defaults["operation"],
        }
        if action == "switch_valve":
            route = step["route"]
            require(isinstance(route, str) and route in template["device"]["routes"],
                    f"步骤 {index}: 阀流路未定义")
            expanded["route"] = route
            current_routes[device_id] = route
            description = f"{template['device']['label']} → {route}"
        else:
            volume = number(step["volume_ml"], f"步骤 {index}.volume_ml", positive=True)
            expanded["volume_ml"] = volume
            fluid = defaults["fluid"]
            require(step.get("fluid", fluid) == fluid,
                    f"step {index}: fluid does not match the {device_id} template ({fluid})")
            expanded["fluid"] = fluid
            expanded["source"] = defaults["source"]
            expanded["destination"] = defaults["destination"]
            description = f"{template['device']['label']}, {defaults['operation']} {volume:g} mL"
            if action == "drain":
                valve = defaults["valve_device"]
                require(valve in current_routes,
                        f"步骤 {index}: 抽液前必须先在总流程中选择阀 {valve} 的废液流路")
                expanded["extra_time_fraction"] = defaults["extra_time_fraction"]
                expanded["requires_route"] = {"device": valve, "route": current_routes[valve]}
                channel = templates[valve]["device"]["routes"][current_routes[valve]]
                expanded["destination"] += f" ({current_routes[valve]}, channel {channel})"
        expanded["description"] = step.get("description", description)
        steps.append(expanded)
    return {**deepcopy(sequence), "schema_version": 1, "steps": steps}


def load_configuration(sequence_path, templates_directory, devices_path=None):
    original = load_json(sequence_path)
    require(isinstance(original, dict), "动作序列必须为 JSON 对象")
    if original.get("schema_version") == 2:
        require(devices_path is None, "模板流程不读取 --devices；请修改 config/templates 中的设备模板")
        devices, templates = load_templates(templates_directory)
        expanded = expand_sequence(original, templates)
        plan = compile_plan(devices, expanded)
        plan["configuration_format"] = "device_templates_v2"
        plan["templates"] = templates
        plan["templates_directory"] = str(Path(templates_directory).resolve())
    else:
        require(devices_path is not None, "旧版序列需要通过 --devices 指定旧版设备文件")
        devices = load_json(devices_path)
        plan = compile_plan(devices, original)
        plan["configuration_format"] = "legacy_v1"
    return devices, original, plan
