import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from pump_console.measurement_store import MeasurementStore, thin_points
import test_api


def synthetic_step():
    return dict(id='cv_step', description='Synthetic measurement', parameters={})


def synthetic_rows():
    return [dict(time_s=i / 10, potential_v=(i - 50) / 1000, current_a=(i - 50) / 1000000,
                 adc_mean=i, dac_code=i, sensitivity=3, current_gain=1, dac_vref_mv=120, sample_count=100)
            for i in range(100)]


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = MeasurementStore(self.tmp.name)

    def writer(self, sequence='synthetic_cv'):
        writer = self.store.create('synthetic-run', sequence, synthetic_step(), 'synthetic_admin', {})
        self.addCleanup(lambda: None if writer.closed else writer.finish('failed'))
        return writer

    def test_private_durable_rows_snapshots_and_downloads(self):
        writer = self.writer(); writer.append(synthetic_rows()); writer.finish('completed', stop_confirmed=True)
        store = MeasurementStore(self.tmp.name)
        item = store.get(writer.metadata['id'])
        self.assertEqual((item['point_count'], item['sample_count']), (100, 10000))
        self.assertEqual(len(store.points(item['id'], 1000)['points']), 100)
        self.assertTrue(item['nominal_conversion'])
        self.assertEqual(os.stat(store.root).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(store.database).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(store.folder(item['id']) / 'points.csv').st_mode & 0o777, 0o600)
        file, filename = store.download(item['id'], 'csv')
        self.assertEqual(len(file.read_text().splitlines()), 101)
        self.assertTrue(filename.endswith('.csv'))

    def test_running_data_uses_bounded_snapshot_and_does_not_download_unfinished_csv(self):
        writer = self.writer(); writer.append(synthetic_rows()); writer.publish(force=True, state='running')
        latest = self.store.latest()
        self.assertEqual(latest['metadata']['point_count'], 100)
        self.assertLessEqual(len(latest['points']), 1800)
        with self.assertRaises(ValueError): self.store.download(writer.metadata['id'], 'csv')
        self.assertTrue(self.store.download(writer.metadata['id'], 'json')[0].exists())

    def test_chart_thinning_keeps_order_endpoints_and_global_extrema(self):
        values = [[i, i, i % 73, 1000 if i == 100 else -500 if i == 200 else 0] for i in range(5000)]
        thin = thin_points(values, 120)
        self.assertLessEqual(len(thin), 120)
        self.assertEqual(thin[0], values[0]); self.assertEqual(thin[-1], values[-1])
        self.assertIn(values[100], thin); self.assertIn(values[200], thin)
        self.assertEqual([p[0] for p in thin], sorted(p[0] for p in thin))

    def test_query_paging_and_literal_wildcards(self):
        one = self.writer('first_run'); one.finish('completed')
        two = self.writer('second_run'); two.finish('failed')
        self.assertEqual(self.store.list(state='failed')['total'], 1)
        self.assertEqual(self.store.list(query='first_run')['total'], 1)
        self.assertEqual(self.store.list(query='%')['total'], 0)
        self.assertEqual(self.store.list(limit=1)['next_offset'], 1)
        self.assertEqual(len(self.store.list(limit=1, offset=1)['measurements']), 1)

    def test_unsafe_paths_unknown_formats_and_invalid_limits_are_rejected(self):
        writer = self.writer(); writer.finish('completed')
        for name in ('../../security/auth.sqlite3', 'cv-not-an-id', '__proto__'):
            with self.assertRaises(ValueError): self.store.get(name)
        with self.assertRaises(ValueError): self.store.download(writer.metadata['id'], '../metadata.json')
        with self.assertRaises(ValueError): self.store.points(writer.metadata['id'], 10001)
        with self.assertRaises(ValueError): self.store.list(limit=0)


class MeasurementDataApiTests(unittest.TestCase):
    setUpBase = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    request = test_api.ApiTests.request

    def setUp(self):
        self.setUpBase()
        writer = self.repo.measurements.create('synthetic-run', 'synthetic_cv', synthetic_step(), 'test_admin', {})
        writer.append(synthetic_rows()); writer.finish('completed', stop_confirmed=True)
        self.dataset = writer.metadata['id']

    def test_authenticated_list_detail_chart_and_download(self):
        self.assertEqual(self.request('GET', '/api/measurements')[1]['total'], 1)
        code, detail = self.request('GET', '/api/measurements/' + self.dataset)
        self.assertEqual(code, 200); self.assertEqual(detail['measurement']['point_count'], 100)
        self.assertTrue(detail['chart']['points'])
        code, content = self.request('GET', '/api/measurements/' + self.dataset + '/download?format=csv')
        self.assertEqual(code, 200); self.assertIn('potential_v_nominal', content)

    def test_viewer_can_read_but_cannot_prepare_instrument_execution(self):
        self.auth.create_user('synthetic_viewer', 'Synthetic viewer password 2026', role='viewer', temporary=False)
        self.token, session = self.auth.login('synthetic_viewer', 'Synthetic viewer password 2026', '127.0.0.1')
        self.csrf = session['csrf_token']
        self.assertEqual(self.request('GET', '/api/measurements/' + self.dataset)[0], 200)
        self.assertEqual(self.request('POST', '/api/execution/prepare', {'sequence': self.sequence})[0], 403)

    def test_all_measurement_routes_require_session(self):
        for suffix in ('', '/' + self.dataset, '/' + self.dataset + '/points', '/' + self.dataset + '/download?format=raw'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.request('GET', '/api/measurements' + suffix, headers={'Cookie': ''})[0], 401)

    def test_malformed_filters_paths_and_nonexistent_records(self):
        for suffix in ('?limit=abc', '?limit=101', '?query=a&query=b', '/' + self.dataset + '/points?limit=1', '/../../security/auth.sqlite3'):
            with self.subTest(suffix=suffix):
                self.assertIn(self.request('GET', '/api/measurements' + suffix)[0], (400, 404))
        self.assertEqual(self.request('GET', '/api/measurements/cv-' + 'f' * 32)[0], 404)

    def test_status_contains_passive_chart_snapshot(self):
        _, status = self.request('GET', '/api/status')
        self.assertEqual(status['measurement']['metadata']['id'], self.dataset)
        self.assertEqual(status['measurement']['metadata']['point_count'], 100)
        self.assertFalse(status['hardware_execution_enabled'])


if __name__ == '__main__': unittest.main()
