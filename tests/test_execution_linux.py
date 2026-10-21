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


class PseudoPump:
    def __init__(self, kind='peristaltic'):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.port = os.ttyname(self.slave)
        self.busy = False
        self.kind, self.channel, self.position, self.valve_position = kind, 5, 0, 'i'
        self.positions = []
        self.serial_number = 'SYNTHETIC-SYRINGE'
        self.stop_error = False
        self.commands = []
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
                        payload = ''
                        if command == '?': payload = str(self.position)
                        elif command == '?10': payload = '0'
                        elif command == '?202': payload = self.serial_number
                        elif command == '?6': payload = self.valve_position
                        elif command == 'IR': self.valve_position = 'i'
                        elif command == 'OR': self.valve_position = 'o'
                        elif re.fullmatch(r'v[0-9]+V[0-9]+A[0-9]+R', command):
                            self.position = int(re.search(r'A([0-9]+)', command).group(1)); self.positions.append(self.position)
                        status = 0x40 if self.busy else 0x60
                        os.write(self.master, b'/0' + bytes([status]) + payload.encode() + b'\x03\r\n')
                    continue
                while len(pending) >= 8:
                    packet, pending = pending[:8], pending[8:]
                    if packet[0] != 0xCC or packet[5] != 0xDD or sum(packet[:6]) != int.from_bytes(packet[6:], 'little'):
                        continue
                    function = packet[2]; self.commands.append(function)
                    if function in (0x47, 0x48): self.busy = True
                    if function == 0x49 and not self.stop_error: self.busy = False
                    if function == 0x44: self.channel = int.from_bytes(packet[3:5], 'little')
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
        file = self.lab / 'config/templates/peristaltic_1.json'
        template = json.loads(file.read_text()); template['device']['port'] = self.pump.port
        file.write_text(json.dumps(template))
        self.sequence = {'schema_version': 2, 'id': 'synthetic_automation', 'description': 'Temporary PTY controller verification',
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
            device = PseudoPump(kind); instruments[name] = device; self.extra_devices.append(device)
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
        code, result = self.request('POST', '/api/execution/start', {'run_id': prepared['run_id'], 'version': prepared['version']})
        self.assertEqual(code, 202, result)
        return prepared

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
        self.assertEqual(instruments['peristaltic_2'].commands.count(0x47), 2)
        self.assertEqual(instruments['peristaltic_3'].commands.count(0x48), 1)
        syringe = instruments['syringe_c']
        self.assertEqual(len(syringe.positions), 2)
        self.assertGreater(syringe.positions[0], 0)
        self.assertEqual(syringe.positions[1], 0)
        self.assertEqual(syringe.valve_position, 'o')

    def test_syringe_preflight_failure_prevents_every_pump_from_moving(self):
        instruments = self.full_sequence()
        instruments['syringe_c'].busy = True
        self.start()
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'failed')
        for name in ('peristaltic_1', 'peristaltic_2', 'peristaltic_3'):
            self.assertNotIn(0x47, instruments[name].commands)
        self.assertEqual(instruments['syringe_c'].positions, [])
