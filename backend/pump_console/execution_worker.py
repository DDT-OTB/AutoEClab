"""Fixed controller entry point; only server-prepared snapshots may execute."""
import argparse
from datetime import datetime, timezone
import fcntl
import importlib
import json
from pathlib import Path
import sys

from .execution import RUN_ID, water_calibration_plan, version, write_private
from .measurement import compile_workflow, is_measurement
from .repository import parse_json
from .cell_safety import drain_sequence, load_policy, require_safe_volumes
from .workflow import flatten_sequence,sequence_mode,preparation_required
from .preflight import require_preflight


def execute(lab_root, run_directory, run_id, runner_factory=None):
    if not RUN_ID.fullmatch(run_id):
        raise ValueError('Invalid execution ID')
    folder = Path(run_directory).resolve() / run_id
    snapshot = parse_json((folder / 'snapshot.json').read_bytes())
    job = parse_json((folder / 'job.json').read_bytes())
    if version(snapshot) != job['version']:
        raise ValueError('Prepared snapshot version changed')
    root = Path(lab_root).resolve()
    sys.path.insert(0, str(root / 'src'))
    config = importlib.import_module('lab_automation.config')
    compiler = importlib.import_module('lab_automation.templates')
    controller = importlib.import_module('lab_automation.__main__')
    operation = snapshot.get('operation', 'sequence')
    if operation not in ('sequence', 'debug_sequence', 'calibrate_syringe', 'drain_cell'):
        raise ValueError('Unsupported controller operation')
    if snapshot.get('safety_policy') != load_policy(snapshot.get('fluidic_topology')):
        raise ValueError('Safety policy changed; prepare the experiment again')
    if operation == 'sequence':
        require_preflight(snapshot['sequence'], snapshot['safety_policy'])
        require_safe_volumes(snapshot['sequence'], snapshot['safety_policy'], snapshot['plan'])
        if not job.get('cell_preparation_consumed_at') or not job.get('cell_empty_confirmed_at'):
            raise ValueError('Experiment has no completed drain and operator empty-cell confirmation')
    if operation == 'debug_sequence':
        if sequence_mode(snapshot['sequence'])!='debug' or preparation_required(snapshot['sequence']) or job.get('debug_setup_confirmed') is not True:
            raise ValueError('Debug workflow has no matching live setup acknowledgement')
        if any(step['action']=='initialize_syringe' for step in snapshot['plan']['steps']) and job.get('debug_homing_discharge_confirmed') is not True:
            raise ValueError('Debug homing requires explicit discharge confirmation')
        require_safe_volumes(snapshot['sequence'],snapshot['safety_policy'],snapshot['plan'])
    if operation == 'drain_cell':
        if job.get('discharge_confirmed') is not True or job.get('target_run_id') != snapshot.get('target_run_id'):
            raise ValueError('Drain requires explicit discharge confirmation')
        route = snapshot['sequence']['steps'][0].get('route', 'waste')
        if snapshot['sequence'] != drain_sequence(snapshot['safety_policy'], route):
            raise ValueError('Startup drain does not match the server safety policy')
    if operation == 'sequence' and any(step['device'] == 'syringe_c' for step in snapshot['plan']['steps']) and not job.get('calibration_consumed_at'):
        raise ValueError('This syringe experiment has no verified calibration certificate')
    if operation == 'calibrate_syringe':
        if job.get('discharge_confirmed') is not True or job.get('target_run_id') != snapshot.get('target_run_id'):
            raise ValueError('Explicit discharge confirmation is required for calibration')
        config.validate_devices(snapshot['devices'])
    plan = water_calibration_plan(snapshot['devices']) if operation == 'calibrate_syringe' else compile_workflow(snapshot['sequence'], snapshot['devices'], snapshot['templates'], compiler, config, snapshot.get('station'), snapshot.get('fluidic_topology'),debug=sequence_mode(snapshot['sequence'])=='debug')
    if plan != snapshot['plan'] or plan['unresolved'] or not plan['ready_for_hardware']:
        raise ValueError('Prepared plan did not pass controller validation')
    logs = root / 'logs/automation'
    logs.mkdir(parents=True, exist_ok=True)
    state = {'state': 'starting', 'updated_at': datetime.now(timezone.utc).isoformat()}
    cleanup_failed = False
    serial_requests = 0
    stop_requested = False
    journal = None
    completed_step_count = 0
    active_plan_step = None

    def publish(**fields):
        state.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
        write_private(folder / 'worker.json', state)

    def event(kind, **fields):
        nonlocal cleanup_failed, serial_requests, completed_step_count, active_plan_step
        if operation in ('sequence','debug_sequence','calibrate_syringe') and kind == 'step_started':
            if load_policy(snapshot.get('fluidic_topology')) != snapshot['safety_policy']:
                raise ValueError('Cell safety policy changed during execution')
            if active_plan_step is not None or completed_step_count >= len(plan['steps']) or fields.get('step') != plan['steps'][completed_step_count]:
                raise ValueError('Controller step does not match the validated sequence')
            active_plan_step = fields['step']
            if operation=='calibrate_syringe':publish(phase='selecting_water' if fields['step']['device']=='valve' else 'homing')
        if operation in ('sequence','debug_sequence','calibrate_syringe') and kind == 'step_completed':
            if active_plan_step is None or fields.get('id') != active_plan_step['id']:
                raise ValueError('Controller completion does not match the active step')
            completed_step_count += 1
            active_plan_step = None
        if operation in ('sequence','debug_sequence','calibrate_syringe') and kind == 'sequence_completed' and completed_step_count != len(plan['steps']):
            raise ValueError('Controller reported completion before all validated steps finished')
        if operation=='calibrate_syringe' and kind=='syringe_water_source_verified':
            publish(water_source_verified=True,water_channel=fields['channel'])
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
                    if operation == 'calibrate_syringe':
                        from .syringe_initialization import initialize_with_water
                        result = initialize_with_water(snapshot['devices']['devices'], plan, event)
                        directory = Path(run_directory).resolve().parent / 'instruments'
                        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                        write_private(directory / 'syringe_c.json', {**result, 'device_id': 'syringe_c', 'status': 'initialized',
                            'requires_initialization': False, 'initialized_at': datetime.now(timezone.utc).isoformat(),
                            'run_id': run_id, 'source': 'experiment_calibration', 'target_run_id': snapshot['target_run_id']})
                        publish(initialization_verified=True,water_source_verified=result['water_source_verified'],water_channel=result['water_channel'])
                    else:
                        if operation=='debug_sequence' or any(step['action'] == 'measurement' for step in plan['steps']) or snapshot.get('fluidic_topology') == 'syringe_inlet_selector':
                            if (operation not in ('sequence','debug_sequence') and any(step['action'] == 'measurement' for step in plan['steps'])) or runner_factory is not None:
                                raise ValueError('Mixed CV execution requires the fixed workstation adapter')
                            from .mixed_runner import run_mixed
                            run_mixed(snapshot['devices']['devices'], plan, event, controller, snapshot.get('station'),
                                      Path(run_directory).resolve().parent, run_id, job['started_by'],
                                      execution_mode='debug_sequence' if sequence_mode(snapshot['sequence'])=='debug' else operation)
                        else:
                            factory = runner_factory or controller.Runner
                            factory(snapshot['devices']['devices'], plan, event).run()
                        if operation == 'drain_cell':
                            publish(cell_drain_completed=True)
                finally:
                    journal.close()
                    journal = None
        message = '本次实验的注射泵回零校验已通过。' if operation == 'calibrate_syringe' else '启动前排液动作完成，等待管理员现场确认空池。' if operation == 'drain_cell' else '调试流程的全部动作已完成。' if operation=='debug_sequence' else '全部步骤执行完成。'
        publish(state='completed', message=message, exit_code=0)
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
