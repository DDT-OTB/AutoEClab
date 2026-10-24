"""Confirmed fluid paths; keep inlet selection separate from direct waste drainage."""
from copy import deepcopy

LEGACY = 'legacy_waste_selector'
INLET = 'syringe_inlet_selector'
SOURCE_CHANNELS = {'water': 5, 'electrolyte': 6}


def topology_mode(value=None):
    if value is None:
        return LEGACY
    if value not in (LEGACY, INLET):
        raise ValueError('Unsupported fluidic topology')
    return value


def view_templates(templates, mode):
    result = deepcopy(templates)
    from .syringe_batch import stroke_limit,load_policy
    policy=load_policy()
    for template in result.values():
        if template['device']['kind']=='syringe':
            template['action'].update(max_stroke_volume_ml=stroke_limit(template['device'],policy)[0],
                                      max_strokes_per_action=policy['max_strokes_per_action'])
    if topology_mode(mode) == INLET:
        result['valve']['device']['routes'] = dict(SOURCE_CHANNELS)
        result['valve']['action']['role'] = 'syringe_inlet_selector'
        result['syringe_c']['action'].update(allowed_fluids=list(SOURCE_CHANNELS), source_valve='valve')
        result['peristaltic_2']['action'].update(routing_mode='direct', destination='waste_bottle')
        result['peristaltic_2']['action'].pop('valve_device', None)
    return result


def migrate_steps(steps, default_fluid='electrolyte'):
    """Remove obsolete waste selection and insert explicit syringe-source selection."""
    result, route = [], None
    reserved = {step['id'] for step in steps if 'id' in step}
    def unique(stem):
        value, n = stem, 2
        while value in reserved:
            value = stem + '_' + str(n); n += 1
        reserved.add(value)
        return value
    for index, original in enumerate(steps, 1):
        step = deepcopy(original)
        if step.get('device') == 'valve' and step.get('operation') == 'switch_route':
            if step.get('route') in ('waste_1', 'waste_2'):
                continue
            if step.get('route') not in SOURCE_CHANNELS:
                raise ValueError('Unknown valve source route; migration requires review')
            route = step['route']
        if step.get('device') == 'syringe_c' and step.get('operation') != 'initialize':
            fluid = step.get('fluid', default_fluid)
            if fluid not in SOURCE_CHANNELS:
                raise ValueError('Unsupported syringe liquid source')
            if route != fluid:
                result.append(dict(id=unique('select_' + fluid + '_' + str(index)), device='valve',
                                   operation='switch_route', route=fluid,
                                   description='Select ' + fluid + ' before syringe aspiration'))
                route = fluid
            step['fluid'] = fluid
        if 'id' not in step:
            step['id'] = unique('action_' + str(index))
        result.append(step)
    return result


def migrate_block(block):
    from .preflight import upgrade_preflight_block
    value = upgrade_preflight_block(block)
    if value.get('type') == 'preflight':
        next(step for step in value['steps'] if step['operation']=='drain_to_waste')['route'] = 'waste'
    else:
        value['steps'] = migrate_steps(value['steps'])
        if value.get('id') in ('drain_waste_1', 'drain_waste_2'):
            value['description'] = 'Drain liquid directly into the waste bottle'
    return value


def migrate_sequence(sequence):
    value = deepcopy(sequence)
    from .preflight import default_preflight
    if value.get('schema_version') == 3:
        value['blocks'] = [item for block in value['blocks'] if (item := migrate_block(block)).get('steps')]
        if not any(block.get('type') == 'preflight' for block in value['blocks']):
            value['blocks'].insert(0, default_preflight('waste'))
    elif value.get('schema_version') == 2:
        value['steps'] = migrate_steps(value['steps'])
        value['preflight'] = migrate_block(value.get('preflight') or default_preflight('waste'))
    else:
        raise ValueError('Unsupported sequence format for topology migration')
    note = 'Valve selects syringe inlet: channel 5 water, channel 6 electrolyte. Pump 2 drains directly to one waste bottle.'
    if note not in value.setdefault('notes', []):
        value['notes'].append(note)
    return value


