"""Functional block import, storage, global safety and immutable preparation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_api
from pump_console.measurement import measurement_templates
from pump_console.repository import ApiError
from pump_console.preflight import default_preflight
from pump_console.workflow import flatten_sequence, import_block, function_block_templates

ROOT = Path(__file__).resolve().parents[1]


class WorkflowApiTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    request = test_api.ApiTests.request

    def workflow(self):
        return json.loads((ROOT / 'config/examples/demo_blocks.json').read_text())

    def test_library_is_authenticated_and_contains_only_english_parameterized_blocks(self):
        code, result = self.request('GET', '/api/function-blocks')
        self.assertEqual(code, 200)
        self.assertEqual(len(result['blocks']), 4)
        for value in result['blocks']:
            block = import_block(value)
            self.assertTrue(block['steps'])
        self.assertEqual(self.request('GET', '/api/function-blocks', headers={'Cookie': ''})[0], 401)
        self.assertNotIn('lab_automation.transport', __import__('sys').modules)

    def test_preview_expands_all_blocks_once_in_order_with_unique_action_ids(self):
        sequence = self.workflow()
        code, result = self.request('POST', '/api/preview', {'sequence': sequence})
        self.assertEqual(code, 200, result)
        flattened = flatten_sequence(sequence)
        self.assertEqual(len(result['plan']['steps']), 9)
        self.assertEqual(result['plan'], self.repo.preview({**flattened,'preflight':default_preflight()})['plan'])
        self.assertEqual([step['id'] for step in result['plan']['steps']], [step['id'] for step in flattened['steps']])
        self.assertEqual([item['first_step'] for item in result['blocks'] if item.get('type') != 'preflight'], [1, 4, 5, 7])
        self.assertEqual(result['cell_safety']['peak_volume_ml'], 15)
        self.assertFalse(result['hardware_executed'])

    def test_save_reload_catalog_copy_and_description_preserve_the_two_levels(self):
        sequence = self.workflow()
        code, saved = self.request('POST', '/api/experiments', {'sequence': sequence})
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved['sequence'], sequence)
        self.assertEqual(saved['block_count'], 5)
        self.assertEqual(saved['step_count'], 9)
        self.assertEqual(self.request('GET', '/api/experiments/'+sequence['id'])[1]['sequence'], sequence)
        catalog = self.request('GET', '/api/experiments')[1]['experiments']
        self.assertTrue(next(item for item in catalog if item['id']==sequence['id'])['editable'])
        code, copied = self.request('POST', '/api/experiments/'+sequence['id']+'/duplicate',
                                    {'new_id': 'copied_blocks', 'expected_version': saved['version']})
        self.assertEqual(code, 200, copied)
        self.assertEqual(copied['sequence']['blocks'], sequence['blocks'])
        document = (self.lab/'config/sequences/copied_blocks.md').read_text()
        for block in sequence['blocks']:
            self.assertIn(block['id'], document)

    def test_global_volume_limit_cannot_be_reset_by_a_new_block_or_hidden_by_a_final_drain(self):
        sequence = self.workflow()
        first = {'id': 'water', 'description': 'Water', 'steps': [{'device': 'peristaltic_1', 'operation': 'inject', 'volume_ml': 25, 'fluid': 'water'}]}
        second = {'id': 'acid', 'description': 'Acid', 'steps': [{'device': 'peristaltic_3', 'operation': 'inject', 'volume_ml': 16, 'fluid': 'sulfuric_acid'}]}
        drain = import_block(next(item for item in function_block_templates() if item['id']=='drain_waste_1'))
        sequence['blocks'] = [default_preflight(),first, second, drain]
        self.repo.execution.enabled = True
        for route in ('/api/preview', '/api/experiments', '/api/execution/prepare'):
            code, error = self.request('POST', route, {'sequence': sequence})
            self.assertEqual(code, 422)
            self.assertEqual(error['error'], 'cell_capacity_exceeded')
        sequence['blocks'] = [default_preflight(),first, drain, second]
        self.assertEqual(self.request('POST', '/api/preview', {'sequence': sequence})[0], 200)

    def test_routes_carry_between_blocks_and_missing_or_reordered_routes_are_rejected(self):
        sequence = self.workflow()
        route = {'id': 'route', 'description': 'Route', 'steps': [{'device': 'valve', 'operation': 'switch_route', 'route': 'waste_2'}]}
        drain = {'id': 'drain', 'description': 'Drain', 'steps': [{'device': 'peristaltic_2', 'operation': 'withdraw', 'volume_ml': 10, 'fluid': 'waste_liquid'}]}
        sequence['blocks'] = [default_preflight(),route, drain]
        code, result = self.request('POST', '/api/preview', {'sequence': sequence})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['plan']['steps'][1]['required_channel'], 6)
        sequence['blocks'][1:] = reversed(sequence['blocks'][1:])
        self.assertEqual(self.request('POST', '/api/preview', {'sequence': sequence})[0], 422)

    def test_ambiguous_nested_empty_or_duplicate_blocks_and_external_references_are_rejected(self):
        base = self.workflow()
        invalid = [dict(base, steps=[]), dict(base, blocks=[]), dict(base, blocks=[base['blocks'][0]]*2)]
        for change in ({'steps': []}, {'reference': '../remote.json'}, {'id': '中文'},
                       {'steps': [{'device': 'valve', 'operation': 'switch_route', 'blocks': []}]},
                       {'steps': [{'device': 'valve', 'operation': 'switch_route', 'route': 'waste_1', 'id':'same'}]*2}):
            value = deepcopy(base)
            value['blocks'][0].update(change)
            invalid.append(value)
        for sequence in invalid:
            with self.subTest(sequence=sequence):
                self.assertEqual(self.request('POST', '/api/preview', {'sequence': sequence})[0], 422)

    def test_cv_inside_any_block_is_planning_only_and_blocks_the_whole_execution(self):
        sequence = self.workflow()
        template = measurement_templates()['electrochemical_station']
        sequence['blocks'].insert(3, {'id': 'cv', 'description': 'CV draft', 'steps': [
            {'device': 'electrochemical_station', 'operation': 'measure', 'technique': 'cyclic_voltammetry', 'parameters': template['action']['default_parameters']}]})
        code, result = self.request('POST', '/api/preview', {'sequence': sequence})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['plan']['steps'][4]['action'], 'measurement')
        self.assertFalse(result['plan']['ready_for_hardware'])
        self.repo.execution.enabled = True
        code, error = self.request('POST', '/api/execution/prepare', {'sequence': sequence})
        self.assertEqual(code, 422)
        self.assertEqual(error['error'], 'instrument_not_integrated')

    def test_preparation_freezes_blocks_and_still_requires_calibration_and_empty_cell(self):
        self.repo.execution.enabled = True
        sequence = self.workflow()
        prepared = self.repo.execution.prepare(sequence, 'test_admin')
        folder, job = self.repo.execution._job(prepared['run_id'])
        snapshot = (folder/'snapshot.json').read_bytes()
        sequence['blocks'][2]['steps'][0]['volume_ml'] = 20
        self.assertEqual((folder/'snapshot.json').read_bytes(), snapshot)
        self.assertEqual(json.loads(snapshot)['sequence']['schema_version'], 3)
        self.assertTrue(prepared['calibration']['required'])
        self.assertEqual(prepared['calibration']['state'], 'required')
        self.assertEqual(prepared['cell_preparation']['state'], 'required')
        with patch.object(self.repo.execution, '_check_ports'):
            with self.assertRaises(ApiError) as caught:
                self.repo.execution.start(prepared['run_id'], prepared['version'], 'test_admin')
            self.assertEqual(caught.exception.code, 'syringe_calibration_required')
        self.assertFalse(prepared['hardware_executed'])
