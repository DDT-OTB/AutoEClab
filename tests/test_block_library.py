"""Template CRUD uses private files, optimistic versions and administrator APIs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_api
from pump_console.auth import COOKIE_NAME
from pump_console.measurement import measurement_templates
from pump_console.repository import ApiError, LabRepository
from pump_console.preflight import default_preflight
from pump_console.workflow import function_block_templates, import_block

ROOT = Path(__file__).resolve().parents[1]


class BlockLibraryTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    request = test_api.ApiTests.request

    def document(self, name='custom_rinse'):
        value = deepcopy(next(block for block in function_block_templates() if block['id']=='water_rinse'))
        value['id'] = name
        return value

    def saved(self, name='custom_rinse'):
        code, saved = self.request('POST', '/api/function-blocks', {'block': self.document(name)})
        self.assertEqual(code, 200, saved)
        return saved

    def test_save_reload_and_restart_keep_custom_templates_outside_published_defaults(self):
        defaults = {path: path.read_bytes() for path in (ROOT/'config/function_blocks').glob('*.json')}
        saved = self.saved()
        self.assertEqual(saved['source'], 'custom')
        self.assertFalse(saved['hardware_executed'])
        self.assertEqual(saved['step_count'], 3)
        code, loaded = self.request('GET', '/api/function-blocks/custom_rinse')
        self.assertEqual(code, 200)
        self.assertEqual(loaded['block'], self.document())
        library = self.request('GET', '/api/function-blocks')[1]
        self.assertEqual(len(library['blocks']), 5)
        self.assertEqual(len(saved['version']), 64)
        for block in library['blocks']:
            import_block(block)
        restored = LabRepository(self.lab, self.repo.data_root)
        self.assertEqual(restored.block_library.get('custom_rinse'), loaded)
        self.assertEqual(restored.block_library.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(restored.block_library.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(defaults, {path:path.read_bytes() for path in defaults})
        self.assertNotIn('lab_automation.transport', sys.modules)

    def test_builtin_templates_can_be_edited_renamed_and_deleted_with_private_overrides(self):
        entry = self.request('GET', '/api/function-blocks/water_rinse')[1]
        document = deepcopy(entry['block']);document['steps'][0]['volume_ml'] = 8
        code, updated = self.request('POST', '/api/function-blocks/water_rinse', {'block':document, 'expected_version':entry['version']})
        self.assertEqual(code, 200, updated)
        self.assertEqual(updated['source'], 'modified')
        self.assertEqual(updated['block']['steps'][0]['volume_ml'], 8)
        document['id'] = 'renamed_rinse'
        code, renamed = self.request('POST', '/api/function-blocks/water_rinse', {'block':document, 'expected_version':updated['version']})
        self.assertEqual(code, 200, renamed)
        self.assertEqual(self.request('GET', '/api/function-blocks/water_rinse')[0], 404)
        code, deleted = self.request('DELETE', '/api/function-blocks/renamed_rinse', {'expected_version':renamed['version']})
        self.assertEqual(code, 200, deleted)
        self.assertFalse(deleted['hardware_executed'])
        restored = LabRepository(self.lab, self.repo.data_root)
        ids = [block['id'] for block in restored.block_library.list()['blocks']]
        self.assertNotIn('water_rinse', ids)
        self.assertNotIn('renamed_rinse', ids)
        self.assertEqual(len(list((self.repo.block_library.directory/'backups').iterdir())), 3)

    def test_missing_versions_stale_writes_and_name_collisions_cannot_overwrite_templates(self):
        saved = self.saved()
        document = deepcopy(saved['block']);document['steps'][0]['volume_ml'] = 9
        self.assertEqual(self.request('POST','/api/function-blocks',{'block':document})[0],409)
        self.assertEqual(self.request('POST','/api/function-blocks/custom_rinse',{'block':document})[0],400)
        self.assertEqual(self.request('POST','/api/function-blocks/custom_rinse',{'block':document,'expected_version':'wrong'})[0],409)
        code, updated = self.request('POST','/api/function-blocks/custom_rinse',{'block':document,'expected_version':saved['version']})
        self.assertEqual(code,200,updated)
        before = self.repo.block_library.path.read_bytes()
        self.assertEqual(self.request('DELETE','/api/function-blocks/custom_rinse',{'expected_version':saved['version']})[0],409)
        renamed = {**document,'id':'water_rinse'}
        self.assertEqual(self.request('POST','/api/function-blocks/custom_rinse',{'block':renamed,'expected_version':updated['version']})[0],409)
        self.assertEqual(before,self.repo.block_library.path.read_bytes())

    def test_only_admins_with_csrf_and_request_protection_can_change_templates(self):
        saved = self.saved()
        self.auth.create_user('observer','Synthetic viewer password 2026',role='viewer',temporary=False)
        token,session = self.auth.login('observer','Synthetic viewer password 2026','127.0.0.1')
        viewer={'Cookie':COOKIE_NAME+'='+token,'X-CSRF-Token':session['csrf_token'],'X-Role':'admin'}
        self.assertEqual(self.request('GET','/api/function-blocks',headers=viewer)[0],200)
        requests=[('POST','/api/function-blocks',{'block':self.document('another_block')}),
                  ('POST','/api/function-blocks/custom_rinse',{'block':self.document(),'expected_version':saved['version']}),
                  ('DELETE','/api/function-blocks/custom_rinse',{'expected_version':saved['version']})]
        for method,path,payload in requests:
            for headers,status in [(viewer,403),({'Cookie':''},401),({'X-CSRF-Token':''},403),({'X-Pump-Console':''},403),({'Origin':'https://untrusted.example'},403)]:
                with self.subTest(method=method,headers=headers):
                    self.assertEqual(self.request(method,path,payload,headers)[0],status)
        self.assertEqual(self.repo.block_library.get('custom_rinse')['version'],saved['version'])

    def test_template_edits_do_not_modify_experiment_instances_or_prepared_execution_snapshots(self):
        sequence=json.loads((ROOT/'config/examples/demo_blocks.json').read_text())
        self.assertEqual(self.request('POST','/api/experiments',{'sequence':sequence})[0],200)
        path=self.lab/'config/sequences/demo_block_experiment.json'
        before=path.read_bytes()
        self.repo.execution.enabled=True
        prepared=self.repo.execution.prepare(sequence,'test_admin')
        folder,_=self.repo.execution._job(prepared['run_id'])
        snapshot=(folder/'snapshot.json').read_bytes()
        entry=self.repo.block_library.get('water_rinse')
        document=deepcopy(entry['block']);document['steps'][0]['volume_ml']=8
        self.repo.block_library.save(document,'water_rinse',entry['version'])
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual((folder/'snapshot.json').read_bytes(),snapshot)
        self.assertEqual(sequence['blocks'][1]['steps'][0]['volume_ml'],10)

    def test_invalid_action_fields_fluids_volumes_or_cv_parameters_never_persist(self):
        invalid=[{**self.document(),'id':'../outside'},{**self.document(),'description':'中文'},
                 {**self.document(),'steps':[]},{**self.document(),'command':'arbitrary'},
                 {**self.document(),'steps':[{'device':'unknown','operation':'inject','volume_ml':1}]},
                 {**self.document(),'steps':[{'device':'peristaltic_1','operation':'inject','fluid':'water','volume_ml':True}]}]
        for change in ({'volume_ml':0},{'fluid':'electrolyte'},{'operation':'withdraw'},{'execution_supported':True}):
            document=self.document();document['steps'][0].update(change);invalid.append(document)
        for document in invalid:
            self.assertEqual(self.request('POST','/api/function-blocks',{'block':document})[0],422)
        self.assertFalse(self.repo.block_library.path.exists())

    def test_drain_blocks_can_reuse_external_route_but_complete_experiments_still_require_it(self):
        document={**self.document('drain_only'),'steps':[{'device':'peristaltic_2','operation':'withdraw','volume_ml':10,'fluid':'waste_liquid'}]}
        code,saved=self.request('POST','/api/function-blocks',{'block':document})
        self.assertEqual(code,200,saved)
        self.assertEqual(saved['block']['steps'],document['steps'])
        sequence={'schema_version':3,'id':'missing_route','description':'Route-dependent block', 'blocks':[default_preflight(),import_block(document)]}
        self.assertEqual(self.request('POST','/api/preview',{'sequence':sequence})[0],422)

    def test_cv_templates_remain_planning_only_and_capacity_is_checked_for_the_combined_experiment(self):
        cv=measurement_templates()['electrochemical_station']
        document={**self.document('cv_template'),'steps':[{'device':'electrochemical_station','operation':'measure',
                   'technique':'cyclic_voltammetry','parameters':cv['action']['default_parameters']}]}
        code,saved=self.request('POST','/api/function-blocks',{'block':document})
        self.assertEqual(code,200,saved)
        self.repo.execution.enabled=True
        sequence={'schema_version':3,'id':'cv_blocks','description':'CV draft','blocks':[default_preflight(),import_block(document)]}
        code,error=self.request('POST','/api/execution/prepare',{'sequence':sequence})
        self.assertEqual(code,422);self.assertEqual(error['error'],'instrument_not_integrated')
        document['id']='overfull_template'
        document['steps']=[{'device':'peristaltic_1','operation':'inject','fluid':'water','volume_ml':41}]
        self.assertEqual(self.request('POST','/api/function-blocks',{'block':document})[0],200)
        sequence['blocks']=[default_preflight(),import_block(document)]
        code,error=self.request('POST','/api/execution/prepare',{'sequence':sequence})
        self.assertEqual(code,422);self.assertEqual(error['error'],'cell_capacity_exceeded')

    def test_two_admin_edits_from_the_same_version_have_one_winner(self):
        saved=self.saved()
        def change(volume):
            document=deepcopy(saved['block']);document['steps'][0]['volume_ml']=volume
            try:self.repo.block_library.save(document,saved['id'],saved['version']);return 200
            except ApiError as error:return error.status
        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(sorted(workers.map(change,[7,8])),[200,409])

    def test_corrupt_state_and_symlinks_are_not_silently_overwritten(self):
        saved=self.saved();path=self.repo.block_library.path
        path.write_text('{invalid json')
        self.assertEqual(self.request('POST','/api/function-blocks',{'block':self.document('another')})[0],500)
        self.assertEqual(path.read_text(),'{invalid json')
        path.unlink()
        outside=self.root/'outside.json';outside.write_text('Unrelated file')
        path.symlink_to(outside)
        self.assertEqual(self.request('POST','/api/function-blocks',{'block':self.document('another')})[0],400)
        self.assertEqual(outside.read_text(),'Unrelated file')
        self.assertEqual(self.request('GET','/api/function-blocks/../outside')[0],400)

    def test_failed_atomic_write_keeps_the_previous_library_and_records_a_backup(self):
        saved=self.saved();library=self.repo.block_library;before=library.path.read_bytes();write=library._write
        def fail_final(path,data):
            if path==library.path:raise OSError('Synthetic disk failure')
            return write(path,data)
        with patch.object(library,'_write',side_effect=fail_final):
            self.assertEqual(self.request('DELETE','/api/function-blocks/custom_rinse',{'expected_version':saved['version']})[0],500)
        self.assertEqual(library.path.read_bytes(),before)
