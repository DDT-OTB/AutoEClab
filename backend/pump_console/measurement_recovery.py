"""Seal exports and recover committed samples without opening an instrument."""
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3


def build_csv(store, metadata):
    from .measurement_store import CSV_COLUMNS
    folder = store.folder(metadata['id'])
    destination = folder / 'points.csv'
    temporary = folder / ('points.' + secrets.token_hex(6) + '.tmp')
    legacy = None
    if metadata.get('storage_format_version', 1) < 2 and destination.is_file():
        backup = folder / 'points.before-recovery.csv'
        if not backup.exists():
            # Preserve the original legacy export as evidence before rebuilding.
            with destination.open('rb') as source:
                descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, 'wb') as output:
                    while chunk := source.read(1024 * 1024):output.write(chunk)
                    output.flush();os.fsync(output.fileno())
        legacy = destination.open(newline='', encoding='utf-8')
    legacy_rows = iter(csv.DictReader(legacy)) if legacy else iter(())
    previous = next(legacy_rows, None)
    count = 0
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as output, store.db() as db:
            writer = csv.writer(output);writer.writerow(CSV_COLUMNS)
            cursor = db.execute('''SELECT p.point_index,p.time_s,p.potential_v,p.current_a,
                a.adc_mean,a.dac_code,a.sensitivity,a.current_gain,a.dac_vref_mv,a.sample_count
                FROM points p LEFT JOIN point_attributes a
                ON a.dataset_id=p.dataset_id AND a.point_index=p.point_index
                WHERE p.dataset_id=? ORDER BY p.point_index''', (metadata['id'],))
            for row in cursor:
                values = list(row)
                if metadata.get('storage_format_version', 1) >= 2 and values[-1] is None:
                    raise ValueError('Committed sample attributes are incomplete')
                if legacy:
                    while previous is not None:
                        try:index = int(previous.get('index', -1))
                        except (ValueError, TypeError):index = -1
                        if index >= values[0]:break
                        previous = next(legacy_rows, None)
                    if previous is not None and index == values[0]:
                        values[4:] = [previous.get(name) for name in CSV_COLUMNS[4:]]
                    elif values[-1] is None:
                        values[-1] = 100
                writer.writerow(values);count += 1
            if count != metadata['point_count']:
                raise ValueError('Committed sample count changed while sealing export')
            output.flush();os.fsync(output.fileno())
        os.replace(temporary, destination)
        descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
        checksum = hashlib.sha256()
        with destination.open('rb') as handle:
            while chunk := handle.read(1024 * 1024):checksum.update(chunk)
        return dict(csv_ready=True, csv_sha256=checksum.hexdigest(), storage_state='saved',
                    committed_point_count=count)
    finally:
        if legacy:legacy.close()
        temporary.unlink(missing_ok=True)


def recover_incomplete(store, grace_seconds=10):
    from .measurement_store import ACTIVE_DATASET_STATES, private_json, now
    with store.db() as db:
        rows = db.execute('''SELECT metadata FROM datasets d WHERE state IN ('starting','running','finalizing')
            AND NOT EXISTS (SELECT 1 FROM measurement_deletions x WHERE x.dataset_id=d.id)''').fetchall()
    recovered, skipped, errors = [], [], []
    for row in rows:
        metadata = json.loads(row[0]);dataset_id = metadata['id']
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(metadata['created_at'])).total_seconds()
        if age < grace_seconds:
            skipped.append(dataset_id);continue
        folder = store.folder(dataset_id);lock = folder / 'writer.lock'
        descriptor = None
        try:
            if lock.is_symlink() or not folder.is_dir():raise ValueError('Invalid recovery directory')
            descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            try:fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                skipped.append(dataset_id);continue
            current = store.get(dataset_id)
            if current['state'] not in ACTIVE_DATASET_STATES:continue
            metadata = {key:value for key,value in current.items() if key!='version'}
            with store.db() as db:
                record = db.execute('''SELECT count(*),min(p.time_s),max(p.time_s),min(p.potential_v),max(p.potential_v),
                    min(p.current_a),max(p.current_a),coalesce(sum(a.sample_count),0),count(a.point_index)
                    FROM points p LEFT JOIN point_attributes a ON a.dataset_id=p.dataset_id AND a.point_index=p.point_index
                    WHERE p.dataset_id=?''', (dataset_id,)).fetchone()
            count,tmin,tmax,vmin,vmax,imin,imax,samples,attributes = record
            if metadata.get('storage_format_version',1)>=2 and attributes!=count:
                raise ValueError('Committed attributes are incomplete; manual inspection required')
            metadata.update(point_count=count,sample_count=samples if attributes==count else count*100,
                            storage_state='finalizing',csv_ready=False)
            metadata['statistics'] = (dict(time_s_min=tmin,time_s_max=tmax,potential_v_min=vmin,
                potential_v_max=vmax,current_a_min=imin,current_a_max=imax) if count else {})
            metadata.update(build_csv(store, metadata))
            metadata.update(state='interrupted', finished_at=now(), updated_at=now(), recovered_at=now(),
                            message='采集进程已中断，已恢复数据库中提交的数据；请现场核对仪器状态。')
            with store.db() as db:
                db.execute('UPDATE datasets SET state=?,metadata=? WHERE id=?',
                           (metadata['state'],json.dumps(metadata,ensure_ascii=False,allow_nan=False),dataset_id))
            private_json(folder/'metadata.json',metadata,durable=True)
            recovered.append(dataset_id)
        except (OSError, ValueError, sqlite3.Error) as error:
            errors.append(dict(id=dataset_id,error=type(error).__name__))
            try:
                current=store.get(dataset_id)
                if current['state'] in ACTIVE_DATASET_STATES:
                    current.pop('version',None)
                    current.update(storage_state='error',csv_ready=False,storage_error=type(error).__name__,updated_at=now())
                    with store.db() as db:
                        db.execute('UPDATE datasets SET metadata=? WHERE id=?',
                                   (json.dumps(current,ensure_ascii=False,allow_nan=False),dataset_id))
            except (OSError,ValueError,sqlite3.Error):pass
        finally:
            if descriptor is not None:os.close(descriptor)
    return dict(recovered=recovered, skipped=skipped, errors=errors)
