"""New physical topology verified with isolated PTY pumps and selector only."""
import json
import sys
import unittest

import test_execution_linux as base
from pump_console.preflight import default_preflight
from pump_console.topology import INLET


@unittest.skipUnless(sys.platform == 'linux', 'Linux controller and PTY integration')
class InletExecutionTests(unittest.TestCase):
    setUpAuthorization = base.LinuxExecutionTests.setUpAuthorization
    tearDownAuthorization = base.LinuxExecutionTests.tearDownAuthorization
    request = base.LinuxExecutionTests.request
    wait_for = base.LinuxExecutionTests.wait_for
    start = base.LinuxExecutionTests.start
    calibrate = base.LinuxExecutionTests.calibrate

    def setUp(self):
        base.LinuxExecutionTests.setUp(self)
        self.repo.topology = INLET
        self.sequence['preflight'] = default_preflight('waste')

    def tearDown(self):
        base.LinuxExecutionTests.tearDown(self)

    def empty_cell(self, prepared, route='waste', request_id='direct_drain'):
        base.LinuxExecutionTests.empty_cell(self, prepared, route, request_id)
        self.assertEqual(self.preflight_devices['valve'].commands, [],
                         'Direct startup drain must not even open the inlet selector')

    def add_syringe(self):
        syringe = base.PseudoPump('syringe'); self.extra_devices.append(syringe)
        path = self.lab / 'config/templates/syringe_c.json'
        template = json.loads(path.read_text()); template['device']['port'] = syringe.port
        syringe.serial_number = template['device']['expected_serial_number']
        path.write_text(json.dumps(template))
        return syringe

    def test_startup_and_main_drain_never_operate_inlet_valve(self):
        self.sequence['steps'] = [dict(device='peristaltic_2', operation='withdraw', fluid='waste_liquid', volume_ml=.04)]
        self.start()
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'completed')
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertEqual(self.preflight_devices['valve'].commands, [])
        self.assertEqual(self.preflight_devices['peristaltic_2'].commands.count(0x47), 2)

    def test_syringe_can_take_water_five_then_electrolyte_six_before_direct_drain(self):
        syringe = self.add_syringe()
        self.sequence['steps'] = [dict(device='valve', operation='switch_route', route='water'),
            dict(device='syringe_c', operation='inject', fluid='water', volume_ml=.1),
            dict(device='valve', operation='switch_route', route='electrolyte'),
            dict(device='syringe_c', operation='inject', fluid='electrolyte', volume_ml=.1),
            dict(device='peristaltic_2', operation='withdraw', fluid='waste_liquid', volume_ml=.2)]
        prepared = self.start()
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'completed')
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertEqual(syringe.homing_count, 1)
        self.assertEqual(syringe.positions, [48, 0, 48, 0])
        valve = self.preflight_devices['valve']
        self.assertEqual(valve.channel, 6); self.assertEqual(valve.commands.count(0x44), 1)
        self.assertEqual(self.preflight_devices['peristaltic_2'].commands.count(0x47), 2)
        events = [json.loads(line) for line in (self.repo.logs_dir / ('run-' + prepared['run_id'] + '.jsonl')).read_text().splitlines()]
        steps = [event['step'] for event in events if event['event'] == 'step_started']
        self.assertEqual([step['requires_source']['channel'] for step in steps if step['action'] == 'syringe_transfer'], [5, 6])
        self.assertEqual(steps[-1]['action'], 'drain_direct')

    def test_obsolete_waste_selector_is_rejected_before_startup_drain(self):
        self.sequence['steps'] = [dict(device='valve', operation='switch_route', route='waste_1'),
            dict(device='peristaltic_2', operation='withdraw', fluid='waste_liquid', volume_ml=.1)]
        self.assertEqual(self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})[0], 422)
        self.assertEqual(self.preflight_devices['valve'].commands, [])
        self.assertEqual(self.preflight_devices['peristaltic_2'].commands, [])

    def test_busy_source_valve_blocks_syringe_aspiration(self):
        syringe = self.add_syringe(); self.preflight_devices['valve'].busy = True
        self.sequence['steps'] = [dict(device='valve', operation='switch_route', route='electrolyte'),
            dict(device='syringe_c', operation='inject', fluid='electrolyte', volume_ml=.1)]
        self.start()
        self.wait_for(lambda: self.repo.execution.snapshot()['current']['state'] == 'failed')
        self.wait_for(lambda: not self.repo.execution.snapshot()['current']['controller_alive'])
        self.assertEqual(syringe.positions, [])
        self.assertNotIn(0x44, self.preflight_devices['valve'].commands)


if __name__ == '__main__': unittest.main()
