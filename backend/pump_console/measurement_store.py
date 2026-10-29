"""Private CV datasets, durable block means, and bounded chart snapshots."""
from contextlib import contextmanager
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import threading

DATASET_ID = re.compile(r'cv-[a-f0-9]{32}')
POINT_COLUMNS = ['index', 'time_s', 'potential_v', 'current_a']
CSV_COLUMNS = ['index', 'received_elapsed_s', 'potential_v_nominal', 'current_a_nominal',
               'adc_mean_code', 'dac_code', 'sensitivity', 'current_gain', 'dac_vref_mv', 'sample_count']
CHART_SAMPLER = 'uniform_index_extrema_v1'
CHART_POINT_LIMIT = 3000
TREND_METHOD = 'full_data_branch_bucket_mean_v1'
ACTIVE_DATASET_STATES = ('starting', 'running', 'finalizing')
TERMINAL_DATASET_STATES = ('completed', 'stopped', 'failed', 'imported', 'interrupted')


class MeasurementBusyError(ValueError):
    pass


class MeasurementConflictError(ValueError):
    pass


def metadata_version(metadata):
    value = {key: entry for key, entry in metadata.items() if key != 'version'}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def with_version(metadata):
    return {**metadata, 'version': metadata_version(metadata)}


def now():
    return datetime.now(timezone.utc).isoformat()


