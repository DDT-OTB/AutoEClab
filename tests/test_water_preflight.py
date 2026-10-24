"""Fixed water-source validation and safe migration without device I/O."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from copy import deepcopy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from pump_console.preflight import default_preflight,validate_preflight,upgrade_preflight_block,upgrade_sequence_preflight
from pump_console.execution import water_calibration_plan,write_private
from pump_console.repository import LabRepository


class WaterPreflightTests(unittest.TestCase):
    def test_fixed_order_and_source_cannot_be_deleted_replaced_or_forged(self):
        value=default_preflight('waste');self.assertEqual(len(value['steps']),4)
        self.assertEqual(value['steps'][0],dict(device='valve',operation='select_syringe_water_if_used',route='water'))
        self.assertEqual(value['steps'][1]['operation'],'home_if_used')
        for steps in (value['steps'][1:],[value['steps'][1],value['steps'][0],*value['steps'][2:]],
                      [{**value['steps'][0],'route':'electrolyte'},*value['steps'][1:]],
                      [{**value['steps'][0],'confirmed':True},*value['steps'][1:]]):
            with self.assertRaises(ValueError):validate_preflight({**value,'steps':steps})

    def test_only_known_legacy_steps_are_upgraded_without_changing_other_actions(self):
        block=default_preflight('waste');block['steps']=block['steps'][1:]
        sequence=dict(schema_version=3,id='migration',description='Migration test',blocks=[block,
            dict(id='action',description='Existing action',steps=[dict(device='peristaltic_1',operation='inject',fluid='water',volume_ml=2)])])
        before=deepcopy(sequence);upgraded=upgrade_sequence_preflight(sequence)
        self.assertEqual(sequence,before);self.assertEqual(upgraded['blocks'][1],before['blocks'][1])
        self.assertEqual(upgraded['blocks'][0]['steps'][1:],before['blocks'][0]['steps'])
        self.assertEqual(upgrade_sequence_preflight(upgraded),upgraded)
        block['steps'][2]['confirmed']=True
        self.assertEqual(upgrade_preflight_block(block),block)

    def test_calibration_plan_requires_explicit_water_five_and_old_proofs_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            lab=Path(temporary)/'lab';shutil.copytree(ROOT/'tests/fixtures/demo-lab',lab)
            repo=LabRepository(lab,Path(temporary)/'data',execution_enabled=True)
            devices,templates=repo.compiler.load_templates(repo.templates_dir)
            plan=water_calibration_plan(devices)
            self.assertEqual([step['device'] for step in plan['steps']],['valve','syringe_c'])
            self.assertEqual(plan['steps'][0]['channel'],5)
            bad=deepcopy(devices);bad['devices']['valve']['routes']['water']=6
            with self.assertRaises(ValueError):water_calibration_plan(bad)
            sequence=json.loads((lab/'config/sequences/demo_syringe.json').read_text())
            parent=repo.execution.prepare(sequence,'synthetic_admin')
            child=repo.execution._store_preparation(dict(schema_version=2,id='old_home',description='Old homing',steps=[dict(device='syringe_c',operation='initialize')]),devices,templates,dict(plan=plan),'synthetic_admin',operation='calibrate_syringe',target_run_id=parent['run_id'])
            folder,job=repo.execution._job(parent['run_id']);job['calibration_run_id']=child['run_id'];write_private(folder/'job.json',job)
            child_folder,child_job=repo.execution._job(child['run_id']);child_job['state']='completed';write_private(child_folder/'job.json',child_job)
            write_private(child_folder/'worker.json',dict(state='completed',initialization_verified=True))
            self.assertEqual(repo.execution.preparation_status(parent['run_id'])['calibration']['state'],'failed')


if __name__=='__main__':unittest.main()
