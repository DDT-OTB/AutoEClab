"""Full debug worker and API tests use synthetic PTYs, never laboratory devices."""
import json
import sys
import unittest

import test_execution_linux as base
from test_cv_execution_linux import PseudoStation
import test_inlet_execution_linux as inlet
from test_debug_workflows import debug_sequence
from pump_console.ec110 import station_policy
from pump_console.measurement import measurement_templates
from pump_console.preflight import default_preflight
from pump_console.topology import INLET


@unittest.skipUnless(sys.platform=='linux','Requires isolated Linux PTYs and PID handles')
class DebugExecutionTests(unittest.TestCase):
    setUpAuthorization=base.LinuxExecutionTests.setUpAuthorization
    tearDownAuthorization=base.LinuxExecutionTests.tearDownAuthorization
    request=base.LinuxExecutionTests.request
    wait_for=base.LinuxExecutionTests.wait_for
    add_syringe=inlet.InletExecutionTests.add_syringe

    def setUp(self):
        base.LinuxExecutionTests.setUp(self)
        self.repo.topology=INLET
        self.sequence=debug_sequence()

    def tearDown(self):base.LinuxExecutionTests.tearDown(self)

    def start_debug(self,homing=False):
        code,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.assertEqual(code,200,prepared)
        self.assertEqual(prepared['operation'],'debug_sequence')
        self.assertEqual(self.pump.commands,[])
        code,result=self.request('POST','/api/execution/debug-start',dict(run_id=prepared['run_id'],version=prepared['version'],confirm_setup=True,confirm_homing_discharge=homing))
        self.assertEqual(code,202,result)
        return prepared

    def completed(self,state='completed'):
        self.wait_for(lambda:self.repo.execution.snapshot()['current']['state']==state)
        self.wait_for(lambda:not self.repo.execution.snapshot()['current']['controller_alive'])

    def test_multiple_blocks_execute_in_order_once_without_implicit_prep(self):
        prepared=self.start_debug()
        code,again=self.request('POST','/api/execution/debug-start',dict(run_id=prepared['run_id'],version=prepared['version'],confirm_setup=True,confirm_homing_discharge=False))
        self.assertEqual(code,202,again);self.assertTrue(again['already_started'])
        self.completed()
        self.assertEqual(self.pump.commands.count(0x47),1)
        self.assertEqual(self.preflight_devices['peristaltic_2'].commands.count(0x47),1,'Only the explicit withdrawal should run')
        self.assertEqual(self.preflight_devices['valve'].commands,[])
        events=[json.loads(line) for line in (self.repo.logs_dir/('run-'+prepared['run_id']+'.jsonl')).read_text().splitlines()]
        self.assertEqual([row['step']['id'] for row in events if row['event']=='step_started'],['block_001_step_001','block_002_step_001'])
        self.assertEqual(self.repo.status()['runtime']['completed_steps'],2)
        progress=self.repo.execution.preparation_status(prepared['run_id'])
        self.assertTrue(progress['workflow']['main_started']);self.assertFalse(progress['cell_preparation']['required'])

    def test_syringe_current_source_is_read_back_without_auto_switch_or_homing(self):
        syringe=self.add_syringe()
        self.sequence['blocks']=[dict(id='transfer',description='Current inlet transfer',steps=[dict(device='syringe_c',operation='inject',fluid='water',volume_ml=.1)])]
        self.start_debug();self.completed()
        self.assertEqual(syringe.homing_count,0);self.assertEqual(syringe.positions,[48,0])
        self.assertNotIn(0x44,self.preflight_devices['valve'].commands)
        self.assertIn(0x3E,self.preflight_devices['valve'].commands)
        self.assertEqual(self.pump.commands,[]);self.assertEqual(self.preflight_devices['peristaltic_2'].commands,[])

    def test_error_seven_never_auto_homes_a_transfer_but_explicit_homing_can_recover(self):
        syringe=self.add_syringe();syringe.initial_error=7
        self.sequence['blocks']=[dict(id='transfer',description='Unhomed syringe transfer',steps=[dict(device='syringe_c',operation='inject',fluid='water',volume_ml=.1)])]
        self.start_debug();self.completed('failed')
        self.assertEqual(syringe.homing_count,0);self.assertEqual(syringe.positions,[]);self.assertNotIn('R',syringe.commands)
        self.sequence['blocks'].insert(0,dict(id='home',description='Explicit recovery',steps=[dict(device='syringe_c',operation='initialize')]))
        self.start_debug(homing=True);self.completed()
        self.assertEqual(syringe.homing_count,1);self.assertEqual(syringe.positions,[48,0]);self.assertNotIn('R',syringe.commands)

    def test_wrong_actual_inlet_stops_before_aspiration(self):
        syringe=self.add_syringe()
        self.sequence['blocks']=[dict(id='transfer',description='Mismatched inlet',steps=[dict(device='syringe_c',operation='inject',fluid='electrolyte',volume_ml=.1)])]
        self.start_debug();self.completed('failed')
        self.assertEqual(syringe.positions,[]);self.assertEqual(syringe.homing_count,0)
        self.assertNotIn(0x44,self.preflight_devices['valve'].commands)

    def test_explicit_homing_then_transfer_reuses_connection_and_does_not_certify_formal_prep(self):
        syringe=self.add_syringe()
        self.sequence['blocks']=[dict(id='home',description='Explicit homing',steps=[dict(device='syringe_c',operation='initialize')]),
            dict(id='transfer',description='After homing',steps=[dict(device='syringe_c',operation='inject',fluid='water',volume_ml=.1)])]
        self.start_debug(homing=True);self.completed()
        self.assertEqual(syringe.homing_count,1);self.assertEqual(syringe.positions,[48,0]);self.assertNotIn('R',syringe.commands)
        self.assertIsNone(self.repo.execution.snapshot()['syringe_calibration']['last_homing'])
        formal={**self.sequence,'mode':'experiment','blocks':[default_preflight('waste'),self.sequence['blocks'][1]]}
        formal.pop('initial_cell_volume_ml')
        formal['blocks'][1]['steps'].insert(0,dict(device='valve',operation='switch_route',route='water'))
        code,result=self.request('POST','/api/execution/prepare',{'sequence':formal})
        self.assertEqual(code,200,result);self.assertEqual(result['calibration']['state'],'required')

    def test_debug_shares_controller_exclusion_and_admin_stop(self):
        self.sequence['blocks']=self.sequence['blocks'][:1]
        self.sequence['blocks'][0]['steps'][0]['volume_ml']=10
        self.start_debug();self.wait_for(lambda:0x47 in self.pump.commands)
        _,other=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        body=dict(run_id=other['run_id'],version=other['version'],confirm_setup=True,confirm_homing_discharge=False)
        code,error=self.request('POST','/api/execution/debug-start',body)
        self.assertEqual(code,409,error);self.assertEqual(error['error'],'execution_busy')
        current=self.repo.execution.snapshot()['current']
        self.assertEqual(self.request('POST','/api/execution/stop',{'run_id':current['run_id']},role='viewer')[0],403)
        self.assertEqual(self.request('POST','/api/execution/stop',{'run_id':current['run_id']})[0],200)
        self.completed('stopped');self.assertFalse(self.pump.busy)

    def test_optional_prefix_still_runs_full_preparation_and_rejects_debug_start_bypass(self):
        self.sequence['blocks']=[default_preflight('waste'),self.sequence['blocks'][0]]
        code,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.assertEqual(code,200,prepared);self.assertEqual(prepared['operation'],'sequence')
        body=dict(run_id=prepared['run_id'],version=prepared['version'],confirm_setup=True,confirm_homing_discharge=False)
        self.assertEqual(self.request('POST','/api/execution/debug-start',body)[0],422)
        base.LinuxExecutionTests.empty_cell(self,prepared,route='waste')
        code,result=self.request('POST','/api/execution/start',dict(run_id=prepared['run_id'],version=prepared['version']))
        self.assertEqual(code,202,result);self.completed()
        self.assertEqual(self.preflight_devices['peristaltic_2'].commands.count(0x47),1)
        self.assertEqual(self.preflight_devices['valve'].commands,[])

    def test_cv_only_debug_opens_only_workstation_and_saves_debug_provenance(self):
        station=PseudoStation()
        try:
            self.repo.station=station_policy(dict(enabled=True,port=station.port))
            params=measurement_templates(self.repo.station)['electrochemical_station']['action']['default_parameters']
            params.update(upper_potential_v=.1,lower_potential_v=-.1)
            self.sequence['blocks']=[dict(id='measure',description='CV only',steps=[dict(device='electrochemical_station',operation='measure',technique='cyclic_voltammetry',parameters=params)])]
            self.start_debug()
            self.wait_for(lambda:self.repo.measurements.latest() and self.repo.measurements.latest()['metadata']['point_count']>=1)
            self.assertEqual(self.repo.status()['runtime']['active_step']['device'],'electrochemical_station')
            self.completed()
            metadata=self.repo.measurements.latest()['metadata']
            self.assertEqual(metadata['point_count'],3);self.assertEqual(metadata['provenance']['execution_mode'],'debug_sequence')
            self.assertEqual(self.pump.commands,[])
            self.assertTrue(all(not device.commands for device in self.preflight_devices.values()))
        finally:station.close()


if __name__=='__main__':unittest.main()
