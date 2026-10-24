"""Real controller/API/SSE integration against a temporary pseudo-terminal pump."""
import http.client
import json
import os
from pathlib import Path
import select
import re
import sys
import threading
import time
import tty
import unittest
from unittest.mock import patch

import test_execution as authorization
from pump_console.auth import COOKIE_NAME
from pump_console.repository import LabRepository
from pump_console.preflight import default_preflight


class PseudoPump:
    def __init__(self, kind='peristaltic'):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.port = os.ttyname(self.slave)
        self.busy = False
        self.kind, self.channel, self.position, self.valve_position = kind, 5, 0, 'i'
        self.positions = []
        self.serial_number = 'SYNTHETIC-SYRINGE'
        self.buffer_state = '0'
        self.homing_count = 0
        self.homing_error = 0
        self.initial_error = 0
        self.stop_error = False
        self.commands = []
        self.ignore_switch = False
        self.pause_target = None
        self.fail_target = None
        self.after_move = None
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True); self.thread.start()

    def serve(self):
        pending = b''
        while not self.closed.is_set():
            if not select.select([self.master], [], [], .05)[0]:
                continue
            try:
                pending += os.read(self.master, 4096)
                if self.kind == 'syringe':
                    while b'\r' in pending:
                        packet, pending = pending.split(b'\r', 1)
                        command = packet[2:].decode('ascii'); self.commands.append(command)
                        payload = ''; move_target=None
                        if command == '?': payload = str(self.position)
                        elif command == '?10': payload = self.buffer_state
                        elif command == '?202': payload = self.serial_number
                        elif command == '?6': payload = self.valve_position
                        elif command == 'IR': self.valve_position = 'i'
                        elif command == 'OR': self.valve_position = 'o'
                        elif command == 'ZR':
                            self.homing_count += 1; self.position = 0; self.valve_position = 'o'; self.buffer_state = '0'
                        elif command == 'T':self.busy=False
                        elif re.fullmatch(r'v[0-9]+V[0-9]+A[0-9]+R', command):
                            self.position = int(re.search(r'A([0-9]+)', command).group(1)); self.positions.append(self.position);move_target=self.position
                            if self.pause_target==move_target:self.busy=True
                            if self.after_move:self.after_move(move_target)
                        status = (0x40 if self.busy else 0x60) | ((self.homing_error if self.homing_count else self.initial_error) if command=='Q' else 0)
                        if move_target is not None and self.fail_target==move_target:status|=3
                        os.write(self.master, b'/0' + bytes([status]) + payload.encode() + b'\x03\r\n')
                    continue
                while len(pending) >= 8:
                    packet, pending = pending[:8], pending[8:]
                    if packet[0] != 0xCC or packet[5] != 0xDD or sum(packet[:6]) != int.from_bytes(packet[6:], 'little'):
                        continue
                    function = packet[2]; self.commands.append(function)
                    if function in (0x47, 0x48): self.busy = True
                    if function == 0x49 and not self.stop_error: self.busy = False
                    if function == 0x44 and not self.ignore_switch: self.channel = int.from_bytes(packet[3:5], 'little')
                    status = 5 if function == 0x49 and self.stop_error else 4 if function == 0x4A and self.busy else 0
                    value = self.channel if function == 0x3E else 0
                    body = bytes((0xCC, packet[1], status, value, 0, 0xDD))
                    os.write(self.master, body + sum(body).to_bytes(2, 'little'))
            except OSError:
                return

    def close(self):
        self.closed.set(); self.thread.join(timeout=2)
        os.close(self.master); os.close(self.slave)


