"""Logical total dose, physical stroke limit and pool capacity are independent."""
from copy import deepcopy
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
from pump_console.syringe_batch import load_policy,split_volume


class SyringeBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.lab=Path(self.tmp.name)/'lab';shutil.copytree(ROOT/'tests/fixtures/demo-lab',self.lab)
        self.repo=LabRepository(self.lab,Path(self.tmp.name)/'data',execution_enabled=True,topology=INLET)

    def sequence(self,volume):
        return dict(schema_version=2,id='dose_total',description='Synthetic total syringe dose',preflight=default_preflight('waste'),
            steps=[dict(device='valve',operation='switch_route',route='electrolyte'),
                   dict(id='total_dose',device='syringe_c',operation='inject',fluid='electrolyte',volume_ml=volume)])

    def test_thirty_ml_is_one_logical_action_with_twenty_and_ten_ml_strokes(self):
        result=self.repo.preview(self.sequence(30));dose=result['plan']['steps'][1]
        self.assertEqual(len(result['plan']['steps']),2);self.assertEqual(dose['id'],'total_dose')
        self.assertEqual(dose['volume_ml'],30);self.assertEqual(dose['achievable_volume_ml'],30)
        self.assertEqual([s['volume_ml'] for s in dose['stroke_plan']],[20,10])
        self.assertEqual([s['delta_steps'] for s in dose['stroke_plan']],[9600,4800])
        self.assertEqual(dose['delta_steps'],9600);self.assertEqual(dose['stroke_count'],2)
        self.assertEqual(result['cell_safety']['final_volume_ml'],30)
        self.assertEqual(result['plan']['totals']['syringe_ml_by_device']['syringe_c'],30)
        self.assertEqual(dose['requires_source']['channel'],6)
        self.assertEqual(self.repo.templates()['syringe_c']['device']['capacity_ml'],25)
        self.assertEqual(self.repo.templates()['syringe_c']['action']['max_stroke_volume_ml'],20)

    def test_twenty_five_and_forty_split_but_small_doses_keep_their_original_single_stroke(self):
        for total,expected in ((25,[20,5]),(40,[20,20]),(20,[20]),(1,[1])):
            dose=self.repo.preview(self.sequence(total))['plan']['steps'][1]
            self.assertEqual([s['volume_ml'] for s in dose.get('stroke_plan',[dose])],expected)
            self.assertTrue(all(s['volume_ml']<=20 for s in dose.get('stroke_plan',[dose])))

    def test_capacity_accounting_cannot_reset_at_stroke_boundaries(self):
        value=self.sequence(30);value['steps'].insert(0,dict(device='peristaltic_1',operation='inject',fluid='water',volume_ml=15))
        with self.assertRaises(ApiError) as caught:self.repo.preview(value)
        self.assertEqual(caught.exception.code,'cell_capacity_exceeded')
        with self.assertRaises(ApiError):self.repo.preview(self.sequence(41))
        self.assertIsNone(self.repo.execution.snapshot()['current'])

    def test_legacy_workflows_and_custom_function_blocks_use_the_same_stroke_policy(self):
        self.repo.topology=None
        value=self.sequence(30);value['preflight']=default_preflight();value['steps']=value['steps'][1:]
        dose=self.repo.preview(value)['plan']['steps'][0]
        self.assertEqual([s['volume_ml'] for s in dose['stroke_plan']],[20,10])
        self.repo.topology=INLET
        document=dict(schema_version=1,kind='function_block',id='dose_thirty',description='Thirty mL dose',steps=[self.sequence(30)['steps'][1]])
        self.assertTrue(self.repo.block_library.save(document)['saved'])

    def test_policy_changes_invalidate_prepared_workflows_before_any_controller_launch(self):
        value=self.sequence(30);prepared=self.repo.execution.prepare(value,'synthetic_admin')
        changed={**load_policy(),'max_stroke_volume_ml':10}
        with patch('pump_console.syringe_batch.load_policy',return_value=changed),patch.object(self.repo.execution,'launcher') as launch:
            with self.assertRaises(ApiError) as caught:
                self.repo.execution.start(prepared['run_id'],prepared['version'],'synthetic_admin')
            self.assertEqual(caught.exception.code,'syringe_policy_changed');launch.assert_not_called()

    def test_nonfinite_and_unbounded_doses_and_client_supplied_stroke_plans_are_rejected(self):
        device=self.repo.templates()['syringe_c']['device']
        for volume in (True,0,-1,float('nan'),float('inf'),1e300):
            with self.assertRaises(ValueError):split_volume(volume,device)
        value=self.sequence(30);value['steps'][1]['stroke_plan']=[dict(volume_ml=30)]
        with self.assertRaises(ApiError):self.repo.preview(value)


if __name__=='__main__':unittest.main()
