"""Actual web worker/native driver tests with PTYs only; no liquid is moved."""
import json
import sys
import unittest

import test_execution_linux as base
import test_inlet_execution_linux as inlet
from pump_console.topology import INLET
from pump_console.preflight import default_preflight


@unittest.skipUnless(sys.platform=='linux','Requires isolated Linux PTYs')
class SyringeBatchExecutionTests(unittest.TestCase):
    setUpAuthorization=base.LinuxExecutionTests.setUpAuthorization
    tearDownAuthorization=base.LinuxExecutionTests.tearDownAuthorization
    request=base.LinuxExecutionTests.request
    wait_for=base.LinuxExecutionTests.wait_for
    add_syringe=inlet.InletExecutionTests.add_syringe

    def setUp(self):
        base.LinuxExecutionTests.setUp(self);self.repo.topology=INLET
        self.syringe=self.add_syringe();self.valve=self.preflight_devices['valve']
        self.sequence=dict(schema_version=3,mode='debug',initial_cell_volume_ml=0,id='batch_debug',description='Synthetic bounded syringe strokes',blocks=[
            dict(id='dose',description='Thirty mL in two strokes',steps=[dict(device='valve',operation='switch_route',route='electrolyte'),
            dict(device='syringe_c',operation='inject',fluid='electrolyte',volume_ml=30)])])

    def tearDown(self):base.LinuxExecutionTests.tearDown(self)

    def start_debug(self):
        code,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.assertEqual(code,200,prepared)
        code,result=self.request('POST','/api/execution/debug-start',dict(run_id=prepared['run_id'],version=prepared['version'],confirm_setup=True,confirm_homing_discharge=False))
        self.assertEqual(code,202,result);return prepared

    def finished(self,state):
        self.wait_for(lambda:self.repo.execution.snapshot()['current']['state']==state)
        self.wait_for(lambda:not self.repo.execution.snapshot()['current']['controller_alive'])

    def events(self,prepared):
        return [json.loads(line) for line in (self.repo.logs_dir/('run-'+prepared['run_id']+'.jsonl')).read_text().splitlines()]

    def test_thirty_ml_runs_twenty_then_ten_with_repeated_inlet_checks_and_one_logical_step(self):
        prepared=self.start_debug();self.finished('completed')
        self.assertEqual(self.syringe.positions,[9600,0,4800,0]);self.assertEqual(self.syringe.homing_count,0)
        self.assertEqual(self.syringe.commands.count('IR'),2);self.assertEqual(self.syringe.commands.count('OR'),2)
        self.assertNotIn('R',self.syringe.commands)
        events=self.events(prepared)
        self.assertEqual([r['stroke_volume_ml'] for r in events if r['event']=='syringe_stroke_completed'],[20,10])
        self.assertEqual([r['completed_nominal_ml'] for r in events if r['event']=='syringe_stroke_completed'],[20,30])
        self.assertEqual(len([r for r in events if r['event']=='step_started']),2)
        self.assertEqual(len([r for r in events if r['event']=='valve_position_verified' and r.get('source')=='syringe_inlet_precheck']),2)

    def test_second_stroke_source_mismatch_preserves_first_dose_and_prevents_second_aspiration(self):
        self.syringe.after_move=lambda target:setattr(self.valve,'channel',5) if target==0 else None
        prepared=self.start_debug();self.finished('failed')
        self.assertEqual(self.syringe.positions,[9600,0])
        self.assertEqual(len([r for r in self.events(prepared) if r['event']=='syringe_stroke_completed']),1)

    def test_stop_during_second_stroke_reports_progress_and_does_not_dispense_it(self):
        self.syringe.pause_target=4800;prepared=self.start_debug()
        self.wait_for(lambda:self.syringe.positions==[9600,0,4800])
        status=self.repo.status()['runtime']['active_step']
        self.assertEqual(status['stroke_index'],2);self.assertEqual(status['stroke_count'],2)
        self.assertEqual(status['completed_nominal_ml'],20);self.assertEqual(status['volume_ml'],30)
        self.assertEqual(self.request('POST','/api/execution/stop',{'run_id':prepared['run_id']})[0],200)
        self.finished('stopped');self.assertIn('T',self.syringe.commands)
        self.assertEqual(self.syringe.positions,[9600,0,4800])

    def test_second_move_error_never_continues_or_retries_a_completed_first_stroke(self):
        self.syringe.fail_target=4800;prepared=self.start_debug();self.finished('failed')
        self.assertEqual(self.syringe.positions,[9600,0,4800]);self.assertEqual(self.syringe.commands.count('IR'),2)
        failures=[r for r in self.events(prepared) if r['event']=='syringe_stroke_failed']
        self.assertEqual(failures[0]['completed_nominal_ml'],20)
        self.assertTrue(failures[0]['current_stroke_volume_unknown'])

    def test_formal_experiment_keeps_verified_water_homing_and_one_empty_cell_preparation(self):
        self.sequence.pop('mode');self.sequence.pop('initial_cell_volume_ml')
        self.sequence['blocks'].insert(0,default_preflight('waste'))
        code,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.assertEqual(code,200,prepared)
        base.LinuxExecutionTests.calibrate(self,prepared)
        base.LinuxExecutionTests.empty_cell(self,prepared,route='waste')
        code,result=self.request('POST','/api/execution/start',dict(run_id=prepared['run_id'],version=prepared['version']))
        self.assertEqual(code,202,result);self.finished('completed')
        self.assertEqual(self.syringe.homing_count,1);self.assertEqual(self.syringe.positions,[9600,0,4800,0])
        self.assertEqual(self.preflight_devices['peristaltic_2'].commands.count(0x47),1)


if __name__=='__main__':unittest.main()
