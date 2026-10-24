"""Real web worker, pump/CV ordering, live data and stop using PTYs only."""
import http.client
import json
import os
import select
import struct
import sys
import threading
import time
import tty
import unittest

import test_execution_linux as linux_test
from test_ec110 import data_frame
from pump_console.auth import COOKIE_NAME
from pump_console.ec110 import frame_bytes, station_policy
from pump_console.measurement import measurement_templates


class PseudoStation:
    def __init__(self):
        self.master, self.slave = os.openpty(); tty.setraw(self.slave)
        self.port = os.ttyname(self.slave)
        self.commands = []
        self.closed = threading.Event(); self.active = threading.Event()
        self.bad_echo = False
        self.thread = threading.Thread(target=self.serve, daemon=True); self.thread.start()

    def emit(self):
        for delay, code in [(.12, 27326), (2.0, -27327), (.12, 0)]:
            if self.closed.wait(delay) or not self.active.is_set():
                return
            try: os.write(self.master, data_frame((code,)))
            except OSError: return

    def serve(self):
        pending = bytearray()
        while not self.closed.is_set():
            if not select.select([self.master], [], [], .05)[0]: continue
            try:
                pending.extend(os.read(self.master, 4096))
                while len(pending) >= 9:
                    if pending[:2] != b'\x55\xaa': del pending[0]; continue
                    length = int.from_bytes(pending[5:7], 'big'); total = length + 9
                    if len(pending) < total: break
                    frame = bytes(pending[:total]); del pending[:total]
                    cmd, seq, payload = frame[4], frame[3], frame[7:-2]
                    self.commands.append((cmd, payload))
                    if cmd == 3:
                        os.write(self.master, frame_bytes(4, b'{"ProductType":"EC-110 Pro V1","FirmwareVersion":"synthetic"}', seq))
                    elif cmd == 5:
                        echo = b'\0' * 4 + payload
                        os.write(self.master, frame_bytes(6, echo[:-4] if self.bad_echo else echo, seq))
                    elif cmd == 0x11:
                        mode, state = struct.unpack_from('>2i', payload)
                        os.write(self.master, frame_bytes(0x12, b'\0' * 4 + payload, seq))
                        if state == 2:
                            self.active.set(); threading.Thread(target=self.emit, daemon=True).start()
                        else: self.active.clear()
            except OSError: return

    def close(self):
        self.closed.set(); self.active.clear(); self.thread.join(timeout=2)
        os.close(self.master); os.close(self.slave)


@unittest.skipUnless(sys.platform == 'linux', 'Linux PTY and PID identity integration')
class CVExecutionTests(unittest.TestCase):
    setUpAuthorization = linux_test.LinuxExecutionTests.setUpAuthorization
    tearDownAuthorization = linux_test.LinuxExecutionTests.tearDownAuthorization
    request = linux_test.LinuxExecutionTests.request
    wait_for = linux_test.LinuxExecutionTests.wait_for
    start = linux_test.LinuxExecutionTests.start
    empty_cell = linux_test.LinuxExecutionTests.empty_cell
    calibrate = linux_test.LinuxExecutionTests.calibrate

    def setUp(self):
        linux_test.LinuxExecutionTests.setUp(self)
        self.station = PseudoStation()
        self.repo.station = station_policy(dict(enabled=True, port=self.station.port))
        parameters = measurement_templates(self.repo.station)['electrochemical_station']['action']['default_parameters']
        self.sequence['steps'][0]['volume_ml'] = .02
        self.sequence['steps'] += [dict(device='electrochemical_station', operation='measure',
            technique='cyclic_voltammetry', parameters=parameters),
            dict(device='peristaltic_1', operation='inject', volume_ml=.02, fluid='water')]

    def tearDown(self):
        try: linux_test.LinuxExecutionTests.tearDown(self)
        finally: self.station.close()

    def test_mixed_real_worker_publishes_live_cv_and_archives_full_parameters(self):
        prepared = self.start()
        self.wait_for(lambda: self.repo.measurements.latest() and self.repo.measurements.latest()['metadata']['point_count'] >= 1)
        status = self.repo.status()
        self.assertEqual(status['runtime']['active_step']['device'], 'electrochemical_station')
        self.assertEqual(status['measurement']['metadata']['state'], 'running')
        token, _ = self.sessions['viewer']
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('GET', '/api/events', headers={'Cookie': COOKIE_NAME + '=' + token})
        response = connection.getresponse()
        payload = None
        for _ in range(12):
            line = response.readline().decode()
            if line.startswith('data: '): payload = json.loads(line[6:]); break
        response.close(); connection.close()
        self.assertIsNotNone(payload['measurement'])
        self.assertTrue(payload['measurement']['points'])
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'completed')
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        final = self.repo.measurements.latest()['metadata']
        self.assertEqual(final['point_count'], 3); self.assertEqual(final['sample_count'], 300)
        self.assertTrue(final['stop_confirmed'])
        self.assertEqual(self.pump.commands.count(0x47), 2)
        self.assertEqual(final['encoded_parameters']['sweep_segments'], 3)
        self.assertEqual(self.request('GET', '/api/measurements', role='viewer')[1]['total'], 1)
        self.assertEqual(self.request('GET', '/api/measurements/' + final['id'], role=None)[0], 401)

    def test_stop_during_cv_retains_partial_data_and_does_not_run_following_pump(self):
        prepared = self.start()
        self.wait_for(lambda: self.repo.measurements.latest() and self.repo.measurements.latest()['metadata']['point_count'] >= 1)
        self.request('POST', '/api/execution/stop', {'run_id': prepared['run_id']})
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        measurement = self.repo.measurements.latest()['metadata']
        self.assertEqual(measurement['state'], 'stopped'); self.assertTrue(measurement['stop_confirmed'])
        self.assertGreaterEqual(measurement['point_count'], 1)
        self.assertLess(measurement['point_count'], 3)
        self.assertEqual(self.pump.commands.count(0x47), 1)
        self.assertFalse(self.station.active.is_set())

    def test_configuration_echo_failure_never_starts_cv_or_following_pump(self):
        self.station.bad_echo = True
        self.start()
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'failed')
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertFalse(any(cmd == 0x11 and struct.unpack_from('>2i', payload) == (1, 2)
                             for cmd, payload in self.station.commands))
        self.assertEqual(self.repo.measurements.latest()['metadata']['state'], 'failed')
        self.assertEqual(self.pump.commands.count(0x47), 1)

    def test_station_profile_change_invalidates_preparation_without_commands(self):
        code, prepared = self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})
        self.assertEqual(code, 200)
        self.repo.station['dac_reference_mv'] = 121
        code, error = self.request('POST', '/api/execution/start', {'run_id': prepared['run_id'], 'version': prepared['version']})
        self.assertEqual(code, 409); self.assertEqual(error['error'], 'station_configuration_changed')
        self.assertEqual(self.station.commands, [])


if __name__ == '__main__': unittest.main()
