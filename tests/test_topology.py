"""Confirmed inlet-selector semantics without opening any physical serial port."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from pump_console.repository import LabRepository, ApiError
from pump_console.preflight import default_preflight
from pump_console.topology import INLET, migrate_sequence, SOURCE_CHANNELS
from pump_console.cell_safety import drain_sequence
from pump_console.mixed_runner import run_mixed


class InletTopologyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.lab = self.root / 'lab'
        shutil.copytree(ROOT / 'tests/fixtures/demo-lab', self.lab)
        self.repo = LabRepository(self.lab, self.root / 'data', topology=INLET)

    def sequence(self, steps):
        return dict(schema_version=2, id='inlet_test', description='Synthetic inlet selector test',
                    preflight=default_preflight('waste'), steps=steps)

    def test_source_channels_are_water_five_electrolyte_six_and_waste_is_direct(self):
        templates = self.repo.templates()
        self.assertEqual(templates['valve']['device']['routes'], SOURCE_CHANNELS)
        self.assertEqual(templates['syringe_c']['action']['allowed_fluids'], ['water', 'electrolyte'])
        self.assertEqual(templates['peristaltic_2']['action']['routing_mode'], 'direct')
        policy = self.repo.safety_policy()
        drain = drain_sequence(policy, 'waste')
        self.assertEqual(len(drain['steps']), 1)
        self.assertEqual(drain['steps'][0]['device'], 'peristaltic_2')
        self.assertEqual(drain['steps'][0]['operation'], 'withdraw')
        self.assertEqual(policy['cell']['capacity_ml'], 40)

    def test_direct_drain_compiles_without_any_valve_action_and_preserves_extra_time(self):
        sequence = self.sequence([dict(device='peristaltic_2', operation='withdraw', fluid='waste_liquid', volume_ml=10)])
        preview = self.repo.preview(sequence); step = preview['plan']['steps'][0]
        self.assertEqual(step['action'], 'drain_direct'); self.assertEqual(step['volume_ml'], 10)
        self.assertNotIn('requires_route', step)
        fraction = self.repo.compiler.load_templates(self.repo.templates_dir)[1]['peristaltic_2']['action']['extra_time_fraction']
        self.assertAlmostEqual(step['timing_equivalent_ml'], 10 * (1 + fraction))
        self.assertEqual(preview['plan']['totals']['drain_nominal_ml'], 10)
        self.assertNotIn('peristaltic_2', preview['plan']['totals']['dose_ml_by_device'])

    def test_each_syringe_liquid_requires_matching_explicit_source_selection(self):
        for fluid, channel in SOURCE_CHANNELS.items():
            steps = [dict(device='valve', operation='switch_route', route=fluid),
                     dict(device='syringe_c', operation='inject', fluid=fluid, volume_ml=1)]
            plan = self.repo.preview(self.sequence(steps))['plan']
            self.assertEqual(plan['steps'][0]['channel'], channel)
            self.assertEqual(plan['steps'][1]['requires_source']['channel'], channel)
            self.assertEqual(plan['steps'][1]['fluid'], fluid)
        for steps in [[dict(device='syringe_c', operation='inject', fluid='electrolyte', volume_ml=1)],
                      [dict(device='valve', operation='switch_route', route='water'), dict(device='syringe_c', operation='inject', fluid='electrolyte', volume_ml=1)]]:
            with self.assertRaises(ApiError): self.repo.preview(self.sequence(steps))

    def test_old_waste_channels_and_missing_preflight_cannot_start(self):
        for route in ('waste_1', 'waste_2'):
            with self.assertRaises(ApiError): self.repo.preview(self.sequence([dict(device='valve', operation='switch_route', route=route)]))
        sequence = self.sequence([dict(device='peristaltic_1', operation='inject', fluid='water', volume_ml=1)])
        sequence['preflight']['steps'][2]['route'] = 'waste_1'
        with self.assertRaises(ApiError): self.repo.preview(sequence)

    def test_migration_removes_obsolete_waste_switches_and_keeps_volumes(self):
        old = json.loads((self.lab / 'config/sequences/demo_rinse_with_predrain.json').read_text())
        migrated = migrate_sequence(old)
        before = [(step['device'], step.get('volume_ml')) for step in old['steps'] if 'volume_ml' in step]
        after = [(step['device'], step.get('volume_ml')) for step in migrated['steps'] if 'volume_ml' in step]
        self.assertEqual(before, after)
        self.assertEqual(migrated['preflight']['steps'][2]['route'], 'waste')
        self.assertFalse(any(step.get('route') in ('waste_1', 'waste_2') for step in migrated['steps']))
        self.repo.preview(migrated)
        self.assertEqual(migrate_sequence(migrated), migrated)

    def test_builtin_blocks_use_direct_drain_and_explicit_inlet_selection(self):
        self.assertEqual(self.repo.block_library.list()['blocks'],[])
        custom=json.loads((ROOT/'tests/fixtures/legacy_function_blocks/electrolyte_injection.json').read_text());custom['id']='custom_electrolyte'
        self.repo.block_library.save(custom)
        blocks = self.repo.block_library.list()['blocks']
        self.assertFalse(any(step.get('route') in ('waste_1', 'waste_2') for block in blocks for step in block['steps']))
        syringe = next(block for block in blocks if block['id'] == 'custom_electrolyte')
        self.assertEqual(syringe['steps'][0]['route'], 'electrolyte')

    def test_mismatched_runtime_source_readback_never_aspirates(self):
        plan = self.repo.preview(self.sequence([dict(device='valve', operation='switch_route', route='water'),
            dict(device='syringe_c', operation='inject', fluid='water', volume_ml=1)]))['plan']
        devices = self.repo.compiler.load_templates(self.repo.templates_dir)[0]['devices']
        transfers = []
        class Device:
            def __init__(self, name, config, link, event, **kwargs): self.motion_pending=False;self.link=link
            def preflight(self, *args): pass
            def normal(self): return True
            def channel(self): return 6
            def goto(self, channel): pass
        class Syringe(Device):
            def transfer(self, step): transfers.append(step)
        class Runner:
            def __init__(self, configs, plan, event):
                self.devices={};self.links=[];self.clock=lambda:0;self.sleep=lambda seconds:None
                self.link_factory=lambda *args:SimpleNamespace(close=lambda:None)
        module = SimpleNamespace(DEVICE_CLASSES={'valve':Device, 'syringe':Syringe}, Syringe=Syringe)
        with patch('pump_console.mixed_runner.importlib.import_module', return_value=module):
            with self.assertRaisesRegex(ValueError, '入口通道'):
                run_mixed(devices, plan, lambda *args, **kwargs:None, SimpleNamespace(Runner=Runner), None,
                          self.root / 'data', 'synthetic_run', 'synthetic_admin')
        self.assertEqual(transfers, [])


if __name__ == '__main__': unittest.main()
