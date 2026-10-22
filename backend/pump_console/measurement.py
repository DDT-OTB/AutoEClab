"""Validate CV drafts and weave them into legacy pump plans without executing them."""
from copy import deepcopy
import math
from pathlib import Path
import re
import json
from .workflow import flatten_sequence

MEASUREMENT_DEVICE = "electrochemical_station"
CV_FIELDS = {"initial_potential_v", "upper_potential_v", "lower_potential_v", "final_potential_v",
             "initial_scan_direction", "scan_rate_v_s", "potential_step_v", "cycles", "quiet_time_s",
             "current_range_a", "reference_electrode"}


def measurement_templates():
    path = Path(__file__).resolve().parents[2] / "config/action_templates/cyclic_voltammetry.json"
    template = json.loads(path.read_text())
    if template["device_id"] != MEASUREMENT_DEVICE or template["device"]["execution_supported"] is not False:
        raise ValueError("The workstation template must remain planning-only")
    validate_cv(template["action"]["default_parameters"])
    return {MEASUREMENT_DEVICE: template}


def is_measurement(step):
    return isinstance(step, dict) and (step.get("device") == MEASUREMENT_DEVICE or step.get("operation") == "measure")


def finite(value, label, positive=False, nonnegative=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(label + "必须是有限数值。")
    if positive and value <= 0 or nonnegative and value < 0:
        raise ValueError(label + ("必须大于 0。" if positive else "不能小于 0。"))
    return value


def validate_cv(parameters):
    if not isinstance(parameters, dict) or set(parameters) != CV_FIELDS:
        raise ValueError("CV 参数字段缺失或包含未支持的字段。")
    p = parameters
    for key in ("initial_potential_v", "upper_potential_v", "lower_potential_v", "final_potential_v"):
        finite(p[key], key)
    if not p["lower_potential_v"] < p["upper_potential_v"]:
        raise ValueError("下限电位必须小于上限电位。")
    for key in ("initial_potential_v", "final_potential_v"):
        if not p["lower_potential_v"] <= p[key] <= p["upper_potential_v"]:
            raise ValueError("起始和终止电位必须位于电位上下限之间。")
    if p["initial_scan_direction"] not in ("positive", "negative"):
        raise ValueError("起始扫描方向必须为 positive 或 negative。")
    finite(p["scan_rate_v_s"], "扫描速率", positive=True)
    finite(p["potential_step_v"], "采样电位间隔", positive=True)
    if p["potential_step_v"] > p["upper_potential_v"]-p["lower_potential_v"]:
        raise ValueError("采样电位间隔不能大于整个电位窗口。")
    if type(p["cycles"]) is not int or not 1 <= p["cycles"] <= 10000:
        raise ValueError("扫描圈数必须是 1 至 10000 的整数。")
    finite(p["quiet_time_s"], "静置时间", nonnegative=True)
    if p["current_range_a"] is not None:
        finite(p["current_range_a"], "固定电流量程", positive=True)
    if not isinstance(p["reference_electrode"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 /()+.,_-]{0,79}", p["reference_electrode"]):
        raise ValueError("参比电极请使用英文标识，例如 RHE 或 Ag/AgCl。")
    first, second = ((p["upper_potential_v"],p["lower_potential_v"]) if p["initial_scan_direction"]=="positive"
                     else (p["lower_potential_v"],p["upper_potential_v"]))
    distance = abs(first-p["initial_potential_v"]) + (2*p["cycles"]-1)*abs(second-first) + abs(p["final_potential_v"]-second)
    duration = p["quiet_time_s"] + distance/p["scan_rate_v_s"]
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("参数产生的预计测量时长无效。")
    return duration


def validate_measurement(step):
    if set(step) - {"device","operation","technique","parameters","id","description"}:
        raise ValueError("测量动作包含不支持的字段。")
    if step.get("device") != MEASUREMENT_DEVICE or step.get("operation") != "measure":
        raise ValueError("测量动作须使用 electrochemical_station 和 measure。")
    if step.get("technique") != "cyclic_voltammetry":
        raise ValueError("当前测量编排仅支持 cyclic_voltammetry。")
    return validate_cv(step.get("parameters"))


def compile_workflow(sequence, devices, templates, compiler, config):
    sequence = flatten_sequence(sequence)
    config.keys(sequence, {"schema_version","id","description","steps","notes"},
                {"schema_version","id","description","steps"}, "总流程")
    steps = sequence["steps"]
    if not isinstance(sequence["description"],str) or not isinstance(steps,list) or not steps:
        raise ValueError("流程描述须为文字，steps 至少包含一个动作。")
    if not any(is_measurement(step) for step in steps):
        return config.compile_plan(devices,compiler.expand_sequence(sequence,templates))
    pump_steps, measurement_steps, ids = [], {}, set()
    for index, step in enumerate(steps,1):
        if not isinstance(step,dict):
            raise ValueError("每个动作必须是 JSON 对象。")
        name = step.get("id",f"step_{index:02d}")
        if not isinstance(name,str) or not name or name in ids:
            raise ValueError("动作 ID 不能为空或重复。")
        ids.add(name)
        if is_measurement(step):
            duration = validate_measurement(step)
            description = step.get("description","Cyclic voltammetry measurement draft")
            if not isinstance(description,str):
                raise ValueError("测量动作 description 须为文字。")
            measurement_steps[index] = {
                "id":name,"index":index,"device":MEASUREMENT_DEVICE,"device_label":"Electrochemical workstation",
                "action":"measurement","operation":"measure","technique":"cyclic_voltammetry",
                "description":description,"parameters":deepcopy(step["parameters"]),
                "estimated_measurement_s":duration,"execution_supported":False,
                "integration_status":"not_integrated",
            }
        else:
            pump_steps.append({**step,"id":name})
    if pump_steps:
        expanded = compiler.expand_sequence({**sequence,"steps":pump_steps},templates)
        plan = config.compile_plan(devices,expanded)
    else:
        plan = {"sequence_id":sequence["id"],"description":sequence["description"],"steps":[],
                "totals":{"dose_ml_by_device":{},"syringe_ml_by_device":{},"drain_nominal_ml":0,"drain_equivalent_ml":0},
                "unresolved":[],"ready_for_hardware":False}
    compiled, pump_index = [], 0
    for index in range(1,len(steps)+1):
        if index in measurement_steps:
            compiled.append(measurement_steps[index])
        else:
            compiled.append({**plan["steps"][pump_index],"index":index})
            pump_index += 1
    plan["steps"] = compiled
    plan["ready_for_hardware"] = False
    plan["unresolved"].append("electrochemical_station: workstation integration is not implemented; measurement steps cannot execute")
    plan["measurement_step_count"] = len(measurement_steps)
    return plan
