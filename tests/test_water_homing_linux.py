"""Water-before-homing is exercised only against isolated pseudo-terminal devices."""
import json
import sys
import unittest

import test_execution_linux as base
import test_inlet_execution_linux as inlet
from pump_console.preflight import default_preflight
from pump_console.topology import INLET


@unittest.skipUnless(sys.platform=='linux','Requires Linux PTY controller integration')
class WaterHomingTests(unittest.TestCase):
    setUpAuthorization=base.LinuxExecutionTests.setUpAuthorization
    tearDownAuthorization=base.LinuxExecutionTests.tearDownAuthorization
    request=base.LinuxExecutionTests.request
    wait_for=base.LinuxExecutionTests.wait_for
    add_syringe=inlet.InletExecutionTests.add_syringe

    def setUp(self):
        base.LinuxExecutionTests.setUp(self)
        self.repo.topology=INLET
        self.sequence['preflight']=default_preflight('waste')
        self.syringe=self.add_syringe()
        self.valve=self.preflight_devices['valve'];self.valve.channel=6
        self.sequence['steps']=[dict(device='valve',operation='switch_route',route='electrolyte'),
            dict(device='syringe_c',operation='inject',fluid='electrolyte',volume_ml=.1)]

    def tearDown(self):base.LinuxExecutionTests.tearDown(self)

    def prepare(self):
        code,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.assertEqual(code,200,prepared);return prepared

    def calibrate(self,prepared):
        code,result=self.request('POST','/api/execution/calibrate',{'run_id':prepared['run_id'],'version':prepared['version'],'confirm_discharge':True})
        self.assertEqual(code,200,result)
        self.wait_for(lambda:not self.repo.execution.snapshot()['current']['controller_alive'])
        return self.repo.execution.preparation_status(prepared['run_id'])['calibration']

    def test_electrolyte_channel_is_replaced_by_verified_water_before_any_zr(self):
        prepared=self.prepare();calibration=self.calibrate(prepared)
        self.assertEqual(calibration['state'],'passed');self.assertTrue(calibration['water_source_verified'])
        self.assertEqual(calibration['water_channel'],5);self.assertEqual(self.valve.channel,5)
        self.assertEqual(self.valve.commands.count(0x44),1);self.assertEqual(self.syringe.homing_count,1)
        self.assertNotIn('R',self.syringe.commands)
        child=self.repo.execution.snapshot()['current']['run_id']
        events=[json.loads(line) for line in (self.repo.logs_dir/('run-'+child+'.jsonl')).read_text().splitlines()]
        self.assertEqual([r['step']['device'] for r in events if r['event']=='step_started'],['valve','syringe_c'])
        kinds=[r['event'] for r in events]
        self.assertLess(kinds.index('syringe_water_source_verified'),kinds.index('syringe_initialization_started'))
        self.assertEqual(self.pump.commands,[]);self.assertEqual(self.preflight_devices['peristaltic_2'].commands,[])

    def test_failed_water_readback_or_busy_valve_prevents_zr_and_main_workflow(self):
        self.valve.busy=True
        prepared=self.prepare();calibration=self.calibrate(prepared)
        self.assertEqual(calibration['state'],'failed');self.assertFalse(calibration['water_source_verified'])
        self.assertEqual(self.syringe.homing_count,0);self.assertNotIn('ZR',self.syringe.commands)
        self.assertNotIn(0x44,self.valve.commands)
        code,_=self.request('POST','/api/execution/start',{'run_id':prepared['run_id'],'version':prepared['version']})
        self.assertEqual(code,409)

    def test_missing_water_port_prevents_launch_and_syringe_commands(self):
        file=self.lab/'config/templates/valve.json';value=json.loads(file.read_text());value['device']['port']='/dev/missing-water-valve';file.write_text(json.dumps(value))
        prepared=self.prepare()
        code,_=self.request('POST','/api/execution/calibrate',{'run_id':prepared['run_id'],'version':prepared['version'],'confirm_discharge':True})
        self.assertEqual(code,422);self.assertEqual(self.syringe.commands,[])

    def test_switch_acknowledgement_without_channel_five_readback_never_authorizes_zr(self):
        self.valve.ignore_switch=True
        file=self.lab/'config/templates/valve.json';value=json.loads(file.read_text())
        value['device'].update(completion_timeout_s=.2,poll_interval_s=.05);file.write_text(json.dumps(value))
        prepared=self.prepare();calibration=self.calibrate(prepared)
        self.assertEqual(calibration['state'],'failed');self.assertFalse(calibration['water_source_verified'])
        self.assertEqual(self.valve.channel,6);self.assertIn(0x44,self.valve.commands)
        self.assertEqual(self.syringe.homing_count,0);self.assertNotIn('ZR',self.syringe.commands)

    def test_no_syringe_skips_water_selection_and_homing(self):
        self.sequence['steps']=[dict(device='peristaltic_1',operation='inject',fluid='water',volume_ml=.1)]
        prepared=self.prepare()
        self.assertFalse(prepared['calibration']['required'])
        self.assertEqual(self.valve.commands,[]);self.assertEqual(self.syringe.commands,[])


if __name__=='__main__':unittest.main()