def private_json(path, value, durable=False):
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
    temporary = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            if durable:
                handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        if durable:
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def thin_points(points, limit=CHART_POINT_LIMIT):
    """Preserve order, endpoints, and E/I extrema; never invent chart points."""
    if len(points) <= limit:
        return points
    buckets = max(1, limit // 6)
    width = math.ceil(len(points) / buckets)
    result = []
    for start in range(0, len(points), width):
        group = points[start:start + width]
        positions = {0, len(group) - 1}
        for field in (2, 3):
            positions.add(min(range(len(group)), key=lambda i: group[i][field]))
            positions.add(max(range(len(group)), key=lambda i: group[i][field]))
        result.extend(group[i] for i in sorted(positions))
    return result


class StreamingTrace:
    """Equal-width index buckets retain extrema without reweighting old samples."""
    def __init__(self, limit=CHART_POINT_LIMIT):
        self.bucket_limit=max(1,limit//6)
        self.width,self.count,self.buckets=1,0,{}

    @staticmethod
    def merge(left,right):
        return [min(left[0],right[0],key=lambda p:p[0]),max(left[1],right[1],key=lambda p:p[0]),
                min(left[2],right[2],key=lambda p:p[2]),max(left[3],right[3],key=lambda p:p[2]),
                min(left[4],right[4],key=lambda p:p[3]),max(left[5],right[5],key=lambda p:p[3])]

    def append(self,points):
        for point in points:
            while self.count//self.width>=self.bucket_limit:
                merged={}
                for index,bucket in self.buckets.items():
                    target=index//2
                    merged[target]=self.merge(merged[target],bucket) if target in merged else bucket
                self.width*=2;self.buckets=merged
            index=self.count//self.width
            item=[point]*6
            self.buckets[index]=self.merge(self.buckets[index],item) if index in self.buckets else item
            self.count+=1

    def points(self):
        result=[]
        for index in sorted(self.buckets):
            unique={point[0]:point for point in self.buckets[index]}
            result.extend(list(unique[key]) for key in sorted(unique))
        return result


class StreamingTrend:
    """Display-only means of all samples, split before averaging at CV reversals.

    Equal acquisition-index buckets carry sums and counts through every merge.
    Extrema-selected scatter points are never used to estimate these means.
    Pathologically many reversals disable the trend rather than growing memory.
    """
    def __init__(self, bucket_limit=CHART_POINT_LIMIT):
        self.bucket_limit = bucket_limit
        self.width, self.count, self.branch, self.direction = 1, 0, 0, 0
        self.previous = None
        self.buckets, self.ends = {}, {}
        self.disabled = False

    def append(self, points):
        if self.disabled:
            return
        for point in points:
            if self.previous is not None:
                delta = point[2] - self.previous[2]
                direction = (delta > 0) - (delta < 0)
                if direction and self.direction and direction != self.direction:
                    self.branch += 1
                    if self.branch >= 1024:
                        self.disabled = True
                        self.buckets.clear(); self.ends.clear()
                        return
                    self.ends[self.branch] = [self.previous, point]
                if direction:
                    self.direction = direction
            if self.branch not in self.ends:
                self.ends[self.branch] = [point, point]
            self.ends[self.branch][1] = point
            while self.count // self.width >= self.bucket_limit:
                merged = {}
                for (index, branch), summary in self.buckets.items():
                    key = (index // 2, branch)
                    if key in merged:
                        old = merged[key]
                        last = summary if summary[5] > old[5] else old
                        merged[key] = [old[0] + summary[0], last[1], old[2] + summary[2],
                                       old[3] + summary[3], old[4] + summary[4], last[5]]
                    else:
                        merged[key] = summary
                self.width *= 2
                self.buckets = merged
            key = (self.count // self.width, self.branch)
            if key not in self.buckets:
                self.buckets[key] = [*point, 1, point[0]]
            else:
                summary = self.buckets[key]
                for field in (0, 2, 3):
                    summary[field] += point[field]
                summary[1], summary[5] = point[1], point[0]
                summary[4] += 1
            self.count += 1
            self.previous = point

    def chart(self):
        if self.disabled:
            return dict(trend_segments=[], trend_method=TREND_METHOD,
                        trend_unavailable_reason='too_many_potential_reversals')
        groups = {branch: [] for branch in self.ends}
        for (_, branch), summary in sorted(self.buckets.items()):
            groups[branch].append([summary[0] / summary[4], summary[1],
                                   summary[2] / summary[4], summary[3] / summary[4]])
        segments = []
        for branch, points in groups.items():
            first, last = self.ends[branch]
            # Actual turnaround points anchor both adjacent branches. Interior
            # trend values are explicitly derived means, not acquired samples.
            segments.append([list(first), *[point for point in points if first[0] < point[0] < last[0]],
                             *([list(last)] if first[0] != last[0] else [])])
        return dict(trend_segments=segments, trend_method=TREND_METHOD)


class MeasurementStore:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve() / 'measurements'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.database = self.root / 'index.sqlite3'
        self._chart_cache=OrderedDict();self._chart_lock=threading.Lock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence_id TEXT NOT NULL,
                    step_id TEXT NOT NULL, created_at TEXT NOT NULL, state TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS datasets_created ON datasets(created_at DESC);
                CREATE INDEX IF NOT EXISTS datasets_run ON datasets(run_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS points (
                    dataset_id TEXT NOT NULL REFERENCES datasets(id), point_index INTEGER NOT NULL,
                    time_s REAL NOT NULL, potential_v REAL NOT NULL, current_a REAL NOT NULL,
                    PRIMARY KEY(dataset_id, point_index)
                );
                CREATE TABLE IF NOT EXISTS point_attributes (
                    dataset_id TEXT NOT NULL, point_index INTEGER NOT NULL,
                    adc_mean REAL, dac_code INTEGER, sensitivity INTEGER,
                    current_gain INTEGER, dac_vref_mv REAL, sample_count INTEGER NOT NULL,
                    PRIMARY KEY(dataset_id, point_index),
                    FOREIGN KEY(dataset_id, point_index) REFERENCES points(dataset_id, point_index)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS measurement_deletions (
                    dataset_id TEXT PRIMARY KEY REFERENCES datasets(id), deleted_at TEXT NOT NULL,
                    deletion_id TEXT NOT NULL, deleted_by TEXT NOT NULL, version TEXT NOT NULL
                );
            ''')
        self.database.chmod(0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def folder(self, dataset_id):
        if not isinstance(dataset_id, str) or not DATASET_ID.fullmatch(dataset_id):
            raise ValueError('Invalid measurement ID')
        path = self.root / dataset_id
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError('Invalid measurement path')
        return path

    def create(self, run_id, sequence_id, step, actor, encoded_parameters, provenance=None):
        dataset_id = 'cv-' + secrets.token_hex(16)
        folder = self.folder(dataset_id)
        folder.mkdir(mode=0o700)
        metadata = dict(id=dataset_id, run_id=run_id, sequence_id=sequence_id, step_id=step['id'],
                        description=step.get('description', 'Cyclic voltammetry'),
                        technique='cyclic_voltammetry', created_at=now(), updated_at=now(),
                        finished_at=None, state='starting', actor=actor,
                        parameters=step['parameters'], encoded_parameters=encoded_parameters,
                        point_count=0, sample_count=0, raw_frame_count=0,
                        nominal_conversion=True, time_basis='server_receive_time',
                        stop_confirmed=False, message=None, statistics={}, provenance=provenance)
        metadata.update(storage_format_version=2, storage_state='recording', csv_ready=False)
        with self.db() as db:
            db.execute('INSERT INTO datasets(id,run_id,sequence_id,step_id,created_at,state,metadata) VALUES (?,?,?,?,?,?,?)',
                       (dataset_id, run_id, sequence_id, step['id'], metadata['created_at'],
                        metadata['state'], json.dumps(metadata, ensure_ascii=False, allow_nan=False)))
        private_json(folder / 'metadata.json', metadata)
        return DatasetWriter(self, metadata)

    def recover_incomplete(self, grace_seconds=10):
        from .measurement_recovery import recover_incomplete
        return recover_incomplete(self, grace_seconds)

    def get(self, dataset_id):
        self.folder(dataset_id)
        with self.db() as db:
            row = db.execute('SELECT metadata FROM datasets WHERE id=? AND NOT EXISTS (SELECT 1 FROM measurement_deletions x WHERE x.dataset_id=datasets.id)', (dataset_id,)).fetchone()
        if row is None:
            raise FileNotFoundError('Measurement does not exist')
        return with_version(json.loads(row['metadata']))

    def list(self, query='', state='', limit=50, offset=0, run_id=''):
        if not isinstance(query, str) or len(query) > 160 or state not in ('', *ACTIVE_DATASET_STATES, *TERMINAL_DATASET_STATES):
            raise ValueError('Invalid measurement filter')
        if not isinstance(run_id,str) or (run_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',run_id)):
            raise ValueError('Invalid run ID')
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('Invalid measurement page')
        where, values = ['NOT EXISTS (SELECT 1 FROM measurement_deletions x WHERE x.dataset_id=datasets.id)'], []
        if query:
            where.append('(id LIKE ? ESCAPE "!" OR sequence_id LIKE ? ESCAPE "!" OR step_id LIKE ? ESCAPE "!")')
            pattern = '%' + query.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
            values.extend([pattern] * 3)
        if state:
            where.append('state=?'); values.append(state)
        if run_id:
            where.append('run_id=?'); values.append(run_id)
        condition = ' WHERE ' + ' AND '.join(where) if where else ''
        with self.db() as db:
            count = db.execute('SELECT count(*) FROM datasets' + condition, values).fetchone()[0]
            rows = db.execute('SELECT metadata FROM datasets' + condition + ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?', values + [limit, offset]).fetchall()
        return dict(measurements=[with_version(json.loads(row['metadata'])) for row in rows], total=count,
                    next_offset=offset + limit if offset + limit < count else None)

    def points(self, dataset_id, limit=CHART_POINT_LIMIT):
        metadata = self.get(dataset_id)
        if type(limit) is not int or not 24 <= limit <= 10000:
            raise ValueError('Chart point limit must be between 24 and 10000')
        live = self.folder(dataset_id) / 'live.json'
        if metadata['state'] in ACTIVE_DATASET_STATES and live.is_file() and not live.is_symlink():
            snapshot = json.loads(live.read_text())
            return dict(points=thin_points(snapshot['points'], limit), point_columns=POINT_COLUMNS,
                        total_points=snapshot['point_count'], updated_at=snapshot['updated_at'],
                        decimated=snapshot['point_count'] > len(snapshot['points']),
                        sampling=snapshot.get('sampling','legacy_snapshot'),
                        **{key: snapshot[key] for key in ('trend_segments', 'trend_method', 'trend_unavailable_reason') if key in snapshot})
        cache_key=(dataset_id,metadata['updated_at'],metadata['point_count'],limit)
        with self._chart_lock:
            cached=self._chart_cache.get(cache_key)
            if cached is not None:
                self._chart_cache.move_to_end(cache_key);return deepcopy(cached)
        # Bound memory while retaining early/late points and extrema for every time bucket.
        count = metadata['point_count']
        width = max(1, math.ceil(count / max(1, limit // 6)))
        result, group = [], []
        trend = StreamingTrend(bucket_limit=min(limit, CHART_POINT_LIMIT))
        with self.db() as db:
            cursor = db.execute('SELECT point_index,time_s,potential_v,current_a FROM points WHERE dataset_id=? ORDER BY point_index', (dataset_id,))
            for row in cursor:
                point = list(row)
                trend.append([point])
                group.append(point)
                if len(group) >= width:
                    result.extend(thin_points(group, 6)); group = []
            if group:
                result.extend(thin_points(group, 6))
        displayed=thin_points(result, limit)
        chart=dict(points=displayed, point_columns=POINT_COLUMNS,total_points=count,
                   updated_at=metadata['updated_at'],decimated=count>len(displayed),sampling=CHART_SAMPLER,
                   **trend.chart())
        with self._chart_lock:
            self._chart_cache[cache_key]=deepcopy(chart)
            while len(self._chart_cache)>8:self._chart_cache.popitem(last=False)
        return chart

    def latest(self, include_points=True):
        with self.db() as db:
            row = db.execute('SELECT metadata FROM datasets WHERE NOT EXISTS (SELECT 1 FROM measurement_deletions x WHERE x.dataset_id=datasets.id) ORDER BY created_at DESC, id DESC LIMIT 1').fetchone()
        if row is None:
            return None
        metadata = with_version(json.loads(row['metadata']))
        if not include_points:
            return dict(metadata=metadata, chart_revision=metadata['version'], total_points=metadata['point_count'])
        if metadata['state'] not in ACTIVE_DATASET_STATES:
            try:chart=self.points(metadata['id'])
            except FileNotFoundError:return None
            return dict(metadata=metadata,points=chart['points'],point_columns=POINT_COLUMNS,
                        total_points=chart['total_points'],sampling=chart['sampling'],
                        **{key: chart[key] for key in ('trend_segments', 'trend_method', 'trend_unavailable_reason') if key in chart})
        live = self.folder(metadata['id']) / 'live.json'
        points = None
        if live.is_file() and not live.is_symlink():
            points = json.loads(live.read_text())
        return dict(metadata=metadata, points=points['points'] if points else [],
                    point_columns=POINT_COLUMNS, total_points=points['point_count'] if points else metadata['point_count'],
                    sampling=points.get('sampling','legacy_snapshot') if points else CHART_SAMPLER,
                    **{key: points[key] for key in ('trend_segments', 'trend_method', 'trend_unavailable_reason') if points and key in points})

    def download(self, dataset_id, kind):
        metadata = self.get(dataset_id)
        names = {'csv': 'points.csv', 'json': 'metadata.json', 'raw': 'frames.jsonl'}
        if kind not in names:
            raise ValueError('Unsupported data download format')
        if kind != 'json' and (metadata['state'] in ACTIVE_DATASET_STATES or (kind=='csv' and metadata.get('csv_ready') is False)):
            raise MeasurementBusyError('数据仍在保存或需要恢复，暂不能下载完整文件。')
        path = self.folder(dataset_id) / names[kind]
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError('Measurement file is unavailable')
        return path, dataset_id + ('.csv' if kind == 'csv' else '.json' if kind == 'json' else '-raw.jsonl')

    def delete(self, dataset_id, expected_version, actor):
        """Hide one terminal dataset and atomically archive its files, retaining all points."""
        folder = self.folder(dataset_id)
        if not isinstance(expected_version, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_version):
            raise ValueError('Expected the current measurement version')
        if not isinstance(actor, str) or not actor:
            raise ValueError('Deletion requires a server-authenticated actor')
        lock = None
        archive = None
        moved = False
        try:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT d.metadata,x.deleted_at,x.deletion_id FROM datasets d LEFT JOIN measurement_deletions x ON x.dataset_id=d.id WHERE d.id=?', (dataset_id,)).fetchone()
                if row is None:
                    raise FileNotFoundError('Measurement does not exist')
                metadata = json.loads(row['metadata'])
                if metadata_version(metadata) != expected_version:
                    raise MeasurementConflictError('测量记录已更新，请刷新后重新确认删除。')
                if row['deleted_at'] is not None:
                    return dict(deleted=True, already_deleted=True, id=dataset_id, trash_id=row['deletion_id'])
                if metadata['state'] not in TERMINAL_DATASET_STATES:
                    raise MeasurementBusyError('正在采集或状态待确认的数据不能删除，请等待采集结束。')
                if not folder.is_dir():
                    raise FileNotFoundError('Measurement files are unavailable')
                lock_path = folder / 'writer.lock'
                if lock_path.is_symlink():
                    raise ValueError('Invalid measurement lock path')
                lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise MeasurementBusyError('采集文件仍在写入，请稍后刷新再删除。') from exc
                trash = self.root / 'trash'
                if trash.is_symlink():
                    raise ValueError('Invalid measurement archive path')
                trash.mkdir(exist_ok=True, mode=0o700); trash.chmod(0o700)
                deletion_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(8)
                archive = trash / deletion_id
                archive.mkdir(mode=0o700)
                deleted_at = now()
                private_json(archive / 'deletion.json', dict(id=dataset_id, deleted_at=deleted_at,
                    deleted_by=actor, version=expected_version, metadata=metadata,
                    retained_sqlite_points=True))
                os.replace(folder, archive / dataset_id)
                moved = True
                db.execute('INSERT INTO measurement_deletions VALUES (?,?,?,?,?)',
                           (dataset_id, deleted_at, deletion_id, actor, expected_version))
            return dict(deleted=True, already_deleted=False, id=dataset_id, trash_id=deletion_id)
        except BaseException:
            if moved and archive is not None:
                os.replace(archive / dataset_id, folder)
                moved = False
            if archive is not None and not moved:
                (archive / 'deletion.json').unlink(missing_ok=True)
                archive.rmdir()
            raise
        finally:
            if lock is not None:
                os.close(lock)


class DatasetWriter:
    def __init__(self, store, metadata):
        self.store, self.metadata = store, metadata
        self.folder = store.folder(metadata['id'])
        self.trace = []
        self.last_publish = 0
        self.raw_bytes = 0
        self.closed = False
        self.writer_lock = os.open(self.folder / 'writer.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        fcntl.flock(self.writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.raw = self.csv = None
        try:
            self.raw = self._open('frames.jsonl')
            self.csv = self._open('points.csv')
            self.csv_writer = csv.writer(self.csv)
            self.csv_writer.writerow(CSV_COLUMNS)
        except BaseException:
            if self.raw is not None: self.raw.close()
            if self.csv is not None: self.csv.close()
            os.close(self.writer_lock); self.closed = True
            raise

    def _open(self, name):
        fd = os.open(self.folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return os.fdopen(fd, 'w', encoding='utf-8', newline='', buffering=1 if name=='frames.jsonl' else 65536)

    def frame(self, direction, version, command, seq, payload, max_bytes=268435456):
        value = json.dumps(dict(time=now(), direction=direction, version=version,
                                command=command, seq=seq, payload_hex=payload.hex()), separators=(',', ':')) + '\n'
        if self.raw_bytes + len(value) > max_bytes:
            raise RuntimeError('Measurement raw-data size limit reached')
        self.raw.write(value)
        self.raw_bytes += len(value)
        self.metadata['raw_frame_count'] += 1

    def append(self, rows):
        if not rows:
            return
        # Do not advance the writer's counters until the same transaction has
        # committed both samples, conversion attributes and authoritative metadata.
        metadata = deepcopy(self.metadata)
        points, attributes = [], []
        for row in rows:
            index = metadata['point_count'] + 1
            t, potential, current = row['time_s'], row['potential_v'], row['current_a']
            if not all(type(x) in (float, int) and math.isfinite(x) for x in (t, potential, current)):
                raise ValueError('Non-finite measurement point')
            points.append([index, t, potential, current])
            metadata['point_count'] = index
            metadata['sample_count'] += row.get('sample_count', 100)
            attributes.append([metadata['id'],index,row.get('adc_mean'),row.get('dac_code'),
                row.get('sensitivity'),row.get('current_gain'),row.get('dac_vref_mv'),row.get('sample_count',100)])
            stats = metadata['statistics']
            for field, value in [('potential_v', potential), ('current_a', current), ('time_s', t)]:
                stats[field + '_min'] = min(stats.get(field + '_min', value), value)
                stats[field + '_max'] = max(stats.get(field + '_max', value), value)
        metadata['updated_at'] = now()
        with self.store.db() as db:
            db.executemany('INSERT INTO points VALUES (?,?,?,?,?)',
                           [(metadata['id'], *point) for point in points])
            db.executemany('INSERT INTO point_attributes VALUES (?,?,?,?,?,?,?,?)',attributes)
            db.execute('UPDATE datasets SET metadata=? WHERE id=?',
                       (json.dumps(metadata,ensure_ascii=False,allow_nan=False),metadata['id']))
        self.metadata = metadata
        if not hasattr(self,'sampler'):self.sampler=StreamingTrace()
        if not hasattr(self,'trend'):self.trend=StreamingTrend()
        self.sampler.append(points)
        self.trend.append(points)
        self.publish()

    def publish(self, force=False, **fields):
        if self.closed:
            raise ValueError('Measurement writer is closed')
        if not force and time.monotonic() - self.last_publish < .4:
            return
        metadata = deepcopy(self.metadata);metadata.update(fields)
        self.trace=self.sampler.points() if hasattr(self,'sampler') else []
        metadata['updated_at'] = now()
        raw = json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        with self.store.db() as db:
            db.execute('UPDATE datasets SET state=?,metadata=? WHERE id=?',
                       (metadata['state'], raw, metadata['id']))
        self.metadata=metadata
        self.last_publish = time.monotonic()
        private_json(self.folder / 'metadata.json', self.metadata)
        private_json(self.folder / 'live.json', dict(points=self.trace,
            point_count=self.metadata['point_count'], updated_at=self.metadata['updated_at'],sampling=CHART_SAMPLER,
            **(self.trend.chart() if hasattr(self,'trend') else dict(trend_segments=[],trend_method=TREND_METHOD))))

    def finish(self, state, stop_confirmed=False, message=None, **fields):
        from .measurement_recovery import build_csv
        try:
            self.publish(force=True, state='finalizing',storage_state='finalizing',csv_ready=False,
                         stop_confirmed=stop_confirmed,message=message,target_state=state,**fields)
            for handle in (self.raw,self.csv):
                handle.flush();os.fsync(handle.fileno());handle.close()
            sealed=build_csv(self.store,self.metadata)
            self.publish(force=True,state=state,message=message,finished_at=now(),**sealed)
            private_json(self.folder/'metadata.json',self.metadata,durable=True)
        finally:
            try:
                self.raw.close()
            finally:
                try:
                    self.csv.close()
                finally:
                    os.close(self.writer_lock); self.closed = True
