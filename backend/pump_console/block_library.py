"""Private, versioned overrides for the reusable function-block library."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile

from .measurement import compile_workflow
from .repository import ApiError, parse_json
from .workflow import function_block_templates, import_block, IDENTIFIER


class BlockLibrary:
    def __init__(self, repository):
        self.repository = repository
        self.directory = repository.data_root / 'function_blocks'
        self.path = self.directory / 'library.json'

    def _check_path(self):
        if (self.directory.is_symlink() or self.path.is_symlink() or
                self.directory.resolve().parent != self.repository.data_root.resolve() or
                (self.path.exists() and not self.path.is_file())):
            raise ApiError(400, '功能块库路径不安全，未修改文件。', 'invalid_library_path')

    def _state(self):
        self._check_path()
        if not self.path.exists():
            return {'schema_version': 1, 'overrides': {}, 'hidden': []}
        try:
            state = parse_json(self.path.read_bytes())
            if (not isinstance(state, dict) or set(state) != {'schema_version', 'overrides', 'hidden'} or
                    type(state['schema_version']) is not int or state['schema_version'] != 1 or
                    not isinstance(state['overrides'], dict) or not isinstance(state['hidden'], list)):
                raise ValueError('Invalid library state')
            for name, document in state['overrides'].items():
                if import_block(document)['id'] != name:
                    raise ValueError('Invalid library entry')
            if (any(not isinstance(name, str) or not IDENTIFIER.fullmatch(name) for name in state['hidden']) or
                    len(set(state['hidden'])) != len(state['hidden']) or set(state['hidden']) & set(state['overrides'])):
                raise ValueError('Invalid hidden entries')
            return state
        except (ApiError, ValueError, TypeError, KeyError):
            raise ApiError(500, '功能块库文件需要检查，原文件已保留。', 'library_state_invalid') from None

    @staticmethod
    def _version(document):
        return hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def _entries(self, state):
        builtins = {block['id']: block for block in function_block_templates()}
        documents = {name: block for name, block in builtins.items() if name not in state['hidden']}
        documents.update(state['overrides'])
        return [{'id': name, 'block': deepcopy(block), 'version': self._version(block),
                 'source': 'custom' if name not in builtins else 'modified' if name in state['overrides'] else 'builtin',
                 'step_count': len(block['steps'])} for name, block in sorted(documents.items())]

    def list(self):
        with self.repository.lock:
            entries = self._entries(self._state())
            # Keep the original, unadorned block documents for older clients.
            return {'blocks': [entry['block'] for entry in entries], 'entries': entries}

    def get(self, name):
        with self.repository.lock:
            return self._find(self._state(), name)

    def _find(self, state, name):
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            raise ApiError(400, '功能块 ID 格式无效。', 'invalid_block_id')
        entry = next((entry for entry in self._entries(state) if entry['id'] == name), None)
        if entry is None:
            raise ApiError(404, '功能块模板不存在，请刷新模板库。', 'not_found')
        return entry

    @staticmethod
    def _require_version(entry, expected_version):
        if not isinstance(expected_version, str) or expected_version != entry['version']:
            raise ApiError(409, '模板已被修改，请刷新模板库并检查后重试。', 'version_conflict')

    def _validate(self, document):
        try:
            block = import_block(document)
            devices, templates = self.repository.compiler.load_templates(self.repository.templates_dir)
            # A reusable block may consume a route selected by a preceding block.
            # Supply routing context only to the validator; never store or execute it.
            valves = set()
            for step in block['steps']:
                template = templates.get(step['device'])
                if template and template['action']['type'] == 'drain':
                    valves.add(template['action']['valve_device'])
            context = [{'device': name, 'operation': 'switch_route', 'route': next(iter(templates[name]['device']['routes']))}
                       for name in sorted(valves)]
            blocks = ([{'id': 'validation_context', 'description': 'Validation routing context', 'steps': context}] if context else [])
            blocks.append({**block, 'id': 'validation_template'})
            compile_workflow({'schema_version': 3, 'id': 'block_template_validation',
                              'description': 'Validate reusable block actions without execution', 'blocks': blocks},
                             devices, templates, self.repository.compiler, self.repository.config)
        except (ValueError, KeyError, TypeError, StopIteration) as error:
            raise ApiError(422, str(error), 'invalid_block_template') from error
        return deepcopy(document)

    @staticmethod
    def _write(path, data):
        descriptor, temporary = tempfile.mkstemp(prefix='.block-library-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _commit(self, state, action, source, target):
        self._check_path()
        data = (json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        backups = self.directory / 'backups'
        if backups.is_symlink():
            raise ApiError(400, '功能块备份路径不安全，未修改模板。', 'invalid_library_path')
        backups.mkdir(exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        backup = backups / stamp
        backup.mkdir(mode=0o700)
        before = self.path.read_bytes() if self.path.exists() else None
        if before is not None:
            self._write(backup / 'library.json', before)
        self._write(backup / 'operation.json', json.dumps({'action': action, 'source': source, 'target': target,
                                                        'previously_existed': before is not None}).encode())
        self._write(self.path, data)
        return stamp

    def save(self, document, original_id=None, expected_version=None):
        document = self._validate(document)
        name = document['id']
        with self.repository.lock:
            state = self._state()
            entries = {entry['id']: entry for entry in self._entries(state)}
            if original_id is not None:
                try:
                    entry = self._find(state, original_id)
                except ApiError as error:
                    if error.status == 404:
                        raise ApiError(409, '原模板已被删除，请刷新模板库后另存。', 'version_conflict') from error
                    raise
                self._require_version(entry, expected_version)
            if name in entries and name != original_id:
                raise ApiError(409, '此模板 ID 已存在，请改名另存或选择已有模板进行更新。', 'name_conflict')
            if original_id is not None and original_id != name:
                state['overrides'].pop(original_id, None)
                state['hidden'] = sorted(set(state['hidden']) | {original_id})
            state['hidden'] = [hidden for hidden in state['hidden'] if hidden != name]
            state['overrides'][name] = document
            backup = self._commit(state, 'save' if original_id in (None, name) else 'rename', original_id, name)
            return {**self._find(state, name), 'saved': True, 'previous_id': original_id, 'backup_id': backup,
                    'library': self.list(), 'hardware_executed': False}

    def delete(self, name, expected_version):
        with self.repository.lock:
            state = self._state()
            self._require_version(self._find(state, name), expected_version)
            state['overrides'].pop(name, None)
            state['hidden'] = sorted(set(state['hidden']) | {name})
            backup = self._commit(state, 'delete', name, None)
            return {'deleted': True, 'id': name, 'backup_id': backup, 'library': self.list(), 'hardware_executed': False}
