"""Fixed controller entry point; only server-prepared snapshots may execute."""
import argparse
from datetime import datetime, timezone
import fcntl
import importlib
import json
from pathlib import Path
import sys

from .execution import RUN_ID, version, write_private
from .measurement import compile_workflow, is_measurement
from .repository import parse_json


def execute(lab_root, run_directory, run_id, runner_factory=None):
    if not RUN_ID.fullmatch(run_id):
        raise ValueError('Invalid execution ID')
    folder = Path(run_directory).resolve() / run_id
    snapshot = parse_json((folder / 'snapshot.json').read_bytes())
    job = parse_json((folder / 'job.json').read_bytes())
    if version(snapshot) != job['version']:
        raise ValueError('Prepared snapshot version changed')
    if any(is_measurement(step) for step in snapshot['sequence']['steps']):
        raise ValueError('Workstation execution is not integrated')
    root = Path(lab_root).resolve()
    sys.path.insert(0, str(root / 'src'))
    config = importlib.import_module('lab_automation.config')
    compiler = importlib.import_module('lab_automation.templates')
    controller = importlib.import_module('lab_automation.__main__')
    plan = compile_workflow(snapshot['sequence'], snapshot['devices'], snapshot['templates'], compiler, config)
    if plan != snapshot['plan'] or plan['unresolved'] or not plan['ready_for_hardware']:
        raise ValueError('Prepared plan did not pass controller validation')
    logs = root / 'logs/automation'
    logs.mkdir(parents=True, exist_ok=True)
    state = {'state': 'starting', 'updated_at': datetime.now(timezone.utc).isoformat()}
    cleanup_failed = False
    serial_requests = 0
    stop_requested = False
    journal = None

    def publish(**fields):
        state.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
        write_private(folder / 'worker.json', state)

    def event(kind, **fields):
        nonlocal cleanup_failed, serial_requests
        if kind in ('serial_reply', 'serial_error'):
            serial_requests += 1
        if kind == 'stop_failed':
            cleanup_failed = True
        journal(kind, **fields)

    # Install cleanup handlers before acquiring the serial lock or constructing a device.
    try:
        with controller.interrupt_cleanup():
            with (logs / 'execution.lock').open('a+') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError('Another controller or status query owns the serial lock')
                journal = controller.Journal(logs / ('run-' + run_id + '.jsonl'))
                try:
                    event('configuration_snapshot', devices=snapshot['devices'], sequence=snapshot['sequence'], plan=plan)
                    event('automation_started', actor=job['started_by'], run_id=run_id)
                    publish(state='running')
                    factory = runner_factory or controller.Runner
                    factory(snapshot['devices']['devices'], plan, event).run()
                finally:
                    journal.close()
                    journal = None
        publish(state='completed', message='全部步骤执行完成。', exit_code=0)
        code = 0
    except BaseException as error:
        stop_requested = isinstance(error, (controller.RunInterrupted, KeyboardInterrupt))
        code = 130 if stop_requested else 2
        publish(state='stopped' if stop_requested and not cleanup_failed else 'failed',
                message=('流程已中止，控制器已完成停止与关闭清理。' if stop_requested and not cleanup_failed else
                         '停止回读失败，设备状态需要现场确认。' if cleanup_failed else str(error)), exit_code=code)
    finally:
        if journal is not None:
            journal.close()
        publish(finished_at=datetime.now(timezone.utc).isoformat(), cleanup_failed=cleanup_failed,
                serial_requests=serial_requests, stop_requested=stop_requested)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab-root', required=True, type=Path)
    parser.add_argument('--run-directory', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--execute', required=True, action='store_true')
    arguments = parser.parse_args()
    try:
        return execute(arguments.lab_root, arguments.run_directory, arguments.run_id)
    except Exception as error:
        print('Controller startup failed: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
