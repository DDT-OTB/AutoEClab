"""Prepare immutable plans and supervise a single, explicitly started controller."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading

from .repository import ApiError, parse_json
from .runtime import active_controller, timestamp
from .cell_safety import drain_sequence, load_policy, require_safe_volumes
from .preflight import require_preflight
from .workflow import sequence_mode, preparation_required

ACTIVE_STATES = {"starting", "running", "stopping"}
TERMINAL_STATES = {"completed", "stopped", "failed"}
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{12}")


def initialization_plan(devices):
    device = devices['devices']['syringe_c']
    if device['kind'] != 'syringe':
        raise ValueError('Syringe initialization requires a syringe device')
    return {'sequence_id': 'syringe_initialization', 'description': 'Explicit syringe mechanical homing',
            'steps': [{'id': 'step_01', 'index': 1, 'device': 'syringe_c', 'device_label': device['label'],
                       'action': 'initialize_syringe', 'operation': 'initialize', 'description': 'Mechanical homing and verification'}],
            'totals': {}, 'unresolved': [], 'ready_for_hardware': True}


def water_calibration_plan(devices):
    plan=initialization_plan(devices)
    valve=devices['devices'].get('valve',{})
    if valve.get('kind')!='valve' or valve.get('routes',{}).get('water')!=5:
        raise ValueError('回零前必须配置并确认入口阀 water 对应通道 5。')
    plan['description']='Verify the water inlet before syringe mechanical homing'
    home=plan['steps'][0]
    home.update(id='step_02',index=2,requires_source={'device':'valve','route':'water','channel':5})
    plan['steps']=[{'id':'step_01','index':1,'device':'valve','device_label':valve['label'],
        'action':'switch_valve','operation':'switch_route','route':'water','channel':5,
        'description':'Select water and verify inlet channel 5 before homing'},home]
    return plan


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()


def version(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def write_private(path, value):
    from .repository import LabRepository
    LabRepository._atomic_write(path, encoded(value))
    path.chmod(0o600)


def worker_identity(pid, run_id):
    """Match the process start time as well as its exact worker arguments."""
    try:
        process = Path('/proc') / str(pid)
        arguments = (process / 'cmdline').read_bytes().split(b'\0')
        if b'pump_console.execution_worker' not in arguments or run_id.encode() not in arguments:
            return None
        fields = (process / 'stat').read_text().rsplit(') ', 1)[1].split()
        if fields[0] == 'Z' or process.stat().st_uid != os.getuid():
            return None
        return fields[19]
    except (OSError, ValueError, IndexError, TypeError):
        return None


def signal_worker(job):
    """Pin the PID before checking identity, so a reused PID cannot be signalled."""
    descriptor = os.pidfd_open(job['pid'])
    try:
        if worker_identity(job['pid'], job['run_id']) != job['process_identity']:
            raise ProcessLookupError('The original controller has exited')
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
    finally:
        os.close(descriptor)


class ExecutionManager:
    def __init__(self, repository, enabled=False, policy=None, launcher=None):
        self.repository = repository
        self.enabled = enabled
        self.policy = {'prepare_ttl_seconds': 600, **(policy or {})}
        if set(self.policy) != {'prepare_ttl_seconds'} or type(self.policy['prepare_ttl_seconds']) is not int or not 30 <= self.policy['prepare_ttl_seconds'] <= 3600:
            raise ValueError('Invalid automation preparation lifetime')
        self.directory = repository.data_root / 'executions'
        self.lock = threading.RLock()
        self.launcher = launcher or subprocess.Popen
        self.children = {}

    def _enabled(self):
        if not self.enabled:
            raise ApiError(403, '服务器尚未开放自动化执行。', 'hardware_disabled')

    def _folder(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ApiError(400, 'Invalid execution ID')
        folder = self.directory / run_id
        if folder.is_symlink() or folder.resolve().parent != self.directory.resolve():
            raise ApiError(400, 'Invalid execution path')
        return folder

    def _job(self, run_id):
        folder = self._folder(run_id)
        try:
            return folder, parse_json((folder / 'job.json').read_bytes())
        except FileNotFoundError:
            raise ApiError(404, '运行准备记录不存在，请重新准备。', 'not_found')

    def _current(self):
        try:
            run_id = parse_json((self.directory / 'current.json').read_bytes())['run_id']
            return self._job(run_id)
        except (FileNotFoundError, ApiError, KeyError):
            return None, None

    def _templates(self):
        return self.repository.compiler.load_templates(self.repository.templates_dir)

    def prepare(self, sequence, actor):
        self._enabled()
        devices, templates = self._templates()
        preview = self.repository.preview(sequence, _templates=(devices, templates))
        plan = preview['plan']
        if any(step.get('execution_supported') is False for step in plan['steps']):
            raise ApiError(422, '电化学工作站尚未接入；包含测量的方案不能启动，也不会跳过测量执行。', 'instrument_not_integrated')
        if plan['unresolved'] or not plan.get('ready_for_hardware'):
            raise ApiError(422, '方案仍有未解决的配置：' + '; '.join(plan['unresolved']), 'configuration_incomplete')
        operation = 'debug_sequence' if sequence_mode(sequence) == 'debug' and not preparation_required(sequence) else 'sequence'
        return self._store_preparation(sequence, devices, templates, preview, actor, operation=operation)

    def _store_preparation(self, sequence, devices, templates, preview, actor, operation='sequence', target_run_id=None):
        plan = preview['plan']
        now = datetime.now(timezone.utc)
        run_id = now.strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(6)
        snapshot = {'schema_version': 1, 'sequence': sequence, 'devices': devices, 'templates': templates, 'plan': plan,
                    'operation': operation, 'target_run_id': target_run_id, 'safety_policy': self.repository.safety_policy(),
                    'warnings': preview.get('warnings',[])}
        from .topology import INLET
        if self.repository.topology == INLET:
            snapshot['fluidic_topology'] = INLET
        if any(step.get('action') == 'measurement' for step in plan['steps']):
            from copy import deepcopy
            snapshot['station'] = deepcopy(self.repository.station)
        if any(step.get('action')=='syringe_transfer' for step in plan['steps']):
            from .syringe_batch import load_policy
            snapshot['syringe_policy']=load_policy()
        fingerprint = version(snapshot)
        with self.lock:
            folder = self._folder(run_id)
            folder.mkdir(parents=True, mode=0o700)
            self.directory.chmod(0o700)
            write_private(folder / 'snapshot.json', snapshot)
            job = {'run_id': run_id, 'sequence_id': sequence['id'], 'version': fingerprint,
                   'sequence_mode': sequence_mode(sequence),
                   'operation': operation, 'target_run_id': target_run_id,
                   'discharge_confirmed': operation in ('calibrate_syringe', 'drain_cell'),
                   'preflight_run_ids': [], 'cell_drain_run_id': None, 'cell_waste_route': None,
                   'cell_empty_confirmed_at': None, 'cell_empty_confirmed_by': None, 'cell_preparation_consumed_at': None,
                   'requires_syringe_calibration': operation == 'sequence' and any(step['device'] == 'syringe_c' for step in plan['steps']),
                   'calibration_run_id': None, 'calibration_consumed_at': None,
                   'prepared_by': actor, 'prepared_at': now.isoformat(), 'state': 'prepared',
                   'templates_version': version(templates), 'pid': None, 'process_identity': None,
                   'started_by': None, 'started_at': None, 'stop_requested_at': None, 'stop_requested_by': None}
            write_private(folder / 'job.json', job)
        return {'run_id': run_id, 'version': fingerprint, 'sequence': sequence, 'preview': preview,
                'operation': operation, 'calibration': self._calibration_state(folder, job),
                'cell_preparation': self._cell_state(folder, job),
                'prepared_at': now.isoformat(), 'expires_in_seconds': self.policy['prepare_ttl_seconds'], 'hardware_executed': False}

    def _calibration_state(self, folder, job):
        required = job.get('requires_syringe_calibration')
        if required is None:
            snapshot = parse_json((folder / 'snapshot.json').read_bytes())
            required = snapshot.get('operation', 'sequence') == 'sequence' and any(step['device'] == 'syringe_c' for step in snapshot['plan']['steps'])
        result = {'required': bool(required), 'state': 'not_required', 'run_id': None}
        if not result['required']:
            return result
        if job.get('calibration_consumed_at'):
            return {**result, 'state': 'consumed', 'completed_at': job['calibration_consumed_at']}
        run_id = job.get('calibration_run_id')
        if not run_id:
            return {**result, 'state': 'required'}
        calibration_folder, calibration_job = self._job(run_id)
        summary = self._summary(calibration_folder, calibration_job)
        result.update(run_id=run_id, message=summary.get('message'))
        try:
            worker = parse_json((calibration_folder / 'worker.json').read_bytes())
        except FileNotFoundError:
            worker = {}
        result.update(water_source_verified=worker.get('water_source_verified') is True,
                      water_channel=worker.get('water_channel'),phase=worker.get('phase'))
        if summary['controller_alive']:
            return {**result, 'state': 'running'}
        if calibration_job['state'] == 'prepared':
            return {**result, 'state': 'required'}
        if (summary['state'] != 'completed' or worker.get('initialization_verified') is not True or
                worker.get('water_source_verified') is not True or worker.get('water_channel')!=5):
            return {**result, 'state': 'failed'}
        _, current = self._current()
        completed = timestamp(worker.get('finished_at'))
        expired = completed is None or (datetime.now(timezone.utc) - completed).total_seconds() > self.policy['prepare_ttl_seconds']
        allowed = {'run-' + value for value in job.get('preflight_run_ids', [])} | {'run-' + run_id}
        externally_changed = completed is not None and any(path.stat().st_mtime > completed.timestamp() for path in self.repository.logs_dir.glob('run-*.jsonl') if path.stem not in allowed)
        current_allowed = current and (current['run_id'] == run_id or (current.get('operation') == 'drain_cell' and current.get('target_run_id') == job['run_id']))
        if not current_allowed or calibration_job.get('target_run_id') != job['run_id'] or expired or externally_changed:
            return {**result, 'state': 'invalidated', 'message': '校验后已有其他任务或校验已过期，请重新校验。'}
        return {**result, 'state': 'passed', 'completed_at': worker['finished_at']}

    def preparation_status(self, run_id):
        with self.lock:
            folder, job = self._job(run_id)
            calibration, cell = self._calibration_state(folder, job), self._cell_state(folder, job)
            result = {'run_id': run_id, 'calibration': calibration, 'cell_preparation': cell}
            if job.get('operation') == 'debug_sequence':
                snapshot = parse_json((folder / 'snapshot.json').read_bytes())
                summary = self._summary(folder, job) if job['state'] != 'prepared' else None
                plan = snapshot['plan']
                return {**result, 'version':job['version'], 'sequence':snapshot['sequence'], 'operation':'debug_sequence',
                    'preview':{'plan':plan,'preflight':None,'sequence_mode':'debug','safety_policy':snapshot['safety_policy'],
                               'cell_safety':require_safe_volumes(snapshot['sequence'],snapshot['safety_policy'],plan),
                               'estimated_timed_seconds':sum(step.get('duration_s') or step.get('estimated_movement_s') or step.get('estimated_measurement_s') or 0 for step in plan['steps']),
                               'warnings':snapshot.get('warnings',['调试流程不自动执行前置准备，需现场确认初始液量和接液条件。']),'hardware_executed':False},
                    'workflow':{'phase':summary['state'] if summary else 'awaiting_debug_confirmation',
                                'main_started':job['state']!='prepared','needs_advance':False}}
            if job.get('operation', 'sequence') == 'sequence':
                snapshot = parse_json((folder / 'snapshot.json').read_bytes())
                try:
                    preflight = require_preflight(snapshot['sequence'], snapshot['safety_policy'])
                except ValueError:
                    return {**result, 'workflow': {'phase': 'failed', 'message': '旧准备记录缺少前置功能块，请重新载入方案。', 'needs_advance': False}}
                plan = snapshot['plan']
                result.update(version=job['version'], sequence=snapshot['sequence'], operation='sequence',
                    preview={'plan': plan, 'preflight': preflight, 'safety_policy': snapshot['safety_policy'],
                             'cell_safety': require_safe_volumes(snapshot['sequence'], snapshot['safety_policy'], plan),
                             'estimated_timed_seconds': sum(step.get('duration_s') or step.get('estimated_movement_s') or step.get('estimated_measurement_s') or 0 for step in plan['steps']),
                             'warnings': [], 'hardware_executed': False},
                    workflow=self._workflow_state(folder, job, calibration, cell))
            return result

    def _workflow_state(self, folder, job, calibration, cell):
        state = job.get('workflow', {})
        result = {'authorized': state.get('authorized') is True, 'needs_advance': False}
        if job['state'] != 'prepared':
            summary = self._summary(folder, job)
            return {**result, 'phase': summary['state'], 'main_started': True}
        if state.get('failed'):
            return {**result, 'phase': 'failed', 'message': state.get('message')}
        if not result['authorized']:
            return {**result, 'phase': 'awaiting_start'}
        if calibration['required'] and calibration['state'] != 'passed':
            if calibration['state'] == 'running':
                return {**result, 'phase': 'homing'}
            if calibration['state'] != 'required':
                return {**result, 'phase': 'failed', 'message': calibration.get('message') or '前置回零未通过，请重新准备流程。'}
            return {**result, 'phase': 'ready_to_home', 'needs_advance': True}
        if cell['state'] == 'required':
            return {**result, 'phase': 'ready_to_drain', 'needs_advance': True}
        if cell['state'] == 'running':
            return {**result, 'phase': 'draining'}
        if cell['state'] == 'awaiting_confirmation':
            return {**result, 'phase': 'awaiting_confirmation'}
        if cell['state'] == 'passed':
            return {**result, 'phase': 'ready_to_start'}
        return {**result, 'phase': 'failed', 'message': cell.get('message') or '前置排空或确认未通过，请重新准备流程。'}

    def advance_workflow(self, run_id, expected_version, actor, confirm_discharge=False, empty_confirmed=False):
        """Advance one authorized stage. Polling GET requests never call this method."""
        self._enabled()
        if type(confirm_discharge) is not bool or type(empty_confirmed) is not bool:
            raise ApiError(400, 'Invalid workflow acknowledgement')
        with self.lock:
            folder, job = self._job(run_id)
            if job.get('operation', 'sequence') != 'sequence' or job['version'] != expected_version:
                raise ApiError(409, '流程准备记录不匹配。', 'version_conflict')
            if job['state'] != 'prepared':
                return {**self.preparation_status(run_id), 'accepted': True, 'already_started': True}
            folder, job, snapshot = self._prepared_snapshot(run_id, expected_version)
            try:
                preflight = require_preflight(snapshot['sequence'], snapshot['safety_policy'])
            except ValueError as error:
                raise ApiError(422, str(error), 'preflight_required') from error
            workflow = job.get('workflow', {})
            if workflow.get('failed'):
                raise ApiError(409, '前置流程已失败，请重新准备，不会自动重试。', 'preflight_failed')
            if workflow.get('authorized') is not True:
                if confirm_discharge is not True:
                    raise ApiError(422, '开始前置功能块前须确认回零与排液条件。', 'discharge_confirmation_required')
                workflow = {'authorized': True, 'authorized_by': actor, 'authorized_at': datetime.now(timezone.utc).isoformat()}
                job['workflow'] = workflow
                write_private(folder / 'job.json', job)
            status = self.preparation_status(run_id)
            phase = status['workflow']['phase']
            try:
                if empty_confirmed and phase not in ('awaiting_confirmation', 'ready_to_start'):
                    raise ApiError(409, '排空尚未完成，不能预先确认空池。', 'cell_preparation_required')
                if phase == 'ready_to_home':
                    child_id = job.get('calibration_run_id')
                    if child_id:
                        _, child = self._job(child_id)
                        self.start(child_id, child['version'], actor)
                    else:
                        self.calibrate(run_id, expected_version, actor, True)
                elif phase == 'ready_to_drain':
                    child_id = job.get('cell_drain_run_id')
                    if child_id:
                        _, child = self._job(child_id)
                        self.start(child_id, child['version'], actor)
                    else:
                        self.drain_cell(run_id, expected_version, actor, preflight['route'], True, 'workflow_drain')
                elif phase in ('awaiting_confirmation', 'ready_to_start'):
                    if empty_confirmed is True:
                        self.confirm_empty(run_id, expected_version, actor, True)
                        self.start(run_id, expected_version, actor)
                elif phase == 'failed':
                    raise ApiError(409, status['workflow'].get('message') or '前置步骤失败。', 'preflight_failed')
            except ApiError as error:
                _, latest = self._job(run_id)
                latest['workflow'] = {**latest.get('workflow', {}), 'failed': True, 'message': str(error)}
                write_private(folder / 'job.json', latest)
                raise
            return {**self.preparation_status(run_id), 'accepted': True}

    def _cell_state(self, folder, job):
        required = job.get('operation', 'sequence') == 'sequence'
        result = {'required': required, 'state': 'required' if required else 'not_required', 'run_id': job.get('cell_drain_run_id'),
                  'volume_measured': False, 'confirmation_source': 'operator', 'route': job.get('cell_waste_route')}
        if not required:
            return result
        if job.get('cell_preparation_consumed_at'):
            return {**result, 'state': 'consumed', 'confirmed_at': job.get('cell_empty_confirmed_at')}
        run_id = job.get('cell_drain_run_id')
        if not run_id:
            return result
        drain_folder, drain_job = self._job(run_id)
        summary = self._summary(drain_folder, drain_job)
        if summary['controller_alive']:
            return {**result, 'state': 'running'}
        try:
            worker = parse_json((drain_folder / 'worker.json').read_bytes())
        except FileNotFoundError:
            worker = {}
        if drain_job['state'] == 'prepared':
            return result
        if summary['state'] != 'completed' or worker.get('cell_drain_completed') is not True:
            return {**result, 'state': 'failed', 'message': summary.get('message')}
        _, current = self._current()
        completed = timestamp(worker.get('finished_at'))
        changed = completed is None or any(path.stat().st_mtime > completed.timestamp() for path in self.repository.logs_dir.glob('run-*.jsonl') if path.stem != 'run-' + run_id)
        expired = completed is None or (datetime.now(timezone.utc) - completed).total_seconds() > self.policy['prepare_ttl_seconds']
        if not current or current['run_id'] != run_id or drain_job.get('target_run_id') != job['run_id'] or changed or expired:
            return {**result, 'state': 'invalidated', 'message': '排空后有其他控制操作或记录过期，需重新排空并确认。'}
        return {**result, 'state': 'passed' if job.get('cell_empty_confirmed_at') else 'awaiting_confirmation',
                'confirmed_at': job.get('cell_empty_confirmed_at'), 'confirmed_by': job.get('cell_empty_confirmed_by'), 'drain_completed_at': worker['finished_at']}

    def _prepared_snapshot(self, run_id, expected_version):
        folder, job = self._job(run_id)
        if job.get('workflow', {}).get('failed'):
            raise ApiError(409, '前置流程已失败，请重新准备。', 'preflight_failed')
        if job['state'] != 'prepared' or job['version'] != expected_version:
            raise ApiError(409, '方案准备记录已变化，请重新准备。', 'version_conflict')
        snapshot = parse_json((folder / 'snapshot.json').read_bytes())
        self._syringe_policy_unchanged(snapshot)
        if version(snapshot) != expected_version or snapshot.get('safety_policy') != self.repository.safety_policy():
            raise ApiError(409, '安全规则已更新，请重新准备。', 'preparation_policy_changed')
        if 'station' in snapshot and snapshot['station'] != self.repository.station:
            raise ApiError(409, '工作站配置已更新，请重新准备。', 'station_configuration_changed')
        if version(self._templates()[1]) != job['templates_version']:
            raise ApiError(409, '设备模板已更新，请重新准备。', 'templates_changed')
        if not 0 <= (datetime.now(timezone.utc) - timestamp(job['prepared_at'])).total_seconds() <= self.policy['prepare_ttl_seconds']:
            raise ApiError(409, '准备记录已过期，请重新准备。', 'preparation_expired')
        return folder, job, snapshot

    def drain_cell(self, run_id, expected_version, actor, route, confirm_discharge, request_id):
        self._enabled()
        if confirm_discharge is not True:
            raise ApiError(422, '请先确认废液流路和接液条件。', 'discharge_confirmation_required')
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            raise ApiError(400, 'Invalid drain request ID')
        with self.lock:
            folder, job, snapshot = self._prepared_snapshot(run_id, expected_version)
            try:
                preflight = require_preflight(snapshot['sequence'], snapshot['safety_policy'])
            except ValueError as error:
                raise ApiError(422, str(error), 'preflight_required') from error
            if route in snapshot['safety_policy']['cell']['allowed_waste_routes'] and route != preflight['route']:
                raise ApiError(422, '排液流路必须与前置功能块中设定的流路一致。', 'preflight_route_mismatch')
            status = self._cell_state(folder, job)
            previous = job.get('cell_drain_requests', {}).get(request_id)
            if previous:
                if previous['route'] != route:
                    raise ApiError(409, '同一排空请求不能更改废液流路。', 'version_conflict')
                return {'run_id': run_id, 'accepted': True, 'already_started': True, 'cell_preparation': status}
            if status['state'] == 'running':
                raise ApiError(409, '本次排空正在执行，请等待完成。', 'execution_busy')
            try:
                sequence = drain_sequence(snapshot['safety_policy'], route)
                preview = self.repository.preview(sequence, _templates=(snapshot['devices'], snapshot['templates']), _internal_drain=True)
            except ValueError as error:
                raise ApiError(422, str(error), 'invalid_waste_route') from error
            if preview['plan']['unresolved'] or not preview['plan']['ready_for_hardware']:
                raise ApiError(422, '启动前排空参数不完整：' + '; '.join(preview['plan']['unresolved']), 'configuration_incomplete')
            prepared = self._store_preparation(sequence, snapshot['devices'], snapshot['templates'], preview, actor, operation='drain_cell', target_run_id=run_id)
            job.update(cell_drain_run_id=prepared['run_id'], cell_waste_route=route, cell_empty_confirmed_at=None, cell_empty_confirmed_by=None)
            job.setdefault('cell_drain_requests', {})[request_id] = {'run_id': prepared['run_id'], 'route': route}
            job.setdefault('preflight_run_ids', []).append(prepared['run_id'])
            write_private(folder / 'job.json', job)
            result = self.start(prepared['run_id'], prepared['version'], actor)
            return {**result, 'run_id': run_id, 'cell_preparation': self._cell_state(folder, job)}

    def confirm_empty(self, run_id, expected_version, actor, empty_confirmed):
        self._enabled()
        if empty_confirmed is not True:
            raise ApiError(422, '必须现场确认电解池已排空。', 'empty_confirmation_required')
        with self.lock:
            folder, job, _ = self._prepared_snapshot(run_id, expected_version)
            status = self._cell_state(folder, job)
            if status['state'] not in ('awaiting_confirmation', 'passed'):
                raise ApiError(409, '本次排空动作尚未成功完成，不能确认空池。', 'cell_preparation_required')
            if status['state'] != 'passed':
                job.update(cell_empty_confirmed_at=datetime.now(timezone.utc).isoformat(), cell_empty_confirmed_by=actor)
                write_private(folder / 'job.json', job)
            return {'run_id': run_id, 'cell_preparation': self._cell_state(folder, job)}

    def calibrate(self, run_id, expected_version, actor, confirm_discharge):
        self._enabled()
        if confirm_discharge is not True:
            raise ApiError(422, '请先确认管路允许机械回零排液。', 'discharge_confirmation_required')
        with self.lock:
            folder, job = self._job(run_id)
            if job.get('workflow', {}).get('failed'):
                raise ApiError(409, '前置流程已失败，请重新准备。', 'preflight_failed')
            if job['version'] != expected_version or job['state'] != 'prepared':
                raise ApiError(409, '方案准备记录已变化，请重新准备。', 'version_conflict')
            calibration = self._calibration_state(folder, job)
            if not calibration['required']:
                raise ApiError(422, '此实验没有需要回零的注射泵。')
            if calibration['state'] in ('running', 'passed'):
                return {'run_id': run_id, 'accepted': True, 'already_started': True, 'calibration': calibration}
            snapshot = parse_json((folder / 'snapshot.json').read_bytes())
            self._syringe_policy_unchanged(snapshot)
            try:
                require_preflight(snapshot['sequence'], snapshot['safety_policy'])
            except ValueError as error:
                raise ApiError(422, str(error), 'preflight_required') from error
            if snapshot.get('safety_policy') != self.repository.safety_policy():
                raise ApiError(409, '安全规则已更新，请重新准备。', 'preparation_policy_changed')
            if version(snapshot) != expected_version or version(self._templates()[1]) != job['templates_version']:
                raise ApiError(409, '方案或设备模板已变化，请重新准备。', 'templates_changed')
            if (datetime.now(timezone.utc) - timestamp(job['prepared_at'])).total_seconds() > self.policy['prepare_ttl_seconds']:
                raise ApiError(409, '准备记录已过期，请重新准备。', 'preparation_expired')
            try:
                plan = water_calibration_plan(snapshot['devices'])
            except ValueError as error:
                raise ApiError(422,str(error),'homing_water_source_required') from error
            sequence = {'schema_version': 2, 'id': plan['sequence_id'], 'description': plan['description'],
                        'steps': [{'device':'valve','operation':'switch_route','route':'water'},
                                  {'device': 'syringe_c', 'operation': 'initialize'}]}
            prepared = self._store_preparation(sequence, snapshot['devices'], snapshot['templates'],
                {'plan': plan, 'valid': True, 'estimated_timed_seconds': None, 'hardware_executed': False, 'warnings': []},
                actor, operation='calibrate_syringe', target_run_id=run_id)
            job['calibration_run_id'] = prepared['run_id']
            job.setdefault('preflight_run_ids', []).append(prepared['run_id'])
            write_private(folder / 'job.json', job)
            result = self.start(prepared['run_id'], prepared['version'], actor)
            return {**result, 'run_id': run_id, 'calibration': self._calibration_state(folder, job)}

    def _alive(self, job):
        child = self.children.get(job['run_id'])
        if child is not None and child.poll() is not None:
            return False
        return bool(job.get('pid') and job.get('process_identity') and worker_identity(job['pid'], job['run_id']) == job['process_identity'])

    def _summary(self, folder, job):
        try:
            worker = parse_json((folder / 'worker.json').read_bytes())
        except FileNotFoundError:
            worker = {}
        alive = self._alive(job)
        state = worker.get('state', job['state'])
        if alive and state not in TERMINAL_STATES:
            state = 'stopping' if job.get('stop_requested_at') else state
        elif not alive and state not in TERMINAL_STATES:
            state = 'failed'
        message = worker.get('message')
        if not alive and state == 'failed' and not message:
            message = '控制进程已退出，未收到完整的结束记录；请检查设备状态。'
        return {key: job.get(key) for key in ('run_id', 'sequence_id', 'prepared_by', 'started_by', 'started_at', 'stop_requested_at', 'stop_requested_by', 'operation', 'target_run_id')} | {
            'sequence_mode': job.get('sequence_mode','experiment'),
            'state': state, 'controller_alive': alive, 'can_stop': alive and state not in TERMINAL_STATES,
            'syringe_calibration_passed': bool(job.get('calibration_consumed_at')),
            'cell_preparation_passed': bool(job.get('cell_preparation_consumed_at')),
            'message': message, 'finished_at': worker.get('finished_at'), 'cleanup_failed': worker.get('cleanup_failed', False),
            'exit_code': worker.get('exit_code'), 'stop_requested': bool(job.get('stop_requested_at'))}

    def snapshot(self):
        with self.lock:
            folder, job = self._current()
            current = self._summary(folder, job) if job else None
            try:
                last_homing = parse_json((self.repository.data_root / 'instruments/syringe_c.json').read_bytes())
                last_homing = {key: last_homing.get(key) for key in ('status', 'initialized_at', 'requires_initialization')}
            except FileNotFoundError:
                last_homing = None
            return {'enabled': self.enabled, 'administrator_required': True,
                    'measurement_execution_enabled': self.enabled and self.repository.station['enabled'], 'current': current,
                    'syringe_calibration': {'required_before_each_experiment': True, 'last_homing': last_homing}}

    def journal_path(self):
        with self.lock:
            _, job = self._current()
            return self.repository.logs_dir / ('run-' + job['run_id'] + '.jsonl') if job else None

    def _check_ports(self, devices, plan, station=None):
        names = list(dict.fromkeys(step['device'] for step in plan['steps']))
        names.extend(step['requires_source']['device'] for step in plan['steps'] if step.get('requires_source') and step['requires_source']['device'] not in names)
        for name in names:
            path = Path(station['port'] if name == 'electrochemical_station' and station else devices['devices'][name]['port'])
            try:
                mode = path.stat().st_mode
            except OSError:
                raise ApiError(422, name + '：未发现配置的串口，请检查设备连接。', 'serial_port_unavailable')
            if not stat.S_ISCHR(mode) or not os.access(path, os.R_OK | os.W_OK):
                raise ApiError(422, name + '：串口类型或访问权限不符合要求。', 'serial_port_unavailable')

    @staticmethod
    def _syringe_policy_unchanged(snapshot):
        from .syringe_batch import validate_snapshot_policy
        try:validate_snapshot_policy(snapshot)
        except ValueError as error:raise ApiError(409,str(error),'syringe_policy_changed') from error

    def start_debug(self, run_id, expected_version, actor, confirm_setup, confirm_homing_discharge=False):
        self._enabled()
        if type(confirm_setup) is not bool or type(confirm_homing_discharge) is not bool:
            raise ApiError(400,'Invalid debug acknowledgement')
        _, job = self._job(run_id)
        if job.get('operation') != 'debug_sequence':
            raise ApiError(422,'此接口仅用于没有前置块的调试流程。','not_debug_workflow')
        return self.start(run_id,expected_version,actor,_debug_setup=confirm_setup,_debug_homing=confirm_homing_discharge)

    def start(self, run_id, expected_version, actor, _debug_setup=False, _debug_homing=False):
        self._enabled()
        with self.lock:
            folder, job = self._job(run_id)
            if job.get('workflow', {}).get('failed'):
                raise ApiError(409, '前置流程已失败，请重新准备。', 'preflight_failed')
            if not isinstance(expected_version, str) or expected_version != job['version']:
                raise ApiError(409, '准备版本不匹配，请重新检查方案。', 'version_conflict')
            current_folder, current = self._current()
            if job['state'] != 'prepared':
                if current and current['run_id'] == run_id:
                    return {'accepted': True, 'already_started': True, 'execution': self._summary(current_folder, current)}
                raise ApiError(409, '此准备记录已经使用，请重新准备。', 'execution_already_used')
            age = (datetime.now(timezone.utc) - timestamp(job['prepared_at'])).total_seconds()
            if age < 0 or age > self.policy['prepare_ttl_seconds']:
                raise ApiError(409, '准备记录已过期，请重新检查方案。', 'preparation_expired')
            if current and self._alive(current):
                raise ApiError(409, '已有自动化流程正在运行，请等待结束或请求停止。', 'execution_busy')
            owner = active_controller(self.repository.logs_dir)
            if owner['alive'] is not False or owner['state'] not in ('idle',):
                raise ApiError(409, '控制器或设备状态查询正在占用串口。', 'execution_busy')
            snapshot = parse_json((folder / 'snapshot.json').read_bytes())
            self._syringe_policy_unchanged(snapshot)
            if version(snapshot) != expected_version:
                raise ApiError(409, '准备内容发生变化，请重新检查方案。', 'version_conflict')
            if version(self._templates()[1]) != job['templates_version']:
                raise ApiError(409, '设备模板已更新，请重新准备运行。', 'templates_changed')
            if snapshot.get('safety_policy') != self.repository.safety_policy():
                raise ApiError(409, '安全规则已更新，请重新准备。', 'preparation_policy_changed')
            if 'station' in snapshot and snapshot['station'] != self.repository.station:
                raise ApiError(409, '工作站配置已更新，请重新准备。', 'station_configuration_changed')
            if snapshot.get('operation', 'sequence') == 'sequence':
                try:
                    require_preflight(snapshot['sequence'], snapshot['safety_policy'])
                except ValueError as error:
                    raise ApiError(422, str(error), 'preflight_required') from error
                require_safe_volumes(snapshot['sequence'], snapshot['safety_policy'], snapshot['plan'])
            elif snapshot.get('operation') == 'debug_sequence':
                if sequence_mode(snapshot['sequence']) != 'debug' or preparation_required(snapshot['sequence']):
                    raise ApiError(422,'调试流程类型不匹配。','invalid_debug_workflow')
                if _debug_setup is not True:
                    raise ApiError(422,'请先确认调试初始液量、液路和现场条件。','debug_confirmation_required')
                if any(step['action']=='initialize_syringe' for step in snapshot['plan']['steps']) and _debug_homing is not True:
                    raise ApiError(422,'含回零动作时须确认机械排液条件。','discharge_confirmation_required')
                require_safe_volumes(snapshot['sequence'],snapshot['safety_policy'],snapshot['plan'])
            self._check_ports(snapshot['devices'], snapshot['plan'], snapshot.get('station'))
            calibration = self._calibration_state(folder, job)
            if calibration['required'] and calibration['state'] != 'passed':
                raise ApiError(409, '本次实验尚未完成注射泵回零校验，请先确认排液条件并执行校验。', 'syringe_calibration_required')
            cell_preparation = self._cell_state(folder, job)
            if cell_preparation['required'] and cell_preparation['state'] != 'passed':
                raise ApiError(409, '本次实验尚未完成启动前排空和人工空池确认。', 'cell_preparation_required')
            if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
                raise ApiError(503, '执行服务需要支持进程身份保护的 Linux 环境。', 'execution_unavailable')
            job.update(state='starting', started_by=actor, started_at=datetime.now(timezone.utc).isoformat())
            if snapshot.get('operation') == 'debug_sequence':
                job.update(debug_setup_confirmed=True,debug_homing_discharge_confirmed=_debug_homing)
            if calibration['required']:
                job['calibration_consumed_at'] = calibration['completed_at']
            if cell_preparation['required']:
                job['cell_preparation_consumed_at'] = datetime.now(timezone.utc).isoformat()
            write_private(folder / 'job.json', job)
            write_private(self.directory / 'current.json', {'run_id': run_id})
            environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]), 'PYTHONUNBUFFERED': '1'}
            command = [sys.executable, '-u', '-m', 'pump_console.execution_worker', '--lab-root', str(self.repository.root),
                       '--run-directory', str(self.directory.resolve()), '--run-id', run_id, '--execute']
            try:
                with (folder / 'controller.log').open('ab') as output:
                    child = self.launcher(command, cwd=self.repository.root, env=environment, stdin=subprocess.DEVNULL,
                                          stdout=output, stderr=subprocess.STDOUT, close_fds=True)
                self.children[run_id] = child
                job.update(pid=child.pid, process_identity=worker_identity(child.pid, run_id))
                write_private(folder / 'job.json', job)
                threading.Thread(target=child.wait, daemon=True, name='controller-' + run_id).start()
            except OSError:
                job['state'] = 'failed'
                write_private(folder / 'job.json', job)
                raise ApiError(503, '控制进程未能启动，请检查服务器日志。', 'execution_unavailable')
            return {'accepted': True, 'already_started': False, 'execution': self._summary(folder, job)}

    def stop(self, run_id, actor):
        self._enabled()
        with self.lock:
            folder, job = self._current()
            if not job or run_id != job['run_id']:
                raise ApiError(409, '当前运行记录已变化，请刷新后重试。', 'execution_changed')
            summary = self._summary(folder, job)
            if not summary['controller_alive'] or job.get('stop_requested_at'):
                return {'stop_requested': bool(job.get('stop_requested_at')), 'execution': summary}
            job.update(stop_requested_at=datetime.now(timezone.utc).isoformat(), stop_requested_by=actor)
            write_private(folder / 'job.json', job)
            try:
                signal_worker(job)
            except ProcessLookupError:
                pass
            except OSError:
                job.update(stop_requested_at=None, stop_requested_by=None)
                write_private(folder / 'job.json', job)
                raise ApiError(503, '未能向控制器发送停止请求，请立即检查服务器与设备。', 'stop_request_failed')
            return {'stop_requested': True, 'execution': self._summary(folder, job)}
