"""Nominal cell-volume accounting and mandatory startup drain policy."""
from decimal import Decimal
import json
import math
from pathlib import Path


class CellSafetyError(ValueError):
    pass


def load_policy():
    value = json.loads((Path(__file__).resolve().parents[2] / 'config/safety_policy.json').read_text())
    cell = value['cell']
    if (value.get('schema_version') != 1 or type(cell['capacity_ml']) not in (int, float) or
            not math.isfinite(cell['capacity_ml']) or cell['capacity_ml'] <= 0 or
            type(cell['predrain_volume_ml']) not in (int, float) or not math.isfinite(cell['predrain_volume_ml']) or
            cell['predrain_volume_ml'] < cell['capacity_ml'] or cell['require_operator_empty_confirmation'] is not True or
            cell['initial_state'] != 'operator_confirmed_empty' or
            not cell['allowed_waste_routes'] or any(route not in ('waste_1', 'waste_2') for route in cell['allowed_waste_routes'])):
        raise ValueError('Invalid cell safety policy')
    return value


def analyze_volumes(sequence, policy, plan=None):
    limit = Decimal(str(policy['cell']['capacity_ml']))
    current, peak = Decimal(0), Decimal(0)
    rows, violations = [], []
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
    return {'capacity_ml': float(limit), 'initial_volume_ml': 0, 'initial_state_requires_preflight': True,
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
    return {'schema_version': 2, 'id': 'cell_startup_drain', 'description': 'Mandatory cell startup drain',
            'steps': [{'device': cell['valve_device'], 'operation': 'switch_route', 'route': route},
                      {'device': cell['drain_device'], 'operation': 'withdraw', 'volume_ml': cell['predrain_volume_ml'], 'fluid': 'waste_liquid'}]}
