"""Cell capacity, startup policy and empty confirmation; no real hardware."""
import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import test_execution as execution_tests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from pump_console.cell_safety import analyze_volumes, drain_sequence, load_policy, require_safe_volumes, CellSafetyError
from pump_console.repository import ApiError
from pump_console.preflight import default_preflight


def action(volume, operation='inject', device='peristaltic_1', fluid='water'):
    return {'device': device, 'operation': operation, 'volume_ml': volume, 'fluid': fluid}


def sequence(steps):
    return {'preflight':default_preflight(),'schema_version': 2, 'id': 'synthetic_cell_policy', 'description': 'Synthetic cell safety verification', 'steps': steps}


class CellVolumeTests(unittest.TestCase):
    def test_exact_capacity_allowed_and_mixed_liquids_are_summed(self):
        safe = sequence([action(10), action(15, device='syringe_c', fluid='electrolyte'), action(15, device='peristaltic_3', fluid='sulfuric_acid')])
        self.assertEqual(require_safe_volumes(safe, load_policy())['peak_volume_ml'], 40)
        safe['steps'][-1]['volume_ml'] = 15.001
        with self.assertRaises(CellSafetyError): require_safe_volumes(safe, load_policy())

    def test_balance_is_checked_after_every_step_and_not_only_final_volume(self):
        report = analyze_volumes(sequence([action(30), action(15), action(100, 'withdraw')]), load_policy())
        self.assertEqual(report['final_volume_ml'], 0)
        self.assertFalse(report['valid'])
        self.assertTrue(report['steps'][1]['over_capacity'])
        self.assertIn('第 2 步', report['errors'][0])

    def test_withdrawals_release_capacity_and_excess_drain_does_not_create_negative_storage(self):
        report = require_safe_volumes(sequence([action(100,'withdraw'),action(30),action(25,'withdraw'),action(35)]),load_policy())
        self.assertEqual([row['after_ml'] for row in report['steps']],[0,30,5,40])
        self.assertFalse(report['volume_measured'])
        self.assertTrue(report['initial_state_requires_preflight'])

    def test_decimal_roundoff_does_not_reject_an_exact_40_ml_plan(self):
        self.assertEqual(require_safe_volumes(sequence([action(.1)]*400),load_policy())['peak_volume_ml'],40)

    def test_syringe_step_rounding_cannot_sneak_past_the_capacity_limit(self):
        value = sequence([action(24.998),action(15.002,device='syringe_c',fluid='electrolyte')])
        plan = {'steps':[{}, {'achievable_volume_ml':15.002083333333333}]}
        self.assertFalse(analyze_volumes(value,load_policy(),plan)['valid'])

    def test_startup_drain_has_fixed_minimum_and_explicit_waste_route(self):
        policy=load_policy()
        for route in policy['cell']['allowed_waste_routes']:
            value=drain_sequence(policy,route)
            self.assertEqual(value['steps'][0]['route'],route)
            self.assertGreaterEqual(value['steps'][1]['volume_ml'],policy['cell']['capacity_ml'])
            self.assertEqual(value['steps'][1]['device'],'peristaltic_2')
        with self.assertRaises(CellSafetyError):drain_sequence(policy,'')


class CellPreparationTests(unittest.TestCase):
    setUp=execution_tests.PreparationTests.setUp
    tearDown=execution_tests.PreparationTests.tearDown
    assert_api_error=execution_tests.PreparationTests.assert_api_error

    def test_preflight_is_required_even_for_water_only_experiments(self):
        prepared=self.manager.prepare(sequence([action(1)]),'synthetic_admin')
        self.assertTrue(prepared['cell_preparation']['required'])
        self.assertEqual(prepared['cell_preparation']['state'],'required')
        with patch.object(self.manager,'_check_ports'):
            self.assert_api_error('cell_preparation_required',self.manager.start,prepared['run_id'],prepared['version'],'synthetic_admin')
        self.assert_api_error('cell_preparation_required',self.manager.confirm_empty,prepared['run_id'],prepared['version'],'synthetic_admin',True)
        self.assert_api_error('empty_confirmation_required',self.manager.confirm_empty,prepared['run_id'],prepared['version'],'synthetic_admin',False)

    def test_over_capacity_cannot_be_saved_or_prepared(self):
        value=sequence([action(30),action(11,device='peristaltic_3',fluid='sulfuric_acid')])
        self.assert_api_error('cell_capacity_exceeded',self.repo.preview,value)
        self.assert_api_error('cell_capacity_exceeded',self.manager.prepare,value,'synthetic_admin')
        self.assert_api_error('cell_capacity_exceeded',self.repo.save_experiment,value)
        self.assertFalse((self.lab/'config/sequences/synthetic_cell_policy.json').exists())

    def test_client_cannot_skip_discharge_confirmation_or_supply_an_unknown_route(self):
        prepared=self.manager.prepare(sequence([action(1)]),'synthetic_admin')
        args=(prepared['run_id'],prepared['version'],'synthetic_admin')
        self.assert_api_error('discharge_confirmation_required',self.manager.drain_cell,*args,'waste_1',False,'test_request')
        self.assert_api_error('invalid_waste_route',self.manager.drain_cell,*args,'unknown',True,'test_request')
