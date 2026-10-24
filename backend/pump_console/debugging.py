"""Explicit debug workflows reuse the standard plan validators, not arbitrary commands."""
from copy import deepcopy


def is_homing(step):
    return step.get('device') == 'syringe_c' and step.get('operation') == 'initialize'


def compile_legacy_debug(sequence, devices, templates, compiler, config, station):
    from .execution import initialization_plan
    from .measurement import compile_workflow
    homing, regular, ids = {}, [], set()
    for index, source in enumerate(sequence['steps'], 1):
        step = deepcopy(source); step.setdefault('id', f'step_{index:02d}')
        if not isinstance(step['id'], str) or not step['id'] or step['id'] in ids:
            raise ValueError('Debug action IDs must be nonempty and unique')
        ids.add(step['id'])
        if is_homing(step):
            if set(step) - {'device','operation','id','description'}:
                raise ValueError('回零调试动作不接受体积或任意指令参数。')
            item = initialization_plan(devices)['steps'][0]
            item.update(id=step['id'],index=index,description=step.get('description','Explicit syringe homing debug action'))
            homing[index] = item
        else:
            regular.append(step)
    if regular:
        plan = compile_workflow({**sequence,'steps':regular},devices,templates,compiler,config,station)
    else:
        plan = dict(sequence_id=sequence['id'],description=sequence['description'],steps=[],
                    totals=dict(dose_ml_by_device={},syringe_ml_by_device={},drain_nominal_ml=0,drain_equivalent_ml=0),
                    unresolved=[],ready_for_hardware=True)
    position, steps = 0, []
    for index in range(1,len(sequence['steps'])+1):
        if index in homing:
            steps.append(homing[index])
        else:
            steps.append({**plan['steps'][position],'index':index});position+=1
    return {**plan,'steps':steps}
