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

ACTIVE_STATES = {"starting", "running", "stopping"}
TERMINAL_STATES = {"completed", "stopped", "failed"}
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{12}")


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
        if any(step.get('execution_supported') is False or step.get('action') == 'measurement' for step in plan['steps']):
            raise ApiError(422, '电化学工作站尚未接入；包含测量的方案不能启动，也不会跳过测量执行。', 'instrument_not_integrated')
        if plan['unresolved'] or not plan.get('ready_for_hardware'):
            raise ApiError(422, '方案仍有未解决的配置：' + '; '.join(plan['unresolved']), 'configuration_incomplete')
        now = datetime.now(timezone.utc)
        run_id = now.strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(6)
        snapshot = {'schema_version': 1, 'sequence': sequence, 'devices': devices, 'templates': templates, 'plan': plan}
        fingerprint = version(snapshot)
        with self.lock:
            folder = self._folder(run_id)
            folder.mkdir(parents=True, mode=0o700)
            self.directory.chmod(0o700)
            write_private(folder / 'snapshot.json', snapshot)
            job = {'run_id': run_id, 'sequence_id': sequence['id'], 'version': fingerprint,
                   'prepared_by': actor, 'prepared_at': now.isoformat(), 'state': 'prepared',
                   'templates_version': version(templates), 'pid': None, 'process_identity': None,
                   'started_by': None, 'started_at': None, 'stop_requested_at': None, 'stop_requested_by': None}
            write_private(folder / 'job.json', job)
        return {'run_id': run_id, 'version': fingerprint, 'sequence': sequence, 'preview': preview,
                'prepared_at': now.isoformat(), 'expires_in_seconds': self.policy['prepare_ttl_seconds'], 'hardware_executed': False}

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
        return {key: job.get(key) for key in ('run_id', 'sequence_id', 'prepared_by', 'started_by', 'started_at', 'stop_requested_at', 'stop_requested_by')} | {
            'state': state, 'controller_alive': alive, 'can_stop': alive and state not in TERMINAL_STATES,
            'message': message, 'finished_at': worker.get('finished_at'), 'cleanup_failed': worker.get('cleanup_failed', False),
            'exit_code': worker.get('exit_code'), 'stop_requested': bool(job.get('stop_requested_at'))}

    def snapshot(self):
        with self.lock:
            folder, job = self._current()
            current = self._summary(folder, job) if job else None
            return {'enabled': self.enabled, 'administrator_required': True,
                    'measurement_execution_enabled': False, 'current': current}

    def journal_path(self):
        with self.lock:
            _, job = self._current()
            return self.repository.logs_dir / ('run-' + job['run_id'] + '.jsonl') if job else None

    def _check_ports(self, devices, plan):
        for name in dict.fromkeys(step['device'] for step in plan['steps']):
            path = Path(devices['devices'][name]['port'])
            try:
                mode = path.stat().st_mode
            except OSError:
                raise ApiError(422, name + '：未发现配置的串口，请检查设备连接。', 'serial_port_unavailable')
            if not stat.S_ISCHR(mode) or not os.access(path, os.R_OK | os.W_OK):
                raise ApiError(422, name + '：串口类型或访问权限不符合要求。', 'serial_port_unavailable')

    def start(self, run_id, expected_version, actor):
        self._enabled()
        with self.lock:
            folder, job = self._job(run_id)
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
            if version(snapshot) != expected_version:
                raise ApiError(409, '准备内容发生变化，请重新检查方案。', 'version_conflict')
            if version(self._templates()[1]) != job['templates_version']:
                raise ApiError(409, '设备模板已更新，请重新准备运行。', 'templates_changed')
            self._check_ports(snapshot['devices'], snapshot['plan'])
            if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
                raise ApiError(503, '执行服务需要支持进程身份保护的 Linux 环境。', 'execution_unavailable')
            job.update(state='starting', started_by=actor, started_at=datetime.now(timezone.utc).isoformat())
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
