"""Nominal cell-volume accounting and mandatory startup drain policy."""
from decimal import Decimal
import json
import math
from pathlib import Path
from .workflow import flatten_sequence, sequence_mode, preparation_required


class CellSafetyError(ValueError):
    pass


def load_policy(topology=None):
    value = json.loads((Path(__file__).resolve().parents[2] / 'config/safety_policy.json').read_text())
    cell = value['cell']
    if (value.get('schema_version') != 1 or type(cell['capacity_ml']) not in (int, float) or
            not math.isfinite(cell['capacity_ml']) or cell['capacity_ml'] <= 0 or
            type(cell['predrain_volume_ml']) not in (int, float) or not math.isfinite(cell['predrain_volume_ml']) or
            cell['predrain_volume_ml'] < cell['capacity_ml'] or cell['require_operator_empty_confirmation'] is not True or
            cell['initial_state'] != 'operator_confirmed_empty' or
            not cell['allowed_waste_routes'] or any(route not in ('waste_1', 'waste_2') for route in cell['allowed_waste_routes'])):
        raise ValueError('Invalid cell safety policy')
    from .topology import topology_mode, INLET
    if topology_mode(topology) == INLET:
        cell['allowed_waste_routes'] = ['waste']
        cell['drain_mode'] = 'direct'
    return value


def analyze_volumes(sequence, policy, plan=None):
    debug = sequence_mode(sequence) == 'debug'
    required = preparation_required(sequence)
    initial = sequence.get('initial_cell_volume_ml', 0) if debug and not required else 0
    sequence = flatten_sequence(sequence)
    limit = Decimal(str(policy['cell']['capacity_ml']))
    current = peak = Decimal(str(initial))
    rows, violations = [], []
    if current > limit:
        violations.append('声明的调试初始液量超过电解池容量。')
    for index, step in enumerate(sequence['steps'], 1):
        before = current
        operation = step.get('operation')
        if operation in ('inject', 'withdraw'):
            volume = step.get('volume_ml')
            if type(volume) not in (int, float) or not math.isfinite(volume) or volume <= 0:
                raise CellSafetyError(f'第 {index} 步体积必须为正的有限数值。')
            amount = Decimal(str(volume))
            if operation == 'inject' and plan is not None:
                achieved = plan['steps'][index-1].get('achievable_volume_ml')
                if type(achieved) in (int, float) and math.isfinite(achieved):
                    amount = max(amount, Decimal(str(achieved)))
            current = current + amount if operation == 'inject' else max(Decimal(0), current - amount)
        peak = max(peak, current)
        over = current > limit
        if over:
            violations.append(f'第 {index} 步后预计池内液量 {float(current):g} mL，超过 {float(limit):g} mL 容量上限。')
        rows.append({'index': index, 'before_ml': float(before), 'after_ml': float(current), 'over_capacity': over})
    return {'capacity_ml': float(limit), 'initial_volume_ml': float(initial), 'initial_state_requires_preflight': required,
            'initial_volume_source': 'operator_declared_debug' if debug and not required else 'operator_confirmed_empty',
            'peak_volume_ml': float(peak), 'final_volume_ml': float(current), 'steps': rows,
            'valid': not violations, 'errors': violations, 'volume_measured': False}


def require_safe_volumes(sequence, policy, plan=None):
    result = analyze_volumes(sequence, policy, plan)
    if not result['valid']:
        raise CellSafetyError(result['errors'][0])
    return result


def drain_sequence(policy, route):
    cell = policy['cell']
    if route not in cell['allowed_waste_routes']:
        raise CellSafetyError('请显式选择允许的废液流路。')
    if cell.get('drain_mode') == 'direct':
        return {'schema_version': 2, 'id': 'cell_startup_drain', 'description': 'Mandatory direct cell startup drain',
                'steps': [{'device': cell['drain_device'], 'operation': 'withdraw',
                           'volume_ml': cell['predrain_volume_ml'], 'fluid': 'waste_liquid'}]}
    return {'schema_version': 2, 'id': 'cell_startup_drain', 'description': 'Mandatory cell startup drain',
            'steps': [{'device': cell['valve_device'], 'operation': 'switch_route', 'route': route},
                      {'device': cell['drain_device'], 'operation': 'withdraw', 'volume_ml': cell['predrain_volume_ml'], 'fluid': 'waste_liquid'}]}
