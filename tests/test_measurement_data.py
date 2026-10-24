import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import math
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from pump_console.measurement_store import MeasurementStore, MeasurementBusyError, MeasurementConflictError, StreamingTrace, StreamingTrend,thin_points,CHART_SAMPLER,CHART_POINT_LIMIT,TREND_METHOD
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
        self.assertLessEqual(len(latest['points']), CHART_POINT_LIMIT)
        self.assertEqual(latest['trend_method'], TREND_METHOD)
        self.assertTrue(latest['trend_segments'])
        with self.assertRaises(ValueError): self.store.download(writer.metadata['id'], 'csv')
        self.assertTrue(self.store.download(writer.metadata['id'], 'json')[0].exists())

    def test_chart_thinning_keeps_order_endpoints_and_global_extrema(self):
        values = [[i, i, i % 73, 1000 if i == 100 else -500 if i == 200 else 0] for i in range(5000)]
        thin = thin_points(values, 120)
        self.assertLessEqual(len(thin), 120)
        self.assertEqual(thin[0], values[0]); self.assertEqual(thin[-1], values[-1])
        self.assertIn(values[100], thin); self.assertIn(values[200], thin)
        self.assertEqual([p[0] for p in thin], sorted(p[0] for p in thin))

    def test_streaming_sampling_preserves_early_middle_and_late_history_and_extrema(self):
        values=[[i+1,i/1000,math.sin(i/1500),1000 if i==100 else -500 if i==50000 else math.sin(i/733)] for i in range(60000)]
        sampler=StreamingTrace()
        for start in range(0,len(values),137):sampler.append(values[start:start+137])
        points=sampler.points();self.assertLessEqual(len(points),CHART_POINT_LIMIT)
        self.assertEqual(points[0],values[0]);self.assertEqual(points[-1],values[-1])
        self.assertIn(values[100],points);self.assertIn(values[50000],points)
        for section in range(3):
            self.assertGreater(sum(section*20000<p[0]<=(section+1)*20000 for p in points),len(points)*.2)
        self.assertTrue(all(point==values[point[0]-1] for point in points))
        self.assertLessEqual(len(sampler.buckets),CHART_POINT_LIMIT//6)

    def test_trend_averages_all_original_points_instead_of_extrema_samples(self):
        values=[[i+1,i/1000,i/10000,100 if i%50==0 else 0] for i in range(12000)]
        trend=StreamingTrend(bucket_limit=100)
        for start in range(0,len(values),137):trend.append(values[start:start+137])
        chart=trend.chart();self.assertEqual(chart['trend_method'],TREND_METHOD)
        self.assertEqual(len(chart['trend_segments']),1)
        segment=chart['trend_segments'][0]
        self.assertEqual(segment[0],values[0]);self.assertEqual(segment[-1],values[-1])
        for point in segment[1:-1]:
            bucket=int((point[0]-1)//trend.width)
            original=values[bucket*trend.width:(bucket+1)*trend.width]
            self.assertAlmostEqual(point[3],sum(p[3] for p in original)/len(original))
        self.assertLess(max(point[3] for point in segment[1:-1]),4)
        self.assertTrue(all(point[1] in {p[1] for p in values} for point in segment))
        one=StreamingTrend(bucket_limit=100);one.append(values)
        self.assertEqual(one.chart(),chart)
        self.assertLessEqual(len(trend.buckets),100)

    def test_trend_keeps_turnaround_anchors_and_never_averages_across_branches(self):
        values=[[1,1,-1,1],[2,1,0,1],[3,1,1,1],[4,1,0,10],[5,1,-1,10]]
        trend=StreamingTrend(bucket_limit=1);trend.append(values)
        segments=trend.chart()['trend_segments']
        self.assertEqual(len(segments),2)
        self.assertEqual(segments[0][-1],values[2]);self.assertEqual(segments[1][0],values[2])
        self.assertTrue(all(point[3]==1 for point in segments[0]))
        self.assertTrue(all(point[3]==10 for point in segments[1][1:]))
        self.assertTrue(all(point[1]==1 for segment in segments for point in segment))

    def test_pathologically_reversing_potentials_disable_only_bounded_display_trend(self):
        trend=StreamingTrend(bucket_limit=24)
        trend.append([[i,i,float(i%2),0] for i in range(4000)])
        self.assertEqual(trend.chart()['trend_unavailable_reason'],'too_many_potential_reversals')
        self.assertEqual(trend.chart()['trend_segments'],[])
        self.assertFalse(trend.buckets);self.assertFalse(trend.ends)

    def test_old_terminal_snapshot_is_rebuilt_once_without_changing_original_data(self):
        writer=self.writer()
        for begin in range(0,12000,200):
            writer.append([dict(time_s=i/1000,potential_v=math.sin(i/300),current_a=math.sin(i/201)*1e-6) for i in range(begin,begin+200)])
        writer.finish('completed',stop_confirmed=True)
        current=self.store.latest();self.assertEqual(current['sampling'],CHART_SAMPLER)
        path=writer.folder/'live.json';legacy=json.loads(path.read_text())
        legacy.pop('sampling');legacy['points']=[legacy['points'][0],legacy['points'][-1]]
        path.write_text(json.dumps(legacy));old_snapshot=path.read_bytes()
        csv=(writer.folder/'points.csv').read_bytes();metadata=(writer.folder/'metadata.json').read_bytes()
        self.store=MeasurementStore(self.tmp.name)
        queries=[];original=self.store.db
        @contextmanager
        def tracked():
            with original() as db:
                db.set_trace_callback(lambda sql:queries.append(sql) if sql.startswith('SELECT point_index,') else None)
                yield db
        with patch.object(self.store,'db',tracked):
            one=self.store.latest();two=self.store.latest()
        self.assertGreater(len(one['points']),len(self.store.points(writer.metadata['id'],1800)['points']))
        self.assertEqual(one['points'],two['points'])
        self.assertEqual(len(queries),1)
        self.assertEqual(one['points'],self.store.points(writer.metadata['id'])['points'])
        self.assertEqual(one['trend_segments'],self.store.points(writer.metadata['id'])['trend_segments'])
        self.assertEqual(one['trend_method'],TREND_METHOD)
        self.assertEqual(path.read_bytes(),old_snapshot)
        self.assertEqual((writer.folder/'points.csv').read_bytes(),csv)
        self.assertEqual((writer.folder/'metadata.json').read_bytes(),metadata)

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

    def test_delete_archives_files_and_keeps_points_and_auditable_metadata(self):
        writer = self.writer(); writer.append(synthetic_rows()); writer.finish('completed', stop_confirmed=True)
        item = self.store.get(writer.metadata['id'])
        original = (writer.folder / 'points.csv').read_bytes()
        result = self.store.delete(item['id'], item['version'], 'synthetic_admin')
        archive = self.store.root / 'trash' / result['trash_id']
        self.assertEqual((archive / item['id'] / 'points.csv').read_bytes(), original)
        audit = json.loads((archive / 'deletion.json').read_text())
        self.assertEqual(audit['deleted_by'], 'synthetic_admin')
        self.assertEqual(audit['version'], item['version'])
        self.assertEqual(self.store.list()['total'], 0); self.assertIsNone(self.store.latest())
        for function, args in [(self.store.get, (item['id'],)), (self.store.points, (item['id'],)), (self.store.download, (item['id'], 'csv'))]:
            with self.assertRaises(FileNotFoundError): function(*args)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM points WHERE dataset_id=?', (item['id'],)).fetchone()[0], 100)
        again = self.store.delete(item['id'], item['version'], 'synthetic_admin')
        self.assertTrue(again['already_deleted']); self.assertEqual(again['trash_id'], result['trash_id'])
        self.assertEqual(os.stat(archive).st_mode & 0o777, 0o700)

    def test_active_or_finalizing_writer_cannot_be_deleted(self):
        writer = self.writer(); item = self.store.get(writer.metadata['id'])
        with self.assertRaises(MeasurementBusyError): self.store.delete(item['id'], item['version'], 'admin')
        writer.publish(force=True, state='completed')
        item = self.store.get(item['id'])
        with self.assertRaises(MeasurementBusyError): self.store.delete(item['id'], item['version'], 'admin')
        self.assertTrue(writer.folder.exists())

    def test_stale_version_or_unknown_path_never_deletes_data(self):
        writer = self.writer(); writer.finish('failed')
        with self.assertRaises(MeasurementConflictError): self.store.delete(writer.metadata['id'], '0' * 64, 'admin')
        with self.assertRaises(ValueError): self.store.delete('../index.sqlite3', '0' * 64, 'admin')
        self.assertTrue(writer.folder.exists()); self.assertEqual(self.store.list()['total'], 1)

    def test_failed_archive_move_rolls_back_record_and_preserves_files(self):
        writer = self.writer(); writer.append(synthetic_rows()); writer.finish('completed')
        item = self.store.get(writer.metadata['id']); original = os.replace
        def fail_folder(source, target):
            if Path(source) == writer.folder: raise OSError('synthetic archive failure')
            return original(source, target)
        with patch('pump_console.measurement_store.os.replace', side_effect=fail_folder), self.assertRaises(OSError):
            self.store.delete(item['id'], item['version'], 'admin')
        self.assertEqual(self.store.get(item['id'])['version'], item['version'])
        self.assertTrue((writer.folder / 'points.csv').exists())
        self.assertEqual(list((self.store.root / 'trash').iterdir()), [])

    def test_upgrade_keeps_legacy_dataset_table_shape_and_data(self):
        writer = self.writer(); writer.append(synthetic_rows()); writer.finish('completed')
        with self.store.db() as db: db.execute('DROP TABLE measurement_deletions')
        upgraded = MeasurementStore(self.tmp.name)
        with upgraded.db() as db:
            self.assertEqual(len(list(db.execute('PRAGMA table_info(datasets)'))), 7)
        self.assertEqual(upgraded.get(writer.metadata['id'])['point_count'], 100)


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
        self.assertTrue(detail['chart']['trend_segments'])
        self.assertEqual(detail['chart']['trend_method'],TREND_METHOD)
        code,chart=self.request('GET','/api/measurements/'+self.dataset+'/points')
        self.assertEqual(code,200);self.assertEqual(chart['trend_segments'],detail['chart']['trend_segments'])
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

    def test_admin_delete_requires_version_and_removes_every_read_route(self):
        item = self.repo.measurements.get(self.dataset)
        code, result = self.request('DELETE', '/api/measurements/' + self.dataset, {'expected_version': item['version']})
        self.assertEqual(code, 200); self.assertTrue(result['deleted'])
        self.assertEqual(self.request('GET', '/api/measurements')[1]['total'], 0)
        for suffix in ('', '/points', '/download?format=csv', '/download?format=raw'):
            self.assertEqual(self.request('GET', '/api/measurements/' + self.dataset + suffix)[0], 404)
        with self.auth.db() as db:
            self.assertTrue(db.execute('SELECT 1 FROM audit WHERE event=? AND username=?',
                                      ('measurement_deleted/' + self.dataset, 'test_admin')).fetchone())

    def test_delete_rejects_anonymous_viewer_missing_csrf_and_arbitrary_fields(self):
        item = self.repo.measurements.get(self.dataset); body = {'expected_version': item['version']}
        self.assertEqual(self.request('DELETE', '/api/measurements/' + self.dataset, body, headers={'Cookie': ''})[0], 401)
        self.assertEqual(self.request('DELETE', '/api/measurements/' + self.dataset, body, headers={'X-CSRF-Token': ''})[0], 403)
        self.assertEqual(self.request('DELETE', '/api/measurements/' + self.dataset, {**body, 'force': True})[0], 400)
        self.auth.create_user('delete_viewer', 'Synthetic viewer password 2026', role='viewer', temporary=False)
        self.token, session = self.auth.login('delete_viewer', 'Synthetic viewer password 2026', '127.0.0.1'); self.csrf = session['csrf_token']
        self.assertEqual(self.request('DELETE', '/api/measurements/' + self.dataset, body)[0], 403)
        self.assertTrue(self.repo.measurements.get(self.dataset))

    def test_stale_delete_version_and_live_acquisition_return_conflict(self):
        self.assertEqual(self.request('DELETE', '/api/measurements/' + self.dataset, {'expected_version': '0' * 64})[0], 409)
        writer = self.repo.measurements.create('live-run', 'live', synthetic_step(), 'test_admin', {})
        try:
            item = self.repo.measurements.get(writer.metadata['id'])
            code, result = self.request('DELETE', '/api/measurements/' + item['id'], {'expected_version': item['version']})
            self.assertEqual(code, 409); self.assertEqual(result['error'], 'measurement_in_use')
        finally: writer.finish('failed')


if __name__ == '__main__': unittest.main()
