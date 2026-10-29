import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from pump_console.measurement_store import MeasurementStore,MeasurementBusyError
from pump_console.measurement_recovery import build_csv


def rows(count=10):
    return [dict(time_s=i*.1,potential_v=i*.01,current_a=i*1e-7,
                 adc_mean=i+10,dac_code=i+20,sensitivity=5,current_gain=0,dac_vref_mv=1020,sample_count=100)
            for i in range(count)]


class StorageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=MeasurementStore(self.temp.name)

    def writer(self):
        writer=self.store.create('synthetic_run','synthetic_sequence',{'id':'cv','parameters':{}},'synthetic_actor',{})
        self.addCleanup(lambda:None if writer.closed else writer.finish('failed'))
        return writer

    def test_transient_database_failure_does_not_advance_count_or_export(self):
        writer=self.writer()
        with patch.object(self.store,'db',side_effect=sqlite3.OperationalError('Synthetic failure')):
            with self.assertRaises(sqlite3.OperationalError):writer.append(rows())
        self.assertEqual(writer.metadata['point_count'],0)
        writer.finish('failed')
        self.assertEqual(self.store.get(writer.metadata['id'])['point_count'],0)
        self.assertEqual(len(list(csv.DictReader((writer.folder/'points.csv').read_text().splitlines()))),0)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM points').fetchone()[0],0)

    def test_failure_after_points_insert_rolls_back_entire_batch_and_counters(self):
        writer=self.writer();writer.append(rows(3))
        with self.store.db() as db:
            db.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON point_attributes BEGIN SELECT RAISE(ABORT,'Synthetic attribute failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):writer.append(rows(7))
        self.assertEqual(writer.metadata['point_count'],3)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM points').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM point_attributes').fetchone()[0],3)
            metadata=json.loads(db.execute('SELECT metadata FROM datasets').fetchone()[0])
            self.assertEqual(metadata['point_count'],3)
            db.execute('DROP TRIGGER synthetic_failure')
        writer.finish('failed')
        self.assertEqual(len((writer.folder/'points.csv').read_text().splitlines()),4)

    def test_batch_validation_cannot_write_a_partial_batch(self):
        writer=self.writer();invalid=rows();invalid[5]['current_a']=float('nan')
        with self.assertRaises(ValueError):writer.append(invalid)
        self.assertEqual(writer.metadata['point_count'],0)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM points').fetchone()[0],0)

    def test_csv_is_built_from_committed_attributes_and_sealed_before_completion(self):
        writer=self.writer();writer.append(rows())
        self.assertEqual(self.store.get(writer.metadata['id'])['point_count'],10)
        def seal(store,metadata):
            actual=store.get(metadata['id'])
            self.assertEqual(actual['state'],'finalizing');self.assertFalse(actual['csv_ready'])
            with self.assertRaises(MeasurementBusyError):store.download(metadata['id'],'csv')
            return build_csv(store,metadata)
        with patch('pump_console.measurement_recovery.build_csv',side_effect=seal):writer.finish('completed',stop_confirmed=True)
        metadata=self.store.get(writer.metadata['id']);self.assertEqual(metadata['storage_state'],'saved')
        raw=(writer.folder/'points.csv').read_bytes()
        self.assertEqual(metadata['csv_sha256'],hashlib.sha256(raw).hexdigest())
        values=list(csv.DictReader(raw.decode().splitlines()))
        self.assertEqual(len(values),10);self.assertEqual(values[-1]['adc_mean_code'],'19.0')
        self.assertEqual(values[-1]['dac_code'],'29');self.assertEqual(values[-1]['sample_count'],'100')

    def test_failed_finalization_is_recoverable_and_never_looks_completed(self):
        writer=self.writer();writer.append(rows())
        with patch('pump_console.measurement_recovery.build_csv',side_effect=OSError('Synthetic disk failure')):
            with self.assertRaises(OSError):writer.finish('completed',stop_confirmed=True)
        self.assertTrue(writer.closed)
        self.assertEqual(self.store.get(writer.metadata['id'])['state'],'finalizing')
        with self.assertRaises(MeasurementBusyError):self.store.download(writer.metadata['id'],'csv')
        result=self.store.recover_incomplete(grace_seconds=0)
        self.assertEqual(result['recovered'],[writer.metadata['id']])
        metadata=self.store.get(writer.metadata['id'])
        self.assertEqual(metadata['state'],'interrupted');self.assertTrue(metadata['csv_ready'])
        self.assertEqual(metadata['point_count'],10);self.assertTrue(metadata['stop_confirmed'])

    def test_recovery_skips_live_writer_and_recent_creation(self):
        writer=self.writer();writer.append(rows())
        self.assertEqual(self.store.recover_incomplete(grace_seconds=0)['skipped'],[writer.metadata['id']])
        self.assertEqual(self.store.recover_incomplete(grace_seconds=3600)['skipped'],[writer.metadata['id']])
        self.assertNotEqual(self.store.get(writer.metadata['id'])['state'],'interrupted')

    def test_hard_process_exit_recovers_exact_committed_rows_without_hardware(self):
        backend=Path(__file__).resolve().parents[1]/'backend'
        code="""import os,sys;sys.path.insert(0,sys.argv[1]);from pump_console.measurement_store import MeasurementStore
s=MeasurementStore(sys.argv[2]);w=s.create('synthetic_run','synthetic_sequence',{'id':'cv','parameters':{}},'synthetic_actor',{})
w.append([dict(time_s=i*.1,potential_v=i*.01,current_a=i*1e-7,sample_count=100) for i in range(23)])
os._exit(91)
"""
        process=subprocess.run([sys.executable,'-c',code,str(backend),self.temp.name],capture_output=True,text=True,timeout=10)
        self.assertEqual(process.returncode,91,process.stderr)
        metadata=self.store.list()['measurements'][0]
        result=self.store.recover_incomplete(grace_seconds=0)
        self.assertEqual(result['recovered'],[metadata['id']])
        recovered=self.store.get(metadata['id']);self.assertEqual(recovered['point_count'],23)
        self.assertEqual(recovered['sample_count'],2300);self.assertFalse(recovered['stop_confirmed'])
        self.assertEqual(recovered['state'],'interrupted')
        self.assertEqual(len(self.store.download(metadata['id'],'csv')[0].read_text().splitlines()),24)
        self.assertEqual(self.store.recover_incomplete(grace_seconds=0)['recovered'],[])

    def test_legacy_recovery_preserves_original_export_and_removes_uncommitted_tail(self):
        writer=self.writer();writer.append(rows());writer.finish('failed')
        metadata=dict(writer.metadata);metadata.pop('storage_format_version')
        metadata.update(state='running',point_count=11,csv_ready=False)
        path=writer.folder/'points.csv';original=path.read_bytes()+b'11,1,0,0,999,999,5,0,1020,100\r\n';path.write_bytes(original)
        with self.store.db() as db:
            db.execute('DELETE FROM point_attributes')
            db.execute('UPDATE datasets SET state=?,metadata=?',('running',json.dumps(metadata)))
        self.store.recover_incomplete(grace_seconds=0)
        recovered=self.store.get(metadata['id']);self.assertEqual(recovered['point_count'],10)
        self.assertEqual((writer.folder/'points.before-recovery.csv').read_bytes(),original)
        self.assertEqual(len(path.read_text().splitlines()),11)


if __name__=='__main__':unittest.main()
