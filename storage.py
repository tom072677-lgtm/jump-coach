"""로컬 개인 기록. 실제 시도와 분석 실행을 구분하며 영구 파일을 자동 삭제하지 않는다."""
import hashlib
import json
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def distance_mm(value):
    if value is None or value == '':
        return None
    try:
        n = Decimal(str(value)) * 10
        if not n.is_finite() or n <= 0 or n >= 2**63 or n != n.to_integral_value():
            raise ValueError
        return int(n)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('기록은 0보다 큰 cm 값으로, 소수 첫째 자리까지 입력하세요.') from None


def validity(value):
    if value not in ('valid', 'foul', 'unknown'):
        raise ValueError('유효 여부를 확인하세요.')
    return value


def validate_record_choice(record, unknown):
    if not isinstance(unknown, bool):
        raise ValueError('기록을 알 수 없는지 다시 선택하세요.')
    if record is not None and unknown:
        raise ValueError('실측 기록 입력과 알 수 없습니다 중 하나만 선택하세요.')


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / 'jumpcoach.sqlite3'
        self.lock = threading.RLock()
        self.maintenance = False
        self.active_uploads = 0
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('이 앱보다 새로운 데이터 형식입니다. 앱을 업데이트하세요.')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS athletes (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, athlete_id TEXT NOT NULL REFERENCES athletes(id),
                    performed_on TEXT NOT NULL, focus TEXT NOT NULL DEFAULT '',
                    practice_note TEXT NOT NULL DEFAULT '', UNIQUE(athlete_id,performed_on));
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    request_key TEXT NOT NULL UNIQUE, request_payload_hash TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL, state TEXT NOT NULL,
                    measured_distance_mm INTEGER, validity TEXT NOT NULL,
                    measurement_context_json TEXT NOT NULL, capture_context_json TEXT NOT NULL,
                    source_path TEXT, source_sha256 TEXT, original_name TEXT,
                    revision INTEGER NOT NULL DEFAULT 0, analysis_input_revision INTEGER NOT NULL DEFAULT 0,
                    analysis_config_json TEXT NOT NULL DEFAULT '{}',
                    selected_run_id TEXT REFERENCES analysis_runs(id), confirmed_selection_fingerprint TEXT,
                    analysis_included INTEGER NOT NULL DEFAULT 1,
                    superseded_by_attempt_id TEXT REFERENCES attempts(id), created_at TEXT NOT NULL,
                    deleted_at TEXT, UNIQUE(session_id,attempt_no));
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id),
                    request_key TEXT NOT NULL UNIQUE, request_payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
                    source_sha256 TEXT NOT NULL, analysis_input_revision INTEGER NOT NULL,
                    config_json TEXT NOT NULL, result_json TEXT, selection_fingerprint TEXT,
                    error_code TEXT, error_message TEXT);
                CREATE TABLE IF NOT EXISTS record_changes (
                    id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id),
                    changed_at TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS attempt_session ON attempts(session_id);
                CREATE INDEX IF NOT EXISTS run_attempt ON analysis_runs(attempt_id);
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            if self.maintenance:
                raise Conflict('백업 중입니다. 잠시 후 다시 저장하세요.')
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    @contextmanager
    def upload(self):
        with self.lock:
            if self.maintenance:
                raise Conflict('백업 중입니다. 잠시 후 다시 업로드하세요.')
            self.active_uploads += 1
        try:
            yield
        finally:
            with self.lock:
                self.active_uploads -= 1

    def safe_path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('잘못된 파일 경로입니다.')
        return path

    @staticmethod
    def require(db, table, key):
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (key,)).fetchone()
        if row is None:
            raise KeyError('기록을 찾지 못했습니다.')
        return dict(row)

    def save_athlete(self, name):
        name = ' '.join(str(name or '').split())
        if not name or len(name) > 60:
            raise ValueError('이름을 1~60자로 입력하세요.')
        with self.transaction() as db:
            db.execute('INSERT OR IGNORE INTO athletes VALUES (?,?,?)', (uuid.uuid4().hex, name, now()))
            return dict(db.execute('SELECT * FROM athletes WHERE name=?', (name,)).fetchone())

    def list_athletes(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM athletes ORDER BY created_at,name')]

    @staticmethod
    def session(db, athlete_id, performed_on):
        Store.require(db, 'athletes', athlete_id)
        date.fromisoformat(performed_on)
        db.execute('INSERT OR IGNORE INTO sessions(id,athlete_id,performed_on) VALUES (?,?,?)',
                   (uuid.uuid4().hex, athlete_id, performed_on))
        return dict(db.execute('SELECT * FROM sessions WHERE athlete_id=? AND performed_on=?',
                               (athlete_id, performed_on)).fetchone())

    def save_session(self, athlete_id, performed_on, focus='', practice_note=''):
        if len(focus) > 200 or len(practice_note) > 4000:
            raise ValueError('연습 메모가 너무 깁니다.')
        with self.transaction() as db:
            s = self.session(db, athlete_id, performed_on)
            db.execute('UPDATE sessions SET focus=?,practice_note=? WHERE id=?', (focus, practice_note, s['id']))
            return self.require(db, 'sessions', s['id'])

    def create_attempt(self, athlete_id, performed_on, request_key, measured_distance_cm=None,
                       validity='valid', commit=True, measurement_setup='학원 측정', capture_setup='측면 고정',
                       record_unknown=False):
        from storage import validity as check_validity
        record = distance_mm(measured_distance_cm)
        validate_record_choice(record, record_unknown)
        check_validity(validity)
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
            raise ValueError('저장 요청 식별자가 필요합니다.')
        values = [athlete_id, performed_on, record, validity, commit, measurement_setup, capture_setup]
        measurement = {'setup': str(measurement_setup)[:100]}
        if record_unknown:
            values.append('record_unknown')
            measurement['record_unknown'] = True
        payload = fingerprint(values)
        with self.transaction() as db:
            prior = db.execute('SELECT * FROM attempts WHERE request_key=?', (request_key,)).fetchone()
            if prior:
                if prior['request_payload_hash'] != payload:
                    raise Conflict('같은 저장 요청의 내용이 달라졌습니다.')
                aid = prior['id']
            else:
                s = self.session(db, athlete_id, performed_on)
                number = db.execute('SELECT COALESCE(MAX(attempt_no),0)+1 FROM attempts WHERE session_id=?', (s['id'],)).fetchone()[0]
                aid = uuid.uuid4().hex
                db.execute('''INSERT INTO attempts
                    (id,session_id,request_key,request_payload_hash,attempt_no,state,measured_distance_mm,
                     validity,measurement_context_json,capture_context_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (aid, s['id'], request_key, payload, number, 'committed' if commit else 'draft', record,
                     validity, encoded(measurement),
                     encoded({'setup': str(capture_setup)[:100]}), now()))
        return self.get_attempt(aid)

    def get_attempt(self, aid):
        with self.connection() as db:
            row = db.execute('''SELECT a.*,s.athlete_id,s.performed_on,s.focus,s.practice_note,p.name
                FROM attempts a JOIN sessions s ON a.session_id=s.id JOIN athletes p ON p.id=s.athlete_id
                WHERE a.id=?''', (aid,)).fetchone()
            if row is None:
                raise KeyError('기록을 찾지 못했습니다.')
            item = dict(row)
            for key in ('measurement_context', 'capture_context', 'analysis_config'):
                item[key] = json.loads(item.pop(key + '_json'))
            item['selected_run'] = self.get_run(item['selected_run_id']) if item['selected_run_id'] else None
            last = db.execute('SELECT id FROM analysis_runs WHERE attempt_id=? ORDER BY rowid DESC LIMIT 1', (aid,)).fetchone()
            item['latest_run'] = self.get_run(last['id']) if last else None
            selected = item['selected_run']
            item['analysis_stale'] = bool(selected and (selected['analysis_input_revision'] != item['analysis_input_revision']
                                                        or selected['source_sha256'] != item['source_sha256']))
            item['clip_confirmed'] = bool(selected and selected['selection_fingerprint']
                                         and selected['selection_fingerprint'] == item['confirmed_selection_fingerprint'])
            item['measured_distance_cm'] = item['measured_distance_mm'] / 10 if item['measured_distance_mm'] is not None else None
            item['record_unknown'] = item['measurement_context'].get('record_unknown') is True
            return item

    def list_attempts(self, athlete_id, deleted=False):
        with self.connection() as db:
            ids = db.execute('''SELECT a.id FROM attempts a JOIN sessions s ON s.id=a.session_id
                WHERE s.athlete_id=? AND (a.deleted_at IS NOT NULL)=? ORDER BY s.performed_on DESC,a.attempt_no DESC''',
                (athlete_id, int(deleted))).fetchall()
        return [self.get_attempt(row['id']) for row in ids]

    def update_attempt(self, aid, expected_revision, patch):
        with self.transaction() as db:
            before = self.require(db, 'attempts', aid)
            if before['revision'] != expected_revision:
                raise Conflict('기록이 변경됐습니다. 새로고침 후 다시 수정하세요.')
            after = dict(before)
            if 'measured_distance_cm' in patch:
                after['measured_distance_mm'] = distance_mm(patch['measured_distance_cm'])
            if 'measured_distance_cm' in patch or 'record_unknown' in patch:
                measurement = json.loads(after['measurement_context_json'])
                unknown = patch.get('record_unknown', measurement.get('record_unknown', False))
                if after['measured_distance_mm'] is not None and 'record_unknown' not in patch:
                    unknown = False
                validate_record_choice(after['measured_distance_mm'], unknown)
                measurement['record_unknown'] = unknown
                after['measurement_context_json'] = encoded(measurement)
            if 'validity' in patch:
                after['validity'] = validity(patch['validity'])
            if 'analysis_included' in patch:
                after['analysis_included'] = int(bool(patch['analysis_included']))
            if patch.get('commit'):
                after['state'] = 'committed'
            if 'deleted' in patch:
                if not patch['deleted'] and before['superseded_by_attempt_id']:
                    raise Conflict('정정된 기록입니다. 대체 시도를 열어 주세요.')
                after['deleted_at'] = now() if patch['deleted'] else None
            if 'athlete_id' in patch or 'performed_on' in patch:
                current = self.require(db, 'sessions', before['session_id'])
                s = self.session(db, patch.get('athlete_id', current['athlete_id']), patch.get('performed_on', current['performed_on']))
                if s['id'] != before['session_id']:
                    after['session_id'] = s['id']
                    after['attempt_no'] = db.execute('SELECT COALESCE(MAX(attempt_no),0)+1 FROM attempts WHERE session_id=?', (s['id'],)).fetchone()[0]
            after['revision'] += 1
            keys = ['measured_distance_mm','measurement_context_json','validity','analysis_included','state','deleted_at','session_id','attempt_no','revision']
            db.execute('UPDATE attempts SET ' + ','.join(k+'=?' for k in keys) + ' WHERE id=?', [after[k] for k in keys]+[aid])
            db.execute('INSERT INTO record_changes(attempt_id,changed_at,before_json,after_json) VALUES (?,?,?,?)',
                       (aid,now(),encoded(before),encoded(after)))
        return self.get_attempt(aid)

    def correct_attempt(self, aid, expected_revision, request_key, staged_file, original_name=None):
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
            raise ValueError('정정 요청 식별자가 필요합니다.')
        source = Path(staged_file)
        digest = file_hash(source)
        payload = fingerprint(['correction', aid, expected_revision, digest])
        with self.transaction() as db:
            existing = db.execute('SELECT * FROM attempts WHERE request_key=?', (request_key,)).fetchone()
            if existing:
                if existing['request_payload_hash'] != payload:
                    raise Conflict('정정 요청의 내용이 달라졌습니다.')
                replacement = existing['id']
            else:
                before = self.require(db, 'attempts', aid)
                if before['revision'] != expected_revision or before['deleted_at']:
                    raise Conflict('시도가 변경됐습니다. 다시 열어 주세요.')
                replacement = uuid.uuid4().hex
                target = self.root / 'attempts' / replacement / 'source' / (uuid.uuid4().hex + source.suffix.lower())
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open('rb') as inp, target.open('xb') as out:
                    shutil.copyfileobj(inp, out)
                number = db.execute('SELECT COALESCE(MAX(attempt_no),0)+1 FROM attempts WHERE session_id=?',
                                    (before['session_id'],)).fetchone()[0]
                db.execute('''INSERT INTO attempts
                    (id,session_id,request_key,request_payload_hash,attempt_no,state,measured_distance_mm,
                     validity,measurement_context_json,capture_context_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (replacement,before['session_id'],request_key,payload,number,before['state'],
                     before['measured_distance_mm'],before['validity'],before['measurement_context_json'],
                     before['capture_context_json'],now()))
                db.execute('UPDATE attempts SET source_path=?,source_sha256=?,original_name=?,revision=1 WHERE id=?',
                           (str(target.relative_to(self.root)),digest,original_name or source.name,replacement))
                db.execute('UPDATE attempts SET deleted_at=?,superseded_by_attempt_id=?,revision=revision+1 WHERE id=?',
                           (now(),replacement,aid))
                db.execute('INSERT INTO record_changes(attempt_id,changed_at,before_json,after_json) VALUES (?,?,?,?)',
                           (aid,now(),encoded(before),encoded(self.require(db,'attempts',aid))))
        return self.get_attempt(replacement)

    def attach_source(self, aid, staged_file, expected_revision, original_name=None):
        source = Path(staged_file)
        digest = file_hash(source)
        with self.transaction() as db:
            a = self.require(db, 'attempts', aid)
            if a['source_sha256'] == digest:
                pass
            elif a['source_path']:
                raise Conflict('이 시도에는 이미 다른 영상이 있습니다. 새 시도로 저장하세요.')
            elif a['revision'] != expected_revision or a['deleted_at']:
                raise Conflict('시도가 변경됐습니다. 다시 열어 주세요.')
            else:
                target = self.root / 'attempts' / aid / 'source' / (uuid.uuid4().hex + source.suffix.lower())
                target.parent.mkdir(parents=True, exist_ok=True)
                # Unique name + exclusive create, never replace a published source.
                with source.open('rb') as inp, target.open('xb') as out:
                    shutil.copyfileobj(inp, out)
                db.execute('UPDATE attempts SET source_path=?,source_sha256=?,original_name=?,revision=revision+1 WHERE id=?',
                           (str(target.relative_to(self.root)), digest, original_name or source.name, aid))
        return self.get_attempt(aid)

    def source_path(self, aid):
        item = self.get_attempt(aid)
        if not item['source_path']:
            raise KeyError('저장된 영상이 없습니다.')
        return self.safe_path(item['source_path'])

    def create_run(self, aid, request_key, expected_input_revision, config):
        payload = fingerprint([aid, expected_input_revision, config])
        with self.transaction() as db:
            a = self.require(db, 'attempts', aid)
            if a['deleted_at']:
                raise ValueError('삭제된 시도는 분석할 수 없습니다.')
            if a['measured_distance_mm'] is None and json.loads(a['measurement_context_json']).get('record_unknown') is not True:
                raise ValueError('실측 기록을 입력하거나 알 수 없습니다를 선택하세요.')
            existing = db.execute('SELECT * FROM analysis_runs WHERE request_key=?', (request_key,)).fetchone()
            if existing:
                if existing['request_payload_hash'] != payload:
                    raise Conflict('분석 요청의 내용이 달라졌습니다.')
                rid = existing['id']
            else:
                if not a['source_path'] or a['deleted_at']:
                    raise ValueError('분석할 영상을 먼저 저장하세요.')
                if a['analysis_input_revision'] != expected_input_revision:
                    raise Conflict('분석 설정이 변경됐습니다. 다시 열어 주세요.')
                revision = a['analysis_input_revision']
                changed = encoded(config) != a['analysis_config_json']
                if changed:
                    revision += 1
                    db.execute('''UPDATE attempts SET analysis_config_json=?,analysis_input_revision=?,
                        revision=revision+1,confirmed_selection_fingerprint=NULL WHERE id=?''', (encoded(config), revision, aid))
                rid = uuid.uuid4().hex
                db.execute('''INSERT INTO analysis_runs
                    (id,attempt_id,request_key,request_payload_hash,status,created_at,source_sha256,analysis_input_revision,config_json)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (rid,aid,request_key,payload,'queued',now(),a['source_sha256'],revision,encoded(config)))
        return self.get_run(rid)

    def get_run(self, rid):
        with self.connection() as db:
            row = self.require(db, 'analysis_runs', rid)
        row['config'] = json.loads(row.pop('config_json'))
        row['result'] = json.loads(row.pop('result_json') or 'null')
        return row

    def start_run(self, rid):
        with self.transaction() as db:
            return db.execute("UPDATE analysis_runs SET status='running' WHERE id=? AND status='queued'", (rid,)).rowcount == 1

    def finish_run(self, rid, result):
        with self.transaction() as db:
            run = self.require(db, 'analysis_runs', rid)
            if run['status'] not in ('queued','running'):
                raise Conflict('이미 종료된 분석입니다.')
            selection = fingerprint([run['source_sha256'], result.get('selection')])
            db.execute("UPDATE analysis_runs SET status='succeeded',finished_at=?,result_json=?,selection_fingerprint=? WHERE id=?",
                       (now(),encoded(result),selection,rid))
            db.execute('''UPDATE attempts SET selected_run_id=? WHERE id=? AND analysis_input_revision=? AND source_sha256=?''',
                       (rid,run['attempt_id'],run['analysis_input_revision'],run['source_sha256']))
        return self.get_run(rid)

    def fail_run(self, rid, code, message):
        with self.transaction() as db:
            db.execute("UPDATE analysis_runs SET status='failed',finished_at=?,error_code=?,error_message=? WHERE id=? AND status IN ('queued','running')",
                       (now(),code,str(message)[:1000],rid))

    def confirm_clip(self, aid, rid, expected_revision):
        with self.transaction() as db:
            a = self.require(db, 'attempts', aid)
            r = self.require(db, 'analysis_runs', rid)
            if (a['revision'] != expected_revision or a['selected_run_id'] != rid or r['attempt_id'] != aid
                    or r['status'] != 'succeeded' or r['analysis_input_revision'] != a['analysis_input_revision']):
                raise Conflict('확인할 분석이 변경됐습니다. 결과를 다시 열어 주세요.')
            db.execute("UPDATE attempts SET confirmed_selection_fingerprint=?,state='committed',revision=revision+1 WHERE id=?",
                       (r['selection_fingerprint'],aid))
        return self.get_attempt(aid)

    def recover_runs(self):
        with self.transaction() as db:
            db.execute("UPDATE analysis_runs SET status='interrupted',finished_at=?,error_message='앱이 종료되어 분석이 중단됐습니다. 다시 분석하세요.' WHERE status IN ('queued','running')", (now(),))

    def run_directory(self, rid):
        run = self.get_run(rid)
        path = self.root / 'attempts' / run['attempt_id'] / 'runs' / rid
        path.mkdir(parents=True, exist_ok=True)
        return path