@unittest.skipUnless(sys.platform == 'linux', 'Requires Linux PID handles and PTY serial transport')
class LinuxExecutionTests(unittest.TestCase):
    setUpAuthorization = authorization.AutomationAuthorizationTests.setUp
    tearDownAuthorization = authorization.AutomationAuthorizationTests.tearDown
    request = authorization.AutomationAuthorizationTests.request

    def setUp(self):
        self.setUpAuthorization()
        self.pump = PseudoPump()
        self.extra_devices = []
        self.preflight_devices={}
        for name,kind in [('peristaltic_2','peristaltic'),('valve','valve')]:
            device=PseudoPump(kind);self.extra_devices.append(device);self.preflight_devices[name]=device
            file=self.lab/'config/templates'/(name+'.json')
            template=json.loads(file.read_text());template['device']['port']=device.port
            if name=='peristaltic_2':template['device']['calibration']['flow_ml_min']=6000
            file.write_text(json.dumps(template))
        file = self.lab / 'config/templates/peristaltic_1.json'
        template = json.loads(file.read_text()); template['device']['port'] = self.pump.port
        file.write_text(json.dumps(template))
        self.sequence = {'preflight':default_preflight(),'schema_version': 2, 'id': 'synthetic_automation', 'description': 'Temporary PTY controller verification',
                         'steps': [{'device': 'peristaltic_1', 'operation': 'inject', 'volume_ml': .4, 'fluid': 'water'}]}

    def tearDown(self):
        try:
            current = self.repo.execution.snapshot()['current']
            if current and current['controller_alive']:
                self.repo.execution.stop(current['run_id'], 'synthetic_admin')
                self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'], timeout=8)
        finally:
            self.pump.close()
            for device in self.extra_devices: device.close()
            self.tearDownAuthorization()

    def full_sequence(self):
        instruments = {'peristaltic_1': self.pump}
        for name, kind in [('peristaltic_2', 'peristaltic'), ('peristaltic_3', 'peristaltic'), ('valve', 'valve'), ('syringe_c', 'syringe')]:
            device = self.preflight_devices.get(name)
            if device is None:
                device = PseudoPump(kind); self.extra_devices.append(device)
            instruments[name] = device
            file = self.lab / 'config/templates' / (name + '.json')
            template = json.loads(file.read_text()); template['device']['port'] = device.port
            if kind == 'syringe': device.serial_number = template['device']['expected_serial_number']
            file.write_text(json.dumps(template))
        templates = self.repo.templates()
        def transfer(name):
            return {'device': name, 'operation': templates[name]['action']['operation'], 'fluid': templates[name]['action']['fluid'], 'volume_ml': 1 if name == 'syringe_c' else .04}
        self.sequence['steps'] = [transfer('peristaltic_1'), {'device': 'valve', 'operation': 'switch_route', 'route': 'waste_1'}, transfer('peristaltic_2'), transfer('syringe_c'), {'device': 'valve', 'operation': 'switch_route', 'route': 'waste_2'}, transfer('peristaltic_2'), transfer('peristaltic_3')]
        return instruments

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value: return value
            time.sleep(.03)
        self.fail('Timed out waiting for the isolated controller')

    def start(self):
        code, prepared = self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})
        self.assertEqual(code, 200)
        self.assertEqual(self.pump.commands, [])
        if prepared['calibration']['required']:
            self.calibrate(prepared)
        self.empty_cell(prepared)
        code, result = self.request('POST', '/api/execution/start', {'run_id': prepared['run_id'], 'version': prepared['version']})
        self.assertEqual(code, 202, result)
        return prepared

    def calibrate(self, prepared):
        code, result = self.request('POST', '/api/execution/calibrate', {'run_id': prepared['run_id'], 'version': prepared['version'], 'confirm_discharge': True})
        self.assertEqual(code, 200, result)
        self.wait_for(lambda: self.repo.execution.preparation_status(prepared['run_id'])['calibration']['state']=='passed')

    def empty_cell(self,prepared,route='waste_1',request_id='synthetic_drain'):
        code,result=self.request('POST','/api/execution/drain',{'run_id':prepared['run_id'],'version':prepared['version'],'route':route,'confirm_discharge':True,'request_id':request_id})
        self.assertEqual(code,200,result)
        self.wait_for(lambda:self.repo.execution.preparation_status(prepared['run_id'])['cell_preparation']['state']=='awaiting_confirmation')
        code,result=self.request('POST','/api/execution/confirm-empty',{'run_id':prepared['run_id'],'version':prepared['version'],'empty_confirmed':True})
        self.assertEqual(code,200,result)
        self.assertEqual(result['cell_preparation']['state'],'passed')

    def test_real_runner_progress_sse_completion_and_duplicate_start(self):
        prepared = self.start()
        code, again = self.request('POST', '/api/execution/start', {'run_id': prepared['run_id'], 'version': prepared['version']})
        self.assertEqual(code, 202); self.assertTrue(again['already_started'])
        self.wait_for(lambda: 0x47 in self.pump.commands)
        token, _ = self.sessions['viewer']
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('GET', '/api/events', headers={'Cookie': COOKIE_NAME + '=' + token})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        frame = None
        for _ in range(10):
            line = response.readline().decode()
            if line.startswith('data: '): frame = json.loads(line[6:]); break
        response.close(); connection.close()
        self.assertIsNotNone(frame)
        self.assertEqual(frame['runtime']['active_step']['device'], 'peristaltic_1')
        self.assertEqual(frame['devices'][0]['activity']['state'], 'executing')
        self.assertTrue(frame['execution']['current']['controller_alive'])
        self.assertFalse(frame['runtime']['active_step']['volume_measured'])
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'completed')
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertEqual(self.pump.commands.count(0x47), 1)
        self.assertIn(0x49, self.pump.commands)
        final = self.repo.status()
        self.assertEqual(final['runtime']['state'], 'completed')
        self.assertEqual(final['runtime']['completed_steps'], 1)
        self.assertIsNone(final['runtime']['active_step'])
        self.assertIsNone(final['motion_commands_sent'])

    def test_function_blocks_execute_in_order_through_the_real_worker_against_only_synthetic_devices(self):
        # These are private PTYs from setUp, never production serial ports.
        action = self.sequence['steps'][0]
        self.sequence = {key: value for key, value in self.sequence.items() if key not in ('steps','preflight')}
        self.sequence.update(schema_version=3, blocks=[default_preflight(),
            {'id': 'first_dose', 'description': 'First synthetic dose', 'steps': [{**action, 'volume_ml': .04}]},
            {'id': 'second_dose', 'description': 'Second synthetic dose', 'steps': [{**action, 'volume_ml': .08}]}])
        prepared = self.start()
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state']=='completed')
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertEqual(self.pump.commands.count(0x47), 2)
        journal = self.repo.logs_dir / ('run-'+prepared['run_id']+'.jsonl')
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        started = [event['step'] for event in events if event['event']=='step_started']
        self.assertEqual([step['id'] for step in started], ['block_002_step_001','block_003_step_001'])
        self.assertEqual([step['volume_ml'] for step in started], [.04,.08])
        self.assertEqual(self.repo.status()['runtime']['completed_steps'], 2)

    def test_admin_stop_cleans_up_and_state_survives_repository_restart(self):
        self.sequence['steps'][0]['volume_ml'] = 10
        prepared = self.start()
        self.wait_for(lambda: 0x47 in self.pump.commands)
        restored = LabRepository(self.lab, self.root / 'data', execution_enabled=True)
        self.assertTrue(restored.execution.snapshot()['current']['can_stop'])
        self.assertEqual(self.request('POST', '/api/execution/stop', {'run_id': prepared['run_id']}, role='viewer')[0], 403)
        with patch.object(restored.device_status, 'refresh') as refresh:
            restored.device_status.policy['enabled'] = True
            self.assertEqual(restored.refresh_status()['scan']['state'], 'skipped')
            refresh.assert_not_called()
        result = restored.execution.stop(prepared['run_id'], 'synthetic_admin')
        self.assertTrue(result['stop_requested'])
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'stopped')
        current = self.repo.execution.snapshot()['current']
        self.assertFalse(current['cleanup_failed'])
        self.assertEqual(current['stop_requested_by'], 'synthetic_admin')
        self.assertIn(0x49, self.pump.commands)
        self.assertFalse(self.pump.busy)
        self.assertEqual(self.repo.status()['runtime']['state'], 'aborted')

    def test_stop_failure_never_reports_safe_completion(self):
        self.sequence['steps'][0]['volume_ml'] = 10
        prepared = self.start()
        self.wait_for(lambda: 0x47 in self.pump.commands)
        self.pump.stop_error = True
        self.request('POST', '/api/execution/stop', {'run_id': prepared['run_id']})
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'failed')
        self.assertTrue(self.repo.execution.snapshot()['current']['cleanup_failed'])
        self.assertTrue(self.pump.busy)
        self.assertNotEqual(self.repo.status()['runtime']['state'], 'completed')

    def test_device_busy_preflight_never_starts_or_stops_existing_motion(self):
        self.pump.busy = True
        self.start()
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'failed')
        self.assertNotIn(0x47, self.pump.commands)
        self.assertNotIn(0x49, self.pump.commands)

    def test_another_prepared_run_and_external_serial_lock_cannot_overlap(self):
        self.sequence['steps'][0]['volume_ml'] = 10
        prepared = self.start()
        self.wait_for(lambda: 0x47 in self.pump.commands)
        _, second = self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})
        self.assertEqual(self.request('POST', '/api/execution/start', {'run_id': second['run_id'], 'version': second['version']})[0], 409)
        self.request('POST', '/api/execution/stop', {'run_id': prepared['run_id']})
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        import fcntl
        with (self.repo.logs_dir / 'execution.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.request('POST', '/api/execution/start', {'run_id': second['run_id'], 'version': second['version']})[0], 409)

    def test_complete_five_device_sequence_and_empty_syringe_aspiration_then_injection(self):
        instruments = self.full_sequence()
        self.start()
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] in ('completed', 'failed'))
        self.assertEqual(self.repo.execution.snapshot()['current']['state'], 'completed')
        self.assertEqual(self.repo.status()['runtime']['completed_steps'], 7)
        self.assertEqual(instruments['valve'].channel, 6)
        self.assertEqual(instruments['peristaltic_2'].commands.count(0x47), 3)
        self.assertEqual(instruments['peristaltic_3'].commands.count(0x48), 1)
        syringe = instruments['syringe_c']
        self.assertEqual(len(syringe.positions), 2)
        self.assertGreater(syringe.positions[0], 0)
        self.assertEqual(syringe.positions[1], 0)
        self.assertEqual(syringe.valve_position, 'o')

    def test_syringe_preflight_failure_prevents_every_pump_from_moving(self):
        instruments = self.full_sequence()
        instruments['syringe_c'].busy = True
        _, prepared = self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})
        code, result = self.request('POST', '/api/execution/calibrate', {'run_id': prepared['run_id'], 'version': prepared['version'], 'confirm_discharge': True})
        self.assertEqual(code, 200, result)
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'failed')
        self.assertEqual(self.request('POST', '/api/execution/start', {'run_id': prepared['run_id'], 'version': prepared['version']})[0], 409)
        for name in ('peristaltic_1', 'peristaltic_2', 'peristaltic_3'):
            self.assertNotIn(0x47, instruments[name].commands)
        self.assertEqual(instruments['syringe_c'].positions, [])

    def test_each_experiment_requires_its_own_homing_and_never_executes_pending_program(self):
        instruments = self.full_sequence()
        syringe = instruments['syringe_c']; syringe.buffer_state = '1'
        _, first = self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})
        self.assertEqual(first['calibration']['state'], 'required')
        self.assertEqual(self.request('POST', '/api/execution/start', {'run_id': first['run_id'], 'version': first['version']})[0], 409)
        self.assertEqual(syringe.commands, [])
        self.assertEqual(self.request('POST', '/api/execution/calibrate', {'run_id': first['run_id'], 'version': first['version'], 'confirm_discharge': False})[0], 422)
        self.empty_cell(first)
        self.calibrate(first)
        self.assertEqual(self.repo.execution.preparation_status(first['run_id'])['cell_preparation']['state'],'invalidated')
        self.assertEqual(self.request('POST','/api/execution/start',{'run_id':first['run_id'],'version':first['version']})[0],409)
        self.assertEqual(syringe.homing_count, 1)
        self.assertNotIn('R', syringe.commands); self.assertNotIn('zR', syringe.commands)
        self.assertEqual(syringe.positions, [])
        self.assertEqual(self.pump.commands, [])
        code, duplicate = self.request('POST', '/api/execution/calibrate', {'run_id': first['run_id'], 'version': first['version'], 'confirm_discharge': True})
        self.assertEqual(code, 200); self.assertTrue(duplicate['already_started'])
        self.assertEqual(syringe.homing_count, 1)
        _, second = self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})
        self.assertEqual(second['calibration']['state'], 'required')
        self.assertEqual(self.request('POST', '/api/execution/start', {'run_id': second['run_id'], 'version': second['version']})[0], 409)
        self.calibrate(second)
        self.assertEqual(self.repo.execution.preparation_status(first['run_id'])['calibration']['state'], 'invalidated')
        self.assertEqual(self.request('POST', '/api/execution/start', {'run_id': first['run_id'], 'version': first['version']})[0], 409)
        self.empty_cell(second)
        self.assertEqual(self.request('POST', '/api/execution/start', {'run_id': second['run_id'], 'version': second['version']})[0], 202)
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state']=='completed')
        self.assertEqual(syringe.homing_count, 2)
        self.assertEqual(self.repo.execution.preparation_status(second['run_id'])['calibration']['state'], 'consumed')

    def test_failed_homing_cannot_be_used_as_a_start_certificate(self):
        instruments = self.full_sequence(); instruments['syringe_c'].homing_error = 7
        _, prepared = self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})
        self.request('POST', '/api/execution/calibrate', {'run_id': prepared['run_id'], 'version': prepared['version'], 'confirm_discharge': True})
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertEqual(self.repo.execution.preparation_status(prepared['run_id'])['calibration']['state'], 'failed')
        self.assertEqual(self.request('POST', '/api/execution/start', {'run_id': prepared['run_id'], 'version': prepared['version']})[0], 409)
        for name in ('peristaltic_1','peristaltic_2','peristaltic_3'):self.assertEqual(instruments[name].commands, [])

    def test_drain_alone_never_authorizes_start_and_duplicate_requests_do_not_repeat_motion(self):
        self.sequence['preflight']['steps'][2]['route']='waste_2'
        _,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        body={'run_id':prepared['run_id'],'version':prepared['version'],'route':'waste_2','confirm_discharge':True,'request_id':'unique_drain_one'}
        self.assertEqual(self.request('POST','/api/execution/confirm-empty',{'run_id':prepared['run_id'],'version':prepared['version'],'empty_confirmed':True})[0],409)
        self.assertEqual(self.request('POST','/api/execution/drain',body)[0],200)
        self.wait_for(lambda:self.repo.execution.preparation_status(prepared['run_id'])['cell_preparation']['state']=='awaiting_confirmation')
        pump=self.preflight_devices['peristaltic_2']
        self.assertEqual(pump.commands.count(0x47),1)
        code,again=self.request('POST','/api/execution/drain',body)
        self.assertEqual(code,200);self.assertTrue(again['already_started'])
        self.assertEqual(pump.commands.count(0x47),1)
        self.assertEqual(self.pump.commands,[])
        self.assertEqual(self.request('POST','/api/execution/start',{'run_id':prepared['run_id'],'version':prepared['version']})[0],409)
        self.assertEqual(self.request('POST','/api/execution/confirm-empty',{'run_id':prepared['run_id'],'version':prepared['version'],'empty_confirmed':False})[0],422)
        self.assertEqual(self.request('POST','/api/execution/confirm-empty',{'run_id':prepared['run_id'],'version':prepared['version'],'empty_confirmed':True})[0],200)
        self.assertEqual(self.request('POST','/api/execution/start',{'run_id':prepared['run_id'],'version':prepared['version']})[0],202)

    def test_drain_fault_blocks_the_experiment_and_cannot_be_confirmed_empty(self):
        self.preflight_devices['peristaltic_2'].stop_error=True
        _,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.request('POST','/api/execution/drain',{'run_id':prepared['run_id'],'version':prepared['version'],'route':'waste_1','confirm_discharge':True,'request_id':'failing_drain'})
        self.wait_for(lambda:not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertEqual(self.repo.execution.preparation_status(prepared['run_id'])['cell_preparation']['state'],'failed')
        self.assertEqual(self.request('POST','/api/execution/confirm-empty',{'run_id':prepared['run_id'],'version':prepared['version'],'empty_confirmed':True})[0],409)
        self.assertEqual(self.request('POST','/api/execution/start',{'run_id':prepared['run_id'],'version':prepared['version']})[0],409)
        self.assertEqual(self.pump.commands,[])

    def test_preflight_workflow_homes_drains_pauses_for_operator_and_then_runs_once(self):
        instruments=self.full_sequence()
        code,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.assertEqual(code,200,prepared)
        body={'run_id':prepared['run_id'],'version':prepared['version'],'confirm_discharge':True,'empty_confirmed':False}
        self.assertEqual(self.request('POST','/api/execution/advance',body)[0],200)
        self.wait_for(lambda:self.repo.execution.preparation_status(prepared['run_id'])['calibration']['state']=='passed')
        self.assertEqual(instruments['syringe_c'].homing_count,1)
        for _ in range(3):self.request('GET','/api/execution/preparations/'+prepared['run_id'])
        self.assertEqual(instruments['peristaltic_2'].commands,[])
        restored=LabRepository(self.lab,self.root/'data',execution_enabled=True)
        self.assertEqual(restored.execution.preparation_status(prepared['run_id'])['workflow']['phase'],'ready_to_drain')
        body['confirm_discharge']=False
        self.assertEqual(self.request('POST','/api/execution/advance',body)[0],200)
        self.wait_for(lambda:self.repo.execution.preparation_status(prepared['run_id'])['cell_preparation']['state']=='awaiting_confirmation')
        self.assertEqual(self.pump.commands,[])
        self.assertEqual(self.request('POST','/api/execution/advance',body)[0],200)
        self.assertEqual(instruments['peristaltic_2'].commands.count(0x47),1)
        body['empty_confirmed']=True
        self.assertEqual(self.request('POST','/api/execution/advance',body)[0],200)
        self.wait_for(lambda:self.repo.execution.snapshot()['current']['state']=='completed')
        self.wait_for(lambda:not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertEqual(self.pump.commands.count(0x47),1)
        self.assertEqual(instruments['syringe_c'].homing_count,1)
        self.assertEqual(self.request('POST','/api/execution/advance',body)[0],200)
        self.assertEqual(self.pump.commands.count(0x47),1)

    def test_failed_workflow_homing_stops_the_block_and_never_starts_drain_or_experiment(self):
        instruments=self.full_sequence();instruments['syringe_c'].homing_error=7
        code,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.assertEqual(code,200,prepared)
        body={'run_id':prepared['run_id'],'version':prepared['version'],'confirm_discharge':True,'empty_confirmed':False}
        self.assertEqual(self.request('POST','/api/execution/advance',body)[0],200)
        self.wait_for(lambda:self.repo.execution.preparation_status(prepared['run_id'])['workflow']['phase']=='failed')
        self.assertEqual(self.request('POST','/api/execution/advance',body)[0],409)
        self.assertEqual(instruments['syringe_c'].homing_count,1)
        for name in ('peristaltic_1','peristaltic_2','peristaltic_3'):
            self.assertEqual(instruments[name].commands,[])
        self.assertEqual(instruments['valve'].channel,5)