def compile_inlet_workflow(sequence, devices, templates, compiler, config, station, debug=False):
    from .measurement import compile_workflow, is_measurement
    from .syringe_batch import compile_native_plan
    doc, source = deepcopy(devices), deepcopy(templates)
    doc['devices']['valve']['routes'] = dict(SOURCE_CHANNELS)
    source['valve']['device']['routes'] = dict(SOURCE_CHANNELS)
    compiled, unresolved, ids = [], [], set()
    totals = dict(dose_ml_by_device={}, syringe_ml_by_device={}, drain_nominal_ml=0, drain_equivalent_ml=0)
    selected = None
    measurement_count = 0
    for index, original in enumerate(sequence['steps'], 1):
        step = deepcopy(original)
        name = step.get('id', f'step_{index:02d}')
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError('Action IDs must be nonempty and unique')
        ids.add(name); step['id'] = name
        local = deepcopy(source)
        direct = False
        if step.get('device') == 'syringe_c' and step.get('operation') == 'initialize':
            if not debug or set(step) - {'device','operation','id','description'}:
                raise ValueError('机械回零动作仅允许在显式调试流程中使用。')
            from .execution import initialization_plan
            item = initialization_plan(doc)['steps'][0]
            item.update(id=name, index=index, description=step.get('description','Explicit syringe homing debug action'))
            compiled.append(item)
            continue
        if step.get('device') == 'valve':
            if step.get('route') not in SOURCE_CHANNELS:
                raise ValueError('六通阀现在用于注射泵入口选液，仅支持 water（5）或 electrolyte（6）；旧废液切阀动作需更新。')
            selected = step['route']
        elif step.get('device') == 'syringe_c':
            fluid = step.get('fluid', local['syringe_c']['action']['fluid'])
            if debug and selected is None and fluid in SOURCE_CHANNELS:
                selected = fluid  # Debug may use the current source, but execution must verify its actual channel.
            if fluid not in SOURCE_CHANNELS or selected != fluid:
                raise ValueError('注射泵吸液前必须显式选择与液体一致的入口：water=5，electrolyte=6。')
            local['syringe_c']['action'].update(fluid=fluid, source=fluid + '_reservoir')
            step['fluid'] = fluid
        elif step.get('device') == 'peristaltic_2':
            if step.get('operation') != 'withdraw' or step.get('fluid', 'waste_liquid') != 'waste_liquid':
                raise ValueError('排液泵仅用于从电解池直接抽出废液。')
            fraction = local['peristaltic_2']['action']['extra_time_fraction']
            nominal = config.number(step.get('volume_ml'), 'volume_ml', positive=True)
            local['peristaltic_2']['action'] = dict(type='dose', operation='inject', source='electrolytic_cell',
                                                 destination='waste_bottle', fluid='waste_liquid')
            step.update(operation='inject', volume_ml=nominal * (1 + fraction))
            direct = True
        singleton = {key: deepcopy(value) for key, value in sequence.items() if key != 'steps'}
        singleton['steps'] = [step]
        try:
            if is_measurement(step):
                plan = compile_workflow(singleton, doc, local, compiler, config, station)
                measurement_count += 1
            else:
                plan = compile_native_plan(doc, compiler.expand_sequence(singleton, local),config)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f'第 {index} 步：{exc}') from exc
        item = plan['steps'][0]
        if direct:
            item.update(action='drain_direct', operation='withdraw', volume_ml=nominal,
                        extra_time_fraction=fraction, destination='waste_bottle', fluid='waste_liquid')
            item['description'] = original.get('description', f"Withdraw {nominal:g} mL directly into the waste bottle")
            plan['totals']['dose_ml_by_device'].pop('peristaltic_2', None)
            plan['totals']['drain_nominal_ml'] = nominal
            plan['totals']['drain_equivalent_ml'] = nominal * (1 + fraction)
        if item['action'] == 'syringe_transfer':
            item['requires_source'] = dict(device='valve', route=selected, channel=SOURCE_CHANNELS[selected])
        item['index'] = index
        compiled.append(item); unresolved.extend(plan['unresolved'])
        for field in ('dose_ml_by_device', 'syringe_ml_by_device'):
            for name, amount in plan['totals'].get(field, {}).items():
                totals[field][name] = totals[field].get(name, 0) + amount
        for field in ('drain_nominal_ml', 'drain_equivalent_ml'):
            totals[field] += plan['totals'].get(field, 0)
    return dict(sequence_id=sequence['id'], description=sequence['description'], steps=compiled,
                totals=totals, unresolved=list(dict.fromkeys(unresolved)), ready_for_hardware=not unresolved,
                measurement_step_count=measurement_count, fluidic_topology=INLET)
