"""Run validated pump actions and CV under one controller lock and journal."""
import importlib
import os
import sys

from .ec110 import EC110Session
from .measurement_store import MeasurementStore


def run_mixed(devices, plan, event, controller, station, data_root, run_id, actor,
              station_factory=EC110Session):
    device_module = importlib.import_module('lab_automation.devices')
    pump_plan = {**plan, 'steps': [step for step in plan['steps'] if step['action'] != 'measurement']}
    runner = controller.Runner(devices, pump_plan, event)
    measuring = any(step['action'] == 'measurement' for step in plan['steps'])
    session = station_factory(station, event) if measuring else None
    store = MeasurementStore(data_root) if measuring else None
    def cleanup_event(kind, **fields):
        try:
            event(kind, **fields)
        except Exception as exc:
            print('Cleanup event could not be recorded: ' + str(exc), file=sys.stderr)
    try:
        used = list(dict.fromkeys(step['device'] for step in pump_plan['steps']))
        resolved = [os.path.realpath(devices[name]['port']) for name in used]
        if measuring:
            resolved.append(os.path.realpath(station['port']))
        if len(set(resolved)) != len(resolved):
            raise ValueError('Pump and workstation device aliases share a serial port')
        for name in used:
            config = devices[name]
            link = runner.link_factory(name, config, event)
            runner.links.append(link)
            runner.devices[name] = device_module.DEVICE_CLASSES[config['kind']](
                name, config, link, event, clock=runner.clock, sleep=runner.sleep)
        # Every participating pump and the workstation must pass before movement.
        for name, device in runner.devices.items():
            if isinstance(device, device_module.Syringe):
                device.preflight([step for step in pump_plan['steps']
                                  if step['device'] == name and step['action'] == 'syringe_transfer'])
            else:
                device.preflight()
            event('preflight_passed', device=name)
        if session:
            session.open()
        event('sequence_started', sequence=plan['sequence_id'])
        for step in plan['steps']:
            event('step_started', step=step)
            if step['action'] == 'measurement':
                writer = store.create(run_id, plan['sequence_id'], step, actor, step['hardware_parameters'])
                session.measure(step, writer)
            else:
                device = runner.devices[step['device']]
                action = step['action']
                if action == 'drain':
                    valve = runner.devices[step['requires_route']['device']]
                    if not valve.normal() or valve.channel() != step['required_channel']:
                        raise ValueError('Waste valve position does not match the validated plan')
                    device.run(step)
                elif action == 'drain_direct':
                    device.run(step)
                elif action == 'dose':
                    device.run(step)
                elif action == 'switch_valve':
                    device.goto(step['channel'])
                elif action == 'syringe_transfer':
                    if step.get('requires_source'):
                        source = step['requires_source']
                        valve = runner.devices[source['device']]
                        if not valve.normal() or valve.channel() != source['channel']:
                            raise ValueError('注射泵吸液前回读的入口通道与液体不匹配，未开始吸液。')
                    device.transfer(step)
                elif action == 'wait':
                    runner.sleep(step['seconds'])
                else:
                    raise ValueError('Unsupported prepared action')
            event('step_completed', id=step['id'])
        event('sequence_completed', sequence=plan['sequence_id'])
    except BaseException as exc:
        cleanup_event('sequence_aborted', exception=type(exc).__name__, message=str(exc))
        for name, device in reversed(list(runner.devices.items())):
            if device.motion_pending:
                device.event = cleanup_event
                if hasattr(device.link, 'event'):
                    device.link.event = cleanup_event
                try:
                    device.stop()
                except Exception as stop_error:
                    cleanup_event('stop_failed', device=name, message=str(stop_error), motion_state='unknown')
        raise
    finally:
        try:
            if session:
                session.close()
        except Exception as exc:
            cleanup_event('close_failed', device='electrochemical_station', message=str(exc))
        for link in reversed(runner.links):
            try:
                link.close()
            except Exception as exc:
                cleanup_event('close_failed', message=str(exc))
