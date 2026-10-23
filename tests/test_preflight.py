"""Required preflight blocks and the server's explicit, idempotent stage gate."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import test_api
from pump_console.auth import COOKIE_NAME
from pump_console.execution import write_private
from pump_console.preflight import default_preflight, require_preflight
from pump_console.repository import ApiError


class PreflightTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    request = test_api.ApiTests.request

    def native(self):
        return {'schema_version':3,'id':'preflight_test','description':'Synthetic preflight validation',
                'blocks':[default_preflight(),{'id':'water','description':'Water injection','steps':[
                    {'device':'peristaltic_1','operation':'inject','fluid':'water','volume_ml':1}]}]}

    def test_missing_duplicate_late_or_name_only_preflight_is_rejected_for_preview_save_and_prepare(self):
        self.repo.execution.enabled=True
        base=self.native()
        missing={**base,'blocks':base['blocks'][1:]}
        late={**base,'blocks':list(reversed(base['blocks']))}
        duplicate={**base,'blocks':[base['blocks'][0],{**base['blocks'][0],'id':'second_preflight'},base['blocks'][1]]}
        fake={**base,'blocks':[{**base['blocks'][1],'id':'experiment_preparation'}]}
        legacy={key:value for key,value in self.sequence.items() if key!='preflight'}
        with patch.object(self.repo.execution,'launcher') as launcher:
            for value in (missing,late,duplicate,fake,legacy):
                for route in ('/api/preview','/api/experiments','/api/execution/prepare'):
                    self.assertEqual(self.request('POST',route,{'sequence':value})[0],422)
            launcher.assert_not_called()

    def test_preflight_steps_cannot_be_reordered_forged_confirmed_or_given_arbitrary_commands(self):
        invalid=[]
        for index,change in [(0,{'operation':'initialize'}),(0,{'command':'R'}),(1,{'volume_ml':1}),(1,{'route':'external_path'}),(2,{'confirmed':True})]:
            value=self.native();value['blocks'][0]['steps'][index].update(change);invalid.append(value)
        value=self.native();value['blocks'][0]['steps'].reverse();invalid.append(value)
        for value in invalid:self.assertEqual(self.request('POST','/api/preview',{'sequence':value})[0],422)

    def test_preflight_metadata_is_separate_from_real_controller_actions_and_cell_budget(self):
        code,result=self.request('POST','/api/preview',{'sequence':self.native()})
        self.assertEqual(code,200,result)
        self.assertEqual(len(result['plan']['steps']),1)
        self.assertFalse(result['preflight']['requires_syringe'])
        self.assertTrue(result['preflight']['operator_confirmation_required'])
        self.assertEqual(result['cell_safety']['peak_volume_ml'],1)
        self.assertEqual(result['preflight']['predrain_volume_ml'],40)
        self.assertEqual(result['plan']['steps'][0]['id'],'block_002_step_001')
        self.assertFalse(result['hardware_executed'])

    def test_route_is_frozen_in_the_block_and_cannot_be_changed_by_an_execution_request(self):
        self.repo.execution.enabled=True
        prepared=self.repo.execution.prepare(self.native(),'test_admin')
        with self.assertRaises(ApiError) as caught:
            self.repo.execution.drain_cell(prepared['run_id'],prepared['version'],'test_admin','waste_2',True,'different_route')
        self.assertEqual(caught.exception.code,'preflight_route_mismatch')

    def test_workflow_advances_only_explicit_stages_and_never_treats_json_as_empty_confirmation(self):
        self.repo.execution.enabled=True
        sequence=self.native();sequence['blocks'][1]['steps'][0]={'device':'syringe_c','operation':'inject','fluid':'electrolyte','volume_ml':1}
        manager=self.repo.execution;prepared=manager.prepare(sequence,'test_admin')
        calls=[]
        state={'calibration':{'required':True,'state':'required'},'cell':{'required':True,'state':'required'}}
        def home(*args):calls.append('home');state['calibration']={'required':True,'state':'passed'}
        def drain(*args):calls.append('drain');state['cell']={'required':True,'state':'awaiting_confirmation'}
        def confirm(*args):calls.append('confirm');state['cell']={'required':True,'state':'passed'}
        def start(*args):
            calls.append('start');folder,job=manager._job(prepared['run_id']);job['state']='starting';write_private(folder/'job.json',job)
        args=(prepared['run_id'],prepared['version'],'test_admin')
        with patch.object(manager,'_calibration_state',side_effect=lambda *args:deepcopy(state['calibration'])),patch.object(manager,'_cell_state',side_effect=lambda *args:deepcopy(state['cell'])),patch.object(manager,'calibrate',side_effect=home),patch.object(manager,'drain_cell',side_effect=drain),patch.object(manager,'confirm_empty',side_effect=confirm),patch.object(manager,'start',side_effect=start),patch.object(manager,'_summary',return_value={'state':'running','controller_alive':True}):
            with self.assertRaises(ApiError):manager.advance_workflow(*args)
            self.assertEqual(calls,[])
            manager.advance_workflow(*args,True,False)
            self.assertEqual(calls,['home'])
            for _ in range(3):manager.preparation_status(prepared['run_id'])
            self.assertEqual(calls,['home'])
            manager.advance_workflow(*args)
            self.assertEqual(calls,['home','drain'])
            manager.advance_workflow(*args)
            self.assertEqual(calls,['home','drain'])
            manager.advance_workflow(*args,False,True)
            self.assertEqual(calls,['home','drain','confirm','start'])
            manager.advance_workflow(*args,False,True)
            self.assertEqual(calls.count('start'),1)

    def test_advance_is_admin_only_requires_csrf_and_cannot_accept_premature_confirmation(self):
        self.repo.execution.enabled=True
        prepared=self.repo.execution.prepare(self.native(),'test_admin')
        payload={'run_id':prepared['run_id'],'version':prepared['version'],'confirm_discharge':True,'empty_confirmed':False}
        self.auth.create_user('observer','Synthetic viewer passphrase 2026',role='viewer',temporary=False)
        token,session=self.auth.login('observer','Synthetic viewer passphrase 2026','127.0.0.1')
        for headers,status in [({'Cookie':''},401),({'Cookie':COOKIE_NAME+'='+token,'X-CSRF-Token':session['csrf_token'],'X-Role':'admin'},403),({'X-CSRF-Token':''},403),({'X-Pump-Console':''},403)]:
            self.assertEqual(self.request('POST','/api/execution/advance',payload,headers)[0],status)
        with patch.object(self.repo.execution,'launcher') as launcher:
            payload['empty_confirmed']=True
            self.assertEqual(self.request('POST','/api/execution/advance',payload)[0],409)
            launcher.assert_not_called()
            self.assertEqual(self.request('POST','/api/execution/advance',{**payload,'empty_confirmed':False})[0],409)
