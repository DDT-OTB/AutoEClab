"""A mandatory typed workflow block, independent of its display name."""
from copy import deepcopy
import json
from pathlib import Path


def default_preflight(route='waste_1'):
    value = json.loads((Path(__file__).resolve().parents[2] / 'config/preflight_block.json').read_text())
    block = {key: value[key] for key in ('type', 'id', 'description', 'steps')}
    block['steps'][1]['route'] = route
    return block


def is_preflight(block):
    return isinstance(block, dict) and block.get('type') == 'preflight'


def validate_preflight(block, policy=None):
    from .cell_safety import load_policy
    allowed = policy['cell']['allowed_waste_routes'] if policy is not None else ('waste_1', 'waste_2', 'waste')
    steps = block.get('steps')
    if not isinstance(steps, list) or len(steps) != 3:
        raise ValueError('前置准备功能块必须包含回零、排空、人工空池确认三个固定步骤。')
    route = steps[1].get('route') if isinstance(steps[1], dict) else None
    if route not in allowed:
        raise ValueError('前置准备功能块须选择有效的废液流路。')
    expected = [{'device': 'syringe_c', 'operation': 'home_if_used'},
                {'device': 'cell', 'operation': 'drain_to_waste', 'route': route},
                {'device': 'cell', 'operation': 'confirm_empty'}]
    if steps != expected:
        raise ValueError('前置步骤或顺序无效：必须先按需回零，再排空，最后现场确认空池。')
    return block


def require_preflight(sequence, policy=None):
    from .workflow import flatten_sequence, validate_block
    from .cell_safety import load_policy
    policy = policy or load_policy()
    if not isinstance(sequence, dict):
        raise ValueError('实验方案必须是 JSON 对象。')
    if sequence.get('schema_version') == 2:
        block = sequence.get('preflight')
        if not is_preflight(block):
            raise ValueError('实验缺少前置准备功能块，请在方案编辑器中补全后保存。')
    else:
        blocks = sequence.get('blocks', [])
        required = [block for block in blocks if is_preflight(block)] if isinstance(blocks, list) else []
        if len(required) != 1 or not blocks or not is_preflight(blocks[0]):
            raise ValueError('实验必须且只能以一个前置准备功能块开始。')
        block = required[0]
    validate_block(block)
    validate_preflight(block, policy)
    flat = flatten_sequence(sequence)
    if not flat['steps']:
        raise ValueError('请在前置准备之后添加至少一个实验动作。')
    return {'block_id': block['id'], 'type': 'preflight', 'steps': deepcopy(block['steps']),
            'requires_syringe': any(step.get('device') == 'syringe_c' for step in flat['steps']),
            'route': block['steps'][1]['route'], 'predrain_volume_ml': policy['cell']['predrain_volume_ml'],
            'operator_confirmation_required': True, 'drain_mode': policy['cell'].get('drain_mode', 'selector')}
