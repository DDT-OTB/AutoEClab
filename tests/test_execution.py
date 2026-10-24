"""Automation authorization and immutable preparation, without serial devices."""
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from pump_console.auth import AuthStore, COOKIE_NAME
from pump_console.execution import write_private, signal_worker
from pump_console.repository import ApiError, LabRepository
from pump_console.server import make_server


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lab = self.root / 'lab'
        shutil.copytree(ROOT / 'tests/fixtures/demo-lab', self.lab)
        self.repo = LabRepository(self.lab, self.root / 'data', execution_enabled=True)
        self.manager = self.repo.execution
        self.sequence = json.loads((self.lab / 'config/sequences/demo_syringe.json').read_text())

    def tearDown(self):
        self.temporary.cleanup()

    def assert_api_error(self, code, function, *arguments):
        with self.assertRaises(ApiError) as caught:
            function(*arguments)
        self.assertEqual(caught.exception.code, code)

    def prepare(self):
        return self.manager.prepare(self.sequence, 'synthetic_admin')

    def started_job(self, prepared):
        folder, job = self.manager._job(prepared['run_id'])
        job.update(state='starting', pid=987654321, process_identity='fixture-identity', started_by='synthetic_admin')
        write_private(folder / 'job.json', job)
        write_private(self.manager.directory / 'current.json', {'run_id': job['run_id']})
        return folder, job

    def test_disabled_by_default_and_capabilities_are_truthful(self):
        repository = LabRepository(self.lab, self.root / 'disabled')
        for function, arguments in [(repository.execution.prepare, (self.sequence, 'admin')), (repository.execution.start, ('anything', 'version', 'admin')), (repository.execution.stop, ('anything', 'admin'))]:
            self.assert_api_error('hardware_disabled', function, *arguments)
        self.assertFalse(repository.health()['hardware_execution_enabled'])
        self.assertTrue(self.repo.health()['hardware_execution_enabled'])
        self.assertIsNone(self.repo.status()['motion_commands_sent'])

    def test_preparation_is_private_immutable_and_does_not_start_a_controller(self):
        before = {path.name: path.read_bytes() for path in (self.lab / 'config/sequences').glob('*')}
        prepared = self.prepare()
        folder, job = self.manager._job(prepared['run_id'])
        original = (folder / 'snapshot.json').read_bytes()
        self.sequence['steps'][0]['volume_ml'] = 7
        self.assertEqual((folder / 'snapshot.json').read_bytes(), original)
        self.assertEqual(job['state'], 'prepared')
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual((folder / 'snapshot.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse(prepared['hardware_executed'])
        self.assertIsNone(self.manager.snapshot()['current'])
        self.assertEqual(before, {path.name: path.read_bytes() for path in (self.lab / 'config/sequences').glob('*')})

    def test_measurement_and_missing_calibration_rejected_before_launch(self):
        measurement = json.loads((ROOT / 'config/examples/demo_cv.json').read_text())
        self.assert_api_error('instrument_not_integrated', self.manager.prepare, measurement, 'admin')
        file = self.lab / 'config/templates/peristaltic_1.json'
        template = json.loads(file.read_text())
        template['device']['calibration']['flow_ml_min'] = None
        file.write_text(json.dumps(template))
        sequence = {**self.sequence, 'steps': [{'device': 'peristaltic_1', 'operation': 'inject', 'volume_ml': 1, 'fluid': 'water'}]}
        self.assert_api_error('configuration_incomplete', self.manager.prepare, sequence, 'admin')

    def test_versions_expiry_snapshot_and_template_changes_rejected(self):
        prepared = self.prepare()
        self.assert_api_error('version_conflict', self.manager.start, prepared['run_id'], 'wrong', 'admin')
        folder, job = self.manager._job(prepared['run_id'])
        job['prepared_at'] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        write_private(folder / 'job.json', job)
        self.assert_api_error('preparation_expired', self.manager.start, prepared['run_id'], prepared['version'], 'admin')
        job['prepared_at'] = datetime.now(timezone.utc).isoformat()
        write_private(folder / 'job.json', job)
        snapshot = json.loads((folder / 'snapshot.json').read_text())
        snapshot['sequence']['description'] = 'Tampered synthetic snapshot'
        write_private(folder / 'snapshot.json', snapshot)
        self.assert_api_error('version_conflict', self.manager.start, prepared['run_id'], prepared['version'], 'admin')
        prepared = self.prepare()
        file = self.lab / 'config/templates/syringe_c.json'
        template = json.loads(file.read_text()); template['description'] = 'Changed synthetic template'
        file.write_text(json.dumps(template))
        self.assert_api_error('templates_changed', self.manager.start, prepared['run_id'], prepared['version'], 'admin')

    def test_missing_ports_do_not_create_a_current_run(self):
        prepared = self.prepare()
        self.assert_api_error('serial_port_unavailable', self.manager.start, prepared['run_id'], prepared['version'], 'admin')
        self.assertIsNone(self.manager.snapshot()['current'])

    def test_uncalibrated_syringe_cannot_start_even_if_q_could_report_ready(self):
        prepared = self.prepare()
        self.assertEqual(prepared['calibration'], {'required': True, 'state': 'required', 'run_id': None})
        with patch.object(self.manager, '_check_ports'):
            self.assert_api_error('syringe_calibration_required', self.manager.start, prepared['run_id'], prepared['version'], 'admin')
        self.assert_api_error('discharge_confirmation_required', self.manager.calibrate, prepared['run_id'], prepared['version'], 'admin', False)

    def test_older_preparations_cannot_bypass_the_new_calibration_requirement(self):
        prepared = self.prepare()
        folder, job = self.manager._job(prepared['run_id'])
        job.pop('requires_syringe_calibration'); write_private(folder / 'job.json', job)
        with patch.object(self.manager, '_check_ports'):
            self.assert_api_error('syringe_calibration_required', self.manager.start, prepared['run_id'], prepared['version'], 'admin')

    def test_same_start_is_idempotent_and_another_run_is_blocked(self):
        prepared = self.prepare()
        self.started_job(prepared)
        with patch('pump_console.execution.worker_identity', return_value='fixture-identity'):
            result = self.manager.start(prepared['run_id'], prepared['version'], 'admin')
            self.assertTrue(result['already_started'])
            second = self.prepare()
            self.assert_api_error('execution_busy', self.manager.start, second['run_id'], second['version'], 'admin')

    def test_stop_is_idempotent_and_failed_signal_allows_retry(self):
        prepared = self.prepare()
        self.started_job(prepared)
        with patch('pump_console.execution.worker_identity', return_value='fixture-identity'), patch('pump_console.execution.signal_worker') as send:
            send.side_effect = PermissionError('synthetic signal failure')
            self.assert_api_error('stop_request_failed', self.manager.stop, prepared['run_id'], 'admin')
            self.assertIsNone(self.manager.snapshot()['current']['stop_requested_at'])
            send.side_effect = None
            result = self.manager.stop(prepared['run_id'], 'admin')
            self.assertEqual(result['execution']['state'], 'stopping')
            self.manager.stop(prepared['run_id'], 'admin')
            self.assertEqual(send.call_count, 2)
        self.assert_api_error('execution_changed', self.manager.stop, 'different-run', 'admin')

    def test_pid_reuse_never_signals_an_unrelated_process(self):
        prepared = self.prepare()
        _, job = self.started_job(prepared)
        with patch('pump_console.execution.os.pidfd_open', return_value=91, create=True), patch('pump_console.execution.worker_identity', return_value='new-process'), patch('pump_console.execution.os.close') as close, patch('pump_console.execution.signal.pidfd_send_signal', create=True) as send:
            with self.assertRaises(ProcessLookupError):
                signal_worker(job)
            send.assert_not_called()
            close.assert_called_once_with(91)


class AutomationAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lab = self.root / 'lab'
        shutil.copytree(ROOT / 'tests/fixtures/demo-lab', self.lab)
        self.repo = LabRepository(self.lab, self.root / 'data', execution_enabled=True)
        self.sequence = json.loads((self.lab / 'config/sequences/demo_syringe.json').read_text())
        self.auth = AuthStore(self.root / 'security/auth.sqlite3')
        self.sessions = {}
        for role in ('admin', 'viewer'):
            self.auth.create_user('synthetic_' + role, 'synthetic secret 2026', role=role, temporary=False)
            token, session = self.auth.login('synthetic_' + role, 'synthetic secret 2026', '127.0.0.1')
            self.sessions[role] = (token, session['csrf_token'])
        self.static = self.root / 'dist'; self.static.mkdir()
        (self.static / 'index.html').write_text('<html>synthetic automation</html>')
        self.server = make_server('127.0.0.1', 0, self.repo, self.static, self.auth)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temporary.cleanup()

    def request(self, method, path, body=None, role='admin', protection=True, csrf=True, extra=None):
        headers = {'Content-Type': 'application/json'}
        if role:
            token, token_csrf = self.sessions[role]
            headers['Cookie'] = COOKIE_NAME + '=' + token
            if csrf: headers['X-CSRF-Token'] = token_csrf
        if protection: headers['X-Pump-Console'] = '1'
        headers.update(extra or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=6)
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse(); raw = response.read(); connection.close()
        return response.status, json.loads(raw)

    def test_viewer_and_anonymous_cannot_prepare_start_or_stop(self):
        with patch.object(self.repo.execution, 'prepare') as prepare, patch.object(self.repo.execution, 'start') as start, patch.object(self.repo.execution, 'stop') as stop:
            for action in ('prepare', 'start', 'debug-start', 'stop', 'calibrate', 'drain', 'confirm-empty'):
                self.assertEqual(self.request('POST', '/api/execution/' + action, {}, role='viewer')[0], 403)
                self.assertEqual(self.request('POST', '/api/execution/' + action, {}, role=None)[0], 401)
            prepare.assert_not_called(); start.assert_not_called(); stop.assert_not_called()
        self.assertEqual(self.request('GET', '/api/execution', role='viewer')[0], 200)
        self.assertEqual(self.request('GET', '/api/execution', role=None)[0], 401)

    def test_admin_still_requires_csrf_request_protection_and_exact_fields(self):
        for action in ('prepare', 'start', 'debug-start', 'stop', 'calibrate', 'drain', 'confirm-empty'):
            path = '/api/execution/' + action
            self.assertEqual(self.request('POST', path, {}, csrf=False)[0], 403)
            self.assertEqual(self.request('POST', path, {}, protection=False)[0], 403)
            self.assertEqual(self.request('POST', path, {}, extra={'Origin': 'https://untrusted.example'})[0], 403)
            self.assertEqual(self.request('POST', path, {'pid': 1, 'command': 'untrusted'})[0], 400)
        for path in ('/api/execute', '/api/devices/syringe_c/initialize', '/api/devices/peristaltic_1/command'):
            self.assertEqual(self.request('POST', path, {})[0], 403)

    def test_admin_preparation_and_failed_port_check_are_audited_without_motion(self):
        code, prepared = self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})
        self.assertEqual(code, 200)
        self.assertFalse(prepared['hardware_executed'])
        code, result = self.request('POST', '/api/execution/start', {'run_id': prepared['run_id'], 'version': prepared['version']})
        self.assertEqual(code, 422)
        self.assertEqual(result['error'], 'serial_port_unavailable')
        with self.auth.db() as database:
            audit = [dict(row) for row in database.execute("SELECT event,username FROM audit WHERE event LIKE 'automation_%'")]
        self.assertTrue(any(row['event'].startswith('automation_prepare_accepted/') for row in audit))
        self.assertTrue(any(row['event'] == 'automation_start_requested' for row in audit))
        self.assertTrue(all(row['username'] == 'synthetic_admin' for row in audit))
        self.assertIsNone(self.repo.execution.snapshot()['current'])

    def test_client_cannot_override_capacity_or_skip_startup_preparation(self):
        self.assertEqual(self.request('POST','/api/execution/prepare',{'sequence':self.sequence,'safety_policy':{'capacity_ml':1000}})[0],400)
        _,prepared=self.request('POST','/api/execution/prepare',{'sequence':self.sequence})
        self.assertEqual(self.request('POST','/api/execution/start',{'run_id':prepared['run_id'],'version':prepared['version'],'skip_preflight':True})[0],400)
