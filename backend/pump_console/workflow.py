"""Two-level workflows expand to the existing controller format in one place."""
from copy import deepcopy
import json
from pathlib import Path
import re

MAX_BLOCKS = 128
MAX_ACTIONS = 1024
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}")


def _fields(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise ValueError(label + "字段缺失或包含未支持的字段。")


def validate_block(block, allow_empty=False):
    _fields(block, {"id", "description", "steps", "type"}, {"id", "description", "steps"}, "功能块")
    if not isinstance(block['id'], str) or not IDENTIFIER.fullmatch(block['id']):
        raise ValueError("功能块 ID 须为字母开头的英文标识，最多 80 个字符。")
    if not isinstance(block['description'], str):
        raise ValueError("功能块 description 须为文字。")
    if 'type' in block:
        if block['type'] != 'preflight':
            raise ValueError('不支持的功能块类型。')
        from .preflight import validate_preflight
        return validate_preflight(block)
    steps = block['steps']
    if not isinstance(steps, list) or len(steps) > MAX_ACTIONS or (not steps and not allow_empty):
        raise ValueError("每个功能块须包含 1 至 1024 个动作。")
    ids = set()
    for step in steps:
        if not isinstance(step, dict) or 'blocks' in step or 'steps' in step:
            raise ValueError("功能块内部只能包含动作，不支持嵌套功能块。")
        if not isinstance(step.get('device'), str) or not isinstance(step.get('operation'), str):
            raise ValueError("每个动作须包含 device 和 operation。")
        if 'id' in step:
            name = step['id']
            if not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or name in ids:
                raise ValueError("同一功能块内的动作 ID 须为有效且不重复的英文标识。")
            ids.add(name)
    return block


def import_block(document):
    _fields(document, {"schema_version", "kind", "id", "description", "steps", "type"},
            {"schema_version", "kind", "id", "description", "steps"}, "功能块文件")
    if type(document['schema_version']) is not int or document['schema_version'] != 1 or document['kind'] != 'function_block':
        raise ValueError("功能块文件需要 schema_version=1 和 kind=function_block。")
    if re.search(r"[\u3400-\u9fff]", json.dumps(document, ensure_ascii=False)):
        raise ValueError("功能块 JSON 请使用英文。")
    return deepcopy(validate_block({key: document[key] for key in ('id', 'description', 'steps', 'type') if key in document}))


def function_block_templates():
    directory = Path(__file__).resolve().parents[2] / 'config/function_blocks'
    result = []
    for path in sorted(directory.glob('*.json')):
        document = json.loads(path.read_text())
        block = import_block(document)
        if block['id'] != path.stem:
            raise ValueError('Function block filename must match its ID')
        result.append(document)
    return result


def flatten_sequence(sequence, allow_empty=False):
    if not isinstance(sequence, dict):
        raise ValueError("实验方案须为 JSON 对象。")
    schema = sequence.get('schema_version')
    if type(schema) is not int or schema not in (2, 3):
        raise ValueError("实验方案需要 schema_version=2 或 3。")
    if schema == 2:
        return {key: deepcopy(value) for key, value in sequence.items() if key != 'preflight'}
    _fields(sequence, {"schema_version", "id", "description", "notes", "blocks"},
            {"schema_version", "id", "description", "blocks"}, "实验方案")
    if not isinstance(sequence['description'], str):
        raise ValueError("实验 description 须为文字。")
    if 'notes' in sequence and (not isinstance(sequence['notes'], list) or any(not isinstance(note, str) for note in sequence['notes'])):
        raise ValueError("实验 notes 须为文字数组。")
    blocks = sequence['blocks']
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS or (not blocks and not allow_empty):
        raise ValueError("实验须包含 1 至 128 个功能块。")
    steps, ids = [], set()
    for block_index, block in enumerate(blocks, 1):
        validate_block(block, allow_empty=allow_empty)
        if block['id'] in ids:
            raise ValueError("同一实验中的功能块 ID 不能重复。")
        ids.add(block['id'])
        if block.get('type') == 'preflight':
            continue
        for action_index, action in enumerate(block['steps'], 1):
            # IDs belong to the flattened execution snapshot, independently of reusable block IDs.
            steps.append({**deepcopy(action), 'id': f'block_{block_index:03d}_step_{action_index:03d}'})
        if len(steps) > MAX_ACTIONS:
            raise ValueError("一个实验最多包含 1024 个动作。")
    return {**{key: deepcopy(value) for key, value in sequence.items() if key != 'blocks'},
            'schema_version': 2, 'steps': steps}


def block_ranges(sequence):
    offset, result = 0, []
    for block in sequence.get('blocks', []):
        if block.get('type') == 'preflight':
            result.append({'id': block['id'], 'description': block['description'], 'type': 'preflight',
                           'step_count': 3, 'first_step': None, 'last_step': None})
            continue
        count = len(block['steps'])
        result.append({'id': block['id'], 'description': block['description'], 'step_count': count,
                       'first_step': offset + 1, 'last_step': offset + count})
        offset += count
    return result
