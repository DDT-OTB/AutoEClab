"""Debug is an ordinary multi-block workflow with an explicit, narrower prep exception."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from pump_console.repository import LabRepository,ApiError
from pump_console.preflight import default_preflight
from pump_console.topology import INLET


def debug_sequence():
    return dict(schema_version=3,mode='debug',initial_cell_volume_ml=10,
        id='synthetic_debug',description='Multiple explicit debug components',blocks=[
            dict(id='first',description='First group',steps=[dict(device='peristaltic_1',operation='inject',fluid='water',volume_ml=.04)]),
            dict(id='second',description='Second group',steps=[dict(device='peristaltic_2',operation='withdraw',fluid='waste_liquid',volume_ml=.04)])])


class DebugWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.lab=self.root/'lab'
        shutil.copytree(ROOT/'tests/fixtures/demo-lab',self.lab)
        self.repo=LabRepository(self.lab,self.root/'data',execution_enabled=True,topology=INLET)

    def test_multi_block_debug_preview_and_save_never_add_preparation_or_move(self):
        value=debug_sequence();preview=self.repo.preview(value)
        self.assertIsNone(preview['preflight']);self.assertEqual(preview['sequence_mode'],'debug')
        self.assertEqual([step['device'] for step in preview['plan']['steps']],['peristaltic_1','peristaltic_2'])
        self.assertEqual(preview['cell_safety']['initial_volume_ml'],10)
        self.assertEqual(preview['cell_safety']['initial_volume_source'],'operator_declared_debug')
        result=self.repo.save_experiment(value)
        self.assertEqual(self.repo.get_experiment(result['id'])['sequence'],value)
        self.assertEqual(self.repo.get_experiment(result['id'])['sequence_mode'],'debug')
        self.assertIn('人工声明的 10 mL',(self.lab/'config/sequences/synthetic_debug.md').read_text())
        self.assertIsNone(self.repo.execution.snapshot()['current'])

    def test_debug_exception_is_explicit_and_optional_preparation_must_be_first(self):
        value=debug_sequence();value.pop('mode');value.pop('initial_cell_volume_ml')
        with self.assertRaises(ApiError):self.repo.preview(value)
        value=debug_sequence();value['blocks'].insert(1,default_preflight('waste'))
        with self.assertRaises(ApiError):self.repo.preview(value)
        value['blocks'].insert(0,value['blocks'].pop(1))
        prepared=self.repo.execution.prepare(value,'synthetic_admin')
        self.assertEqual(prepared['operation'],'sequence')
        self.assertTrue(prepared['cell_preparation']['required'])

    def test_initial_cell_liquid_counts_towards_the_same_capacity_and_cannot_be_overridden(self):
        for amount in (40,41,-1,True,'0',float('nan'),float('inf')):
            value=debug_sequence();value['initial_cell_volume_ml']=amount
            with self.assertRaises(ApiError):self.repo.preview(value)
        value=debug_sequence();value['initial_cell_volume_ml']=39.96
        self.assertEqual(self.repo.preview(value)['cell_safety']['peak_volume_ml'],40)

    def test_syringe_debug_may_verify_current_source_but_does_not_add_valve_or_homing(self):
        value=debug_sequence();value['blocks']=[dict(id='syringe',description='Explicit syringe transfer',steps=[
            dict(device='syringe_c',operation='inject',fluid='water',volume_ml=1)])]
        prepared=self.repo.execution.prepare(value,'synthetic_admin')
        self.assertEqual(len(prepared['preview']['plan']['steps']),1)
        step=prepared['preview']['plan']['steps'][0]
        self.assertEqual(step['action'],'syringe_transfer');self.assertEqual(step['requires_source']['channel'],5)
        self.assertFalse(prepared['calibration']['required']);self.assertFalse(prepared['cell_preparation']['required'])

    def test_no_start_endpoint_can_launch_debug_without_acknowledgement(self):
        value=debug_sequence();manager=self.repo.execution
        prepared=manager.prepare(value,'synthetic_admin')
        with patch.object(manager,'launcher') as launch:
            for fn,args in [(manager.start,()),(manager.start_debug,(False,)),(manager.start_debug,('yes',))]:
                with self.assertRaises(ApiError):fn(prepared['run_id'],prepared['version'],'synthetic_admin',*args)
            launch.assert_not_called()
        status=manager.preparation_status(prepared['run_id'])
        self.assertEqual(status['workflow']['phase'],'awaiting_debug_confirmation')
        self.assertEqual(status['sequence'],value)

    def test_explicit_debug_homing_needs_separate_discharge_ack_and_never_qualifies_as_formal_proof(self):
        value=debug_sequence();value['blocks'][0]['steps']=[dict(device='syringe_c',operation='initialize')]
        prepared=self.repo.execution.prepare(value,'synthetic_admin')
        with self.assertRaises(ApiError) as caught:
            self.repo.execution.start_debug(prepared['run_id'],prepared['version'],'synthetic_admin',True)
        self.assertEqual(caught.exception.code,'discharge_confirmation_required')
        value['blocks'][0]['steps'][0]['volume_ml']=1
        with self.assertRaises(ApiError):self.repo.preview(value)
        self.assertIsNone(self.repo.execution.snapshot()['syringe_calibration']['last_homing'])

    def test_custom_homing_block_can_be_saved_and_reused_without_added_transfer_parameters(self):
        document=dict(schema_version=1,kind='function_block',id='explicit_homing',description='Explicit homing',
                      steps=[dict(device='syringe_c',operation='initialize')])
        self.repo.block_library.save(document)
        block=self.repo.block_library.list()['blocks'][0]
        self.assertNotIn('fluid',block['steps'][0]);self.assertNotIn('volume_ml',block['steps'][0])
        self.assertEqual(len(block['steps']),1)
        value=debug_sequence();value['blocks']=[{key:item for key,item in block.items() if key not in ('schema_version','kind')}]
        self.assertEqual(self.repo.preview(value)['plan']['steps'][0]['action'],'initialize_syringe')


if __name__=='__main__':unittest.main()
