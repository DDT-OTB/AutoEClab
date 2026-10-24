"""Private CV datasets, durable block means, and bounded chart snapshots."""
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

DATASET_ID = re.compile(r'cv-[a-f0-9]{32}')
POINT_COLUMNS = ['index', 'time_s', 'potential_v', 'current_a']
CSV_COLUMNS = ['index', 'received_elapsed_s', 'potential_v_nominal', 'current_a_nominal',
               'adc_mean_code', 'dac_code', 'sensitivity', 'current_gain', 'dac_vref_mv', 'sample_count']


def now():
    return datetime.now(timezone.utc).isoformat()


def private_json(path, value):
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
    temporary = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def thin_points(points, limit=1800):
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


class MeasurementStore:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve() / 'measurements'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.database = self.root / 'index.sqlite3'
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
        with self.db() as db:
            db.execute('INSERT INTO datasets VALUES (?,?,?,?,?,?,?)',
                       (dataset_id, run_id, sequence_id, step['id'], metadata['created_at'],
                        metadata['state'], json.dumps(metadata, ensure_ascii=False, allow_nan=False)))
        private_json(folder / 'metadata.json', metadata)
        return DatasetWriter(self, metadata)

    def get(self, dataset_id):
        self.folder(dataset_id)
        with self.db() as db:
            row = db.execute('SELECT metadata FROM datasets WHERE id=?', (dataset_id,)).fetchone()
        if row is None:
            raise FileNotFoundError('Measurement does not exist')
        return json.loads(row['metadata'])

    def list(self, query='', state='', limit=50, offset=0):
        if not isinstance(query, str) or len(query) > 160 or state not in ('', 'starting', 'running', 'completed', 'stopped', 'failed', 'imported'):
            raise ValueError('Invalid measurement filter')
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('Invalid measurement page')
        where, values = [], []
        if query:
            where.append('(id LIKE ? ESCAPE "!" OR sequence_id LIKE ? ESCAPE "!" OR step_id LIKE ? ESCAPE "!")')
            pattern = '%' + query.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
            values.extend([pattern] * 3)
        if state:
            where.append('state=?'); values.append(state)
        condition = ' WHERE ' + ' AND '.join(where) if where else ''
        with self.db() as db:
            count = db.execute('SELECT count(*) FROM datasets' + condition, values).fetchone()[0]
            rows = db.execute('SELECT metadata FROM datasets' + condition + ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?', values + [limit, offset]).fetchall()
        return dict(measurements=[json.loads(row['metadata']) for row in rows], total=count,
                    next_offset=offset + limit if offset + limit < count else None)

    def points(self, dataset_id, limit=1800):
        metadata = self.get(dataset_id)
        if type(limit) is not int or not 24 <= limit <= 10000:
            raise ValueError('Chart point limit must be between 24 and 10000')
        live = self.folder(dataset_id) / 'live.json'
        if metadata['state'] in ('starting', 'running') and live.is_file() and not live.is_symlink():
            snapshot = json.loads(live.read_text())
            return dict(points=thin_points(snapshot['points'], limit), point_columns=POINT_COLUMNS,
                        total_points=snapshot['point_count'], updated_at=snapshot['updated_at'],
                        decimated=snapshot['point_count'] > len(snapshot['points']))
        # Bound memory while retaining early/late points and extrema for every time bucket.
        count = metadata['point_count']
        width = max(1, math.ceil(count / max(1, limit // 6)))
        result, group = [], []
        with self.db() as db:
            cursor = db.execute('SELECT point_index,time_s,potential_v,current_a FROM points WHERE dataset_id=? ORDER BY point_index', (dataset_id,))
            for row in cursor:
                group.append(list(row))
                if len(group) >= width:
                    result.extend(thin_points(group, 6)); group = []
            if group:
                result.extend(thin_points(group, 6))
        return dict(points=thin_points(result, limit), point_columns=POINT_COLUMNS,
                    total_points=count, updated_at=metadata['updated_at'], decimated=count > len(result))

    def latest(self):
        with self.db() as db:
            row = db.execute('SELECT metadata FROM datasets ORDER BY created_at DESC, id DESC LIMIT 1').fetchone()
        if row is None:
            return None
        metadata = json.loads(row['metadata'])
        live = self.folder(metadata['id']) / 'live.json'
        points = None
        if live.is_file() and not live.is_symlink():
            points = json.loads(live.read_text())
        return dict(metadata=metadata, points=points['points'] if points else [],
                    point_columns=POINT_COLUMNS, total_points=points['point_count'] if points else metadata['point_count'])

    def download(self, dataset_id, kind):
        metadata = self.get(dataset_id)
        names = {'csv': 'points.csv', 'json': 'metadata.json', 'raw': 'frames.jsonl'}
        if kind not in names:
            raise ValueError('Unsupported data download format')
        if metadata['state'] in ('starting', 'running') and kind != 'json':
            raise ValueError('Please wait until acquisition stops before downloading data')
        path = self.folder(dataset_id) / names[kind]
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError('Measurement file is unavailable')
        return path, dataset_id + ('.csv' if kind == 'csv' else '.json' if kind == 'json' else '-raw.jsonl')


class DatasetWriter:
    def __init__(self, store, metadata):
        self.store, self.metadata = store, metadata
        self.folder = store.folder(metadata['id'])
        self.trace = []
        self.last_publish = 0
        self.raw_bytes = 0
        self.raw = self._open('frames.jsonl')
        self.csv = self._open('points.csv')
        self.csv_writer = csv.writer(self.csv)
        self.csv_writer.writerow(CSV_COLUMNS)
        self.closed = False

    def _open(self, name):
        fd = os.open(self.folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return os.fdopen(fd, 'w', encoding='utf-8', newline='', buffering=1)

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
        points = []
        for row in rows:
            index = self.metadata['point_count'] + 1
            t, potential, current = row['time_s'], row['potential_v'], row['current_a']
            if not all(type(x) in (float, int) and math.isfinite(x) for x in (t, potential, current)):
                raise ValueError('Non-finite measurement point')
            points.append([index, t, potential, current])
            self.metadata['point_count'] = index
            self.metadata['sample_count'] += row.get('sample_count', 100)
            self.csv_writer.writerow([index, t, potential, current, row.get('adc_mean'), row.get('dac_code'),
                row.get('sensitivity'), row.get('current_gain'), row.get('dac_vref_mv'), row.get('sample_count', 100)])
            stats = self.metadata['statistics']
            for field, value in [('potential_v', potential), ('current_a', current), ('time_s', t)]:
                stats[field + '_min'] = min(stats.get(field + '_min', value), value)
                stats[field + '_max'] = max(stats.get(field + '_max', value), value)
        with self.store.db() as db:
            db.executemany('INSERT INTO points VALUES (?,?,?,?,?)',
                           [(self.metadata['id'], *point) for point in points])
        self.trace = thin_points(self.trace + points, 1800)
        self.publish()

    def publish(self, force=False, **fields):
        self.metadata.update(fields)
        if not force and time.monotonic() - self.last_publish < .4:
            return
        self.last_publish = time.monotonic()
        self.metadata['updated_at'] = now()
        raw = json.dumps(self.metadata, ensure_ascii=False, allow_nan=False)
        with self.store.db() as db:
            db.execute('UPDATE datasets SET state=?,metadata=? WHERE id=?',
                       (self.metadata['state'], raw, self.metadata['id']))
        private_json(self.folder / 'metadata.json', self.metadata)
        private_json(self.folder / 'live.json', dict(points=self.trace,
            point_count=self.metadata['point_count'], updated_at=self.metadata['updated_at']))

    def finish(self, state, stop_confirmed=False, message=None, **fields):
        try:
            self.publish(force=True, state=state, stop_confirmed=stop_confirmed,
                         message=message, finished_at=now(), **fields)
        finally:
            self.raw.close(); self.csv.close(); self.closed = True
