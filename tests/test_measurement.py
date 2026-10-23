"""CV planning is validated without any workstation or pump activity."""
import copy
import json
from pathlib import Path
import sys
import unittest
import test_api

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from pump_console.measurement import measurement_templates, validate_cv
from pump_console.repository import ApiError
from pump_console.preflight import default_preflight


class MeasurementApiTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    request = test_api.ApiTests.request

    def measurement(self):
        template = measurement_templates()["electrochemical_station"]
        return {"device":"electrochemical_station","operation":"measure","technique":"cyclic_voltammetry",
                "parameters":copy.deepcopy(template["action"]["default_parameters"])}

    def cv_sequence(self):
        return {"preflight":default_preflight(),"schema_version":2,"id":"cv_only_draft","description":"Cyclic voltammetry planning draft",
                "steps":[self.measurement()]}

    def test_template_is_planning_only_and_not_added_to_hardware_inventory(self):
        _, templates = self.request("GET","/api/templates")
        self.assertEqual(len(templates["templates"]),5)
        virtual = templates["measurement_templates"]["electrochemical_station"]
        self.assertFalse(virtual["device"]["execution_supported"])
        _, status = self.request("GET","/api/status")
        self.assertEqual(len(status["devices"]),5)

    def test_cv_only_preview_has_timing_and_blocks_execution(self):
        code, result = self.request("POST","/api/preview",{"sequence":self.cv_sequence()})
        self.assertEqual(code,200)
        self.assertEqual(result["estimated_timed_seconds"],25)
        self.assertFalse(result["plan"]["ready_for_hardware"])
        self.assertTrue(result["plan"]["unresolved"])
        self.assertFalse(result["plan"]["steps"][0]["execution_supported"])
        self.assertTrue(result["warnings"])
        self.assertFalse(result["hardware_executed"])

    def test_mixed_plan_keeps_original_order_global_ids_routes_and_volumes(self):
        original = self.repo.preview(self.sequence)["plan"]
        self.sequence["steps"].insert(6,self.measurement())
        code, result = self.request("POST","/api/preview",{"sequence":self.sequence})
        self.assertEqual(code,200)
        plan = result["plan"]
        self.assertEqual(len(plan["steps"]),12)
        self.assertEqual(plan["steps"][6]["action"],"measurement")
        self.assertEqual(plan["steps"][6]["id"],"step_07")
        self.assertEqual(plan["steps"][7]["index"],8)
        self.assertEqual(plan["steps"][8]["required_channel"],6)
        self.assertEqual(plan["totals"],original["totals"])
        self.assertFalse(plan["ready_for_hardware"])

    def test_save_and_reload_measurement_json_and_companion_markdown(self):
        sequence = self.cv_sequence()
        code, saved = self.request("POST","/api/experiments",{"sequence":sequence})
        self.assertEqual(code,200)
        _, loaded = self.request("GET","/api/experiments/cv_only_draft")
        self.assertEqual(loaded["sequence"],sequence)
        document = (self.lab/"config/sequences/cv_only_draft.md").read_text()
        self.assertIn("scan_rate_v_s",document)
        self.assertIn("尚未接入工作站",document)
        self.assertEqual(saved["plan"]["steps"][0]["integration_status"],"not_integrated")

    def test_invalid_cv_values_are_rejected(self):
        for patch in ({"upper_potential_v":-1},{"initial_potential_v":2},{"scan_rate_v_s":0},
                      {"cycles":True},{"cycles":1.5},{"potential_step_v":2},{"current_range_a":0},
                      {"quiet_time_s":-1},{"reference_electrode":"中文"},{"initial_potential_v":10**400}):
            sequence = self.cv_sequence()
            sequence["steps"][0]["parameters"].update(patch)
            with self.subTest(patch=patch):
                self.assertEqual(self.request("POST","/api/preview",{"sequence":sequence})[0],422)

    def test_unknown_measurement_modes_and_execution_flags_are_rejected(self):
        for patch in ({"technique":"impedance_spectroscopy"},{"execution_supported":True},{"operation":"inject"}):
            sequence = self.cv_sequence()
            sequence["steps"][0].update(patch)
            self.assertEqual(self.request("POST","/api/preview",{"sequence":sequence})[0],422)

    def test_measurement_does_not_bypass_valve_routing_or_legacy_runner_validation(self):
        invalid = {**self.sequence,"steps":[self.measurement(),self.sequence["steps"][1]]}
        self.assertEqual(self.request("POST","/api/preview",{"sequence":invalid})[0],422)
        mixed = copy.deepcopy(self.sequence)
        mixed["steps"].insert(3,self.measurement())
        mixed["id"] = "blocked_legacy_cv"
        path = self.lab/"config/sequences/blocked_legacy_cv.json"
        path.write_text(json.dumps(mixed))
        with self.assertRaises(ValueError):
            self.repo.compiler.load_configuration(path,self.lab/"config/templates")
        self.assertNotIn("lab_automation.transport",sys.modules)
        self.assertNotIn("lab_automation.runner",sys.modules)

    def test_direction_and_full_cycles_have_consistent_estimates(self):
        parameters = self.measurement()["parameters"]
        self.assertEqual(validate_cv({**parameters,"cycles":3}),65)
        self.assertEqual(validate_cv({**parameters,"initial_scan_direction":"negative"}),25)
