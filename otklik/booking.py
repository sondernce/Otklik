"""SQLite migrations and booking operations shared by the existing HTTP server."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from contextlib import closing

MIGRATION_LOCK = threading.RLock()
APPROVAL_HOURS = 12
SCHEMA_VERSION = 4
PUBLIC_FIELDS = {'id', 'name', 'initials', 'gender', 'price', 'experience', 'topics', 'style', 'approach', 'bio', 'photo', 'education'}


class Conflict(ValueError):
    pass


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    if not isinstance(value, str):
        raise ValueError('Проверьте дату и время.')
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Проверьте дату и время.') from None
    if result.tzinfo is None or result.second or result.microsecond:
        raise ValueError('Время должно содержать часовой пояс и целые минуты.')
    return result.astimezone(timezone.utc)


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5, factory=Connection)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] < SCHEMA_VERSION:
            with MIGRATION_LOCK:
                migrate(db, path)
        return db
    except Exception:
        db.close()
        raise


def migrate(db, path):
    if db.execute('PRAGMA user_version').fetchone()[0] >= SCHEMA_VERSION:
        return
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
        folder = path.parent / 'backups'
        folder.mkdir(exist_ok=True)
        backup = folder / (path.name + '.before-booking-' + secrets.token_hex(6))
        with closing(sqlite3.connect(backup)) as destination:
            db.backup(destination)
    db.execute('BEGIN IMMEDIATE')
    try:
        # Another process may have finished the migration while this one waited.
        if db.execute('PRAGMA user_version').fetchone()[0] >= SCHEMA_VERSION:
            db.commit()
            return
        if db.execute('PRAGMA user_version').fetchone()[0] == 0:
            statements = [
                'CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS psychologists (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL)',
                'CREATE TABLE clients (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL)',
                'CREATE TABLE client_sessions (token_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id), expires REAL NOT NULL)',
                'CREATE TABLE booking_settings (profile_id TEXT PRIMARY KEY REFERENCES profiles(id), auto_approve INTEGER NOT NULL DEFAULT 0 CHECK(auto_approve IN (0,1)))',
                'CREATE TABLE slots (id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id), start_at TEXT NOT NULL, end_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), UNIQUE(profile_id,start_at))',
                "CREATE TABLE appointments (id TEXT PRIMARY KEY, slot_id TEXT NOT NULL REFERENCES slots(id), client_id TEXT NOT NULL REFERENCES clients(id), profile_id TEXT NOT NULL REFERENCES profiles(id), start_at TEXT NOT NULL, end_at TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>=0), psychologist_name TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','confirmed','declined','expired')), created_at TEXT NOT NULL, expires_at TEXT, request_key TEXT NOT NULL, UNIQUE(client_id,request_key))",
                "CREATE UNIQUE INDEX one_active_booking ON appointments(slot_id) WHERE status IN ('pending','confirmed')",
                'CREATE INDEX appointments_client_time ON appointments(client_id,start_at)',
                'CREATE INDEX appointments_psychologist_time ON appointments(profile_id,start_at)',
            ]
            for statement in statements:
                db.execute(statement)
            for row in db.execute('SELECT id,data FROM profiles').fetchall():
                p = json.loads(row['data'])
                if 'education_public' in p:
                    internal = p.get('education', '').strip()
                    public = p.pop('education_public', '').strip()
                    p['education'] = internal if not public or public in internal else (internal + '\n\n' + public).strip()
                    db.execute('UPDATE profiles SET data=? WHERE id=?', (json.dumps(p, ensure_ascii=False), row['id']))
                for value in p.get('slots', []):
                    start = instant(value)
                    db.execute('INSERT INTO slots VALUES(?,?,?,?,1)', (secrets.token_hex(12), row['id'], start.isoformat(), (start + timedelta(minutes=50)).isoformat()))
            db.execute('PRAGMA user_version=1')
        if db.execute('PRAGMA user_version').fetchone()[0] == 1:
            db.execute('ALTER TABLE appointments ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1))')
            db.execute('ALTER TABLE appointments ADD COLUMN cancelled_by TEXT')
            db.execute('ALTER TABLE appointments ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
            db.execute('DROP INDEX one_active_booking')
            db.execute("CREATE UNIQUE INDEX one_active_booking ON appointments(slot_id) WHERE status IN ('pending','confirmed') AND cancelled=0")
            db.execute('CREATE TABLE profile_publications (profile_id TEXT PRIMARY KEY REFERENCES profiles(id), data TEXT NOT NULL, published_at TEXT NOT NULL)')
            db.execute("CREATE TABLE appointment_changes (id TEXT PRIMARY KEY, appointment_id TEXT NOT NULL REFERENCES appointments(id), slot_id TEXT NOT NULL REFERENCES slots(id), start_at TEXT NOT NULL, end_at TEXT NOT NULL, offered_by TEXT NOT NULL CHECK(offered_by IN ('client','psychologist')), status TEXT NOT NULL CHECK(status IN ('pending','accepted','declined','expired','cancelled')), expires_at TEXT NOT NULL, created_at TEXT NOT NULL, request_key TEXT NOT NULL, UNIQUE(appointment_id,request_key))")
            db.execute("CREATE UNIQUE INDEX one_pending_change ON appointment_changes(appointment_id) WHERE status='pending'")
            db.execute('CREATE TABLE appointment_events (id INTEGER PRIMARY KEY, appointment_id TEXT NOT NULL REFERENCES appointments(id), action TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, from_start TEXT, to_start TEXT)')
            for row in db.execute('SELECT data FROM profiles').fetchall():
                p = json.loads(row[0])
                if p.get('status') == 'approved':
                    publish(db, p)
            db.execute('PRAGMA user_version=2')
        if db.execute('PRAGMA user_version').fetchone()[0] == 2:
            db.execute("CREATE TABLE appointment_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, appointment_id TEXT NOT NULL REFERENCES appointments(id), sender_role TEXT NOT NULL CHECK(sender_role IN ('client','psychologist')), sender_id TEXT NOT NULL, body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000), request_key TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(appointment_id,sender_role,sender_id,request_key))")
            db.execute('CREATE INDEX appointment_message_feed ON appointment_messages(appointment_id,id)')
            db.execute("CREATE TABLE support_tickets (id TEXT PRIMARY KEY, owner_role TEXT NOT NULL CHECK(owner_role IN ('client','psychologist')), owner_id TEXT NOT NULL, subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 150), status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')), revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, request_key TEXT NOT NULL, UNIQUE(owner_role,owner_id,request_key))")
            db.execute("CREATE TABLE support_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL REFERENCES support_tickets(id), sender_role TEXT NOT NULL CHECK(sender_role IN ('client','psychologist','admin')), sender_id TEXT NOT NULL, body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000), request_key TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(ticket_id,sender_role,sender_id,request_key))")
            db.execute('CREATE INDEX support_message_feed ON support_messages(ticket_id,id)')
            db.execute('CREATE INDEX support_ticket_owner ON support_tickets(owner_role,owner_id,updated_at)')
            db.execute('PRAGMA user_version=3')
        if db.execute('PRAGMA user_version').fetchone()[0] == 3:
            import notifications
            notifications.install(db)
            db.execute('PRAGMA user_version=4')
        db.commit()
    except Exception:
        db.rollback()
        raise


def expire(db):
    db.execute("UPDATE appointment_changes SET status='expired' WHERE status='pending' AND (expires_at<=? OR start_at<=? OR EXISTS(SELECT 1 FROM appointments a WHERE a.id=appointment_id AND (a.start_at<=? OR a.cancelled=1)))", (now(), now(), now()))
    db.execute("UPDATE appointments SET status='expired' WHERE status='pending' AND (expires_at<=? OR start_at<=?)", (now(), now()))


def active_clause(alias='a'):
    return f"({alias}.cancelled=0 AND ({alias}.status='confirmed' OR ({alias}.status='pending' AND {alias}.expires_at>? AND {alias}.start_at>?)))"


def available_slots(db, profile_id):
    stamp = now()
    return [r[0] for r in db.execute(
        "SELECT s.start_at FROM slots s WHERE s.profile_id=? AND s.active=1 AND s.start_at>? "
        "AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.profile_id=s.profile_id "
        "AND a.start_at<s.end_at AND a.end_at>s.start_at AND " + active_clause() + ") AND NOT EXISTS (SELECT 1 FROM appointment_changes c JOIN appointments a ON a.id=c.appointment_id WHERE a.profile_id=s.profile_id AND a.cancelled=0 AND a.start_at>? AND c.status='pending' AND c.expires_at>? AND c.start_at<s.end_at AND c.end_at>s.start_at) ORDER BY s.start_at",
        (profile_id, stamp, stamp, stamp, stamp, stamp))]


def sync_slots(db, profile_id, starts):
    """Keep JSON schedule compatible while slots become separately addressable."""
    desired = {instant(value).isoformat() for value in starts}
    expire(db)
    stamp = now()
    busy = db.execute('SELECT a.start_at FROM appointments a WHERE a.profile_id=? AND a.end_at>? AND ' + active_clause(),
                      (profile_id, stamp, stamp, stamp)).fetchall()
    changes = db.execute("SELECT c.start_at FROM appointment_changes c JOIN appointments a ON a.id=c.appointment_id WHERE a.profile_id=? AND c.status='pending' AND c.expires_at>? AND a.cancelled=0", (profile_id, stamp)).fetchall()
    if any(row[0] not in desired for row in [*busy, *changes]):
        raise Conflict('На это время уже есть запись. Занятое окно нельзя удалить.')
    db.execute('UPDATE slots SET active=0 WHERE profile_id=?', (profile_id,))
    for value in desired:
        start = instant(value)
        end = (start + timedelta(minutes=50)).isoformat()
        db.execute('INSERT INTO slots(id,profile_id,start_at,end_at,active) VALUES(?,?,?,?,1) '
                   'ON CONFLICT(profile_id,start_at) DO UPDATE SET active=1',
                   (secrets.token_hex(12), profile_id, value, end))


def create(db, client_id, data):
    key = data.get('request_key')
    if not isinstance(key, str) or not 16 <= len(key) <= 100:
        raise ValueError('Обновите страницу записи и повторите попытку.')
    profile_id = data.get('psychologist_id')
    if not isinstance(profile_id, str):
        raise ValueError('Выберите психолога.')
    start = instant(data.get('start_at')).isoformat()
    db.execute('BEGIN IMMEDIATE')
    expire(db)
    previous = db.execute('SELECT * FROM appointments WHERE client_id=? AND request_key=?', (client_id, key)).fetchone()
    if previous:
        if previous['profile_id'] != profile_id or previous['start_at'] != start:
            raise Conflict('Этот запрос уже использован для другой встречи.')
        return dict(previous)
    row = db.execute('SELECT data FROM profiles WHERE id=?', (profile_id,)).fetchone()
    p = json.loads(row[0]) if row else {}
    public = publication(db, profile_id)
    if not public or p.get('hidden') or p.get('deleted'):
        raise Conflict('Психолог сейчас недоступен для новых записей.')
    slot = db.execute('SELECT * FROM slots WHERE profile_id=? AND start_at=? AND active=1', (profile_id, start)).fetchone()
    if not slot or start <= now():
        raise Conflict('Это время уже недоступно. Выберите другое окно.')
    stamp = now()
    collision = db.execute('SELECT 1 FROM appointments a WHERE (a.profile_id=? OR a.client_id=?) '
                           'AND a.start_at<? AND a.end_at>? AND ' + active_clause(),
                           (profile_id, client_id, slot['end_at'], start, stamp, stamp)).fetchone()
    if collision or change_collision(db, profile_id, client_id, start, slot['end_at']):
        raise Conflict('В это время уже есть запись. Выберите другое окно.')
    setting = db.execute('SELECT auto_approve FROM booking_settings WHERE profile_id=?', (profile_id,)).fetchone()
    auto = bool(setting and setting[0])
    expires = None if auto else min(datetime.now(timezone.utc) + timedelta(hours=APPROVAL_HOURS), instant(start)).isoformat()
    ident = secrets.token_hex(12)
    db.execute('INSERT INTO appointments(id,slot_id,client_id,profile_id,start_at,end_at,price,psychologist_name,status,created_at,expires_at,request_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (ident, slot['id'], client_id, profile_id, start, slot['end_at'], public['price'], public['name'],
                'confirmed' if auto else 'pending', stamp, expires, key))
    return dict(db.execute('SELECT * FROM appointments WHERE id=?', (ident,)).fetchone())


def decide(db, owner, ident, action):
    if not isinstance(ident, str) or action not in {'confirm', 'decline'}:
        raise ValueError('Выберите заявку и действие.')
    db.execute('BEGIN IMMEDIATE')
    expire(db)
    row = db.execute('SELECT a.* FROM appointments a JOIN profiles p ON p.id=a.profile_id '
                     "WHERE a.id=? AND json_extract(p.data,'$.owner_id')=?", (ident, owner)).fetchone()
    if not row:
        raise Conflict('Заявка не найдена или недоступна.')
    target = 'confirmed' if action == 'confirm' else 'declined'
    if row['cancelled']:
        raise Conflict('Встреча отменена.')
    if row['status'] == target:
        return dict(row)
    if row['status'] != 'pending':
        raise Conflict('Заявка уже рассмотрена или срок подтверждения истёк.')
    db.execute('UPDATE appointments SET status=?,revision=revision+1 WHERE id=?', (target, ident))
    return dict(db.execute('SELECT * FROM appointments WHERE id=?', (ident,)).fetchone())


def listings(db, role, ident=None):
    condition, params = '', ()
    if role == 'client':
        condition, params = 'WHERE a.client_id=?', (ident,)
    elif role == 'psychologist':
        condition, params = "WHERE json_extract(p.data,'$.owner_id')=?", (ident,)
    rows = db.execute('SELECT a.*,c.name AS client_name FROM appointments a '
                      'JOIN profiles p ON p.id=a.profile_id JOIN clients c ON c.id=a.client_id '
                      f'{condition} ORDER BY a.start_at DESC', params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        if item['status'] == 'pending' and (item['expires_at'] <= now() or item['start_at'] <= now()):
            item['status'] = 'expired'
        item['change'] = pending_change(db, item['id'])
        item['history'] = [dict(event) for event in db.execute('SELECT action,actor,at,from_start,to_start FROM appointment_events WHERE appointment_id=? ORDER BY id', (item['id'],))]
        result.append(public_appointment(item))
    return result


def public_appointment(item):
    item = dict(item)
    if item.get('cancelled'):
        item['status'] = 'cancelled'
    return {key: item[key] for key in ('id', 'profile_id', 'start_at', 'end_at', 'price', 'psychologist_name', 'status', 'expires_at', 'revision', 'cancelled_by', 'change', 'history') if key in item} | (
        {'client_name': item['client_name']} if 'client_name' in item else {})


def publish(db, profile):
    public = {key: value for key, value in profile.items() if key in PUBLIC_FIELDS}
    db.execute('INSERT INTO profile_publications VALUES(?,?,?) ON CONFLICT(profile_id) '
               'DO UPDATE SET data=excluded.data,published_at=excluded.published_at',
               (profile['id'], json.dumps(public, ensure_ascii=False), now()))


def publication(db, profile_id):
    row = db.execute('SELECT data FROM profile_publications WHERE profile_id=?', (profile_id,)).fetchone()
    return json.loads(row[0]) if row else None


def change_collision(db, profile_id, client_id, start, end, excluded=''):
    stamp = now()
    return db.execute("SELECT 1 FROM appointment_changes c JOIN appointments a ON a.id=c.appointment_id "
                      "WHERE (a.profile_id=? OR a.client_id=?) AND a.cancelled=0 AND a.start_at>? "
                      "AND c.id<>? AND c.status='pending' AND c.expires_at>? AND c.start_at<? AND c.end_at>?",
                      (profile_id, client_id, stamp, excluded, stamp, end, start)).fetchone() is not None


def pending_change(db, appointment_id):
    stamp = now()
    row = db.execute("SELECT c.id,c.start_at,c.end_at,c.offered_by,c.expires_at FROM appointment_changes c "
                     "JOIN appointments a ON a.id=c.appointment_id WHERE c.appointment_id=? AND a.cancelled=0 "
                     "AND a.start_at>? AND c.status='pending' AND c.expires_at>? AND c.start_at>?",
                     (appointment_id, stamp, stamp, stamp)).fetchone()
    return dict(row) if row else None


def owned_appointment(db, role, actor, ident):
    if not isinstance(ident, str):
        raise ValueError('Выберите встречу.')
    if role == 'client':
        condition = 'a.client_id=?'
    elif role == 'psychologist':
        condition = "json_extract(p.data,'$.owner_id')=?"
    else:
        raise Conflict('Действие недоступно.')
    row = db.execute('SELECT a.* FROM appointments a JOIN profiles p ON p.id=a.profile_id '
                     f'WHERE a.id=? AND {condition}', (ident, actor)).fetchone()
    if not row:
        raise Conflict('Встреча не найдена или недоступна.')
    return dict(row)


def check_revision(item, data):
    if type(data.get('revision')) is not int or data['revision'] != item['revision']:
        raise Conflict('Встреча изменилась. Обновите список и повторите действие.')


def check_future(item, confirmed=False):
    if item['cancelled'] or item['start_at'] <= now() or item['status'] not in ({'confirmed'} if confirmed else {'pending', 'confirmed'}):
        raise Conflict('Эту встречу уже нельзя изменить.')


def audit(db, item, action, actor, target=None):
    db.execute('INSERT INTO appointment_events(appointment_id,action,actor,at,from_start,to_start) VALUES(?,?,?,?,?,?)',
               (item['id'], action, actor, now(), item['start_at'], target))


def cancel(db, role, actor, data):
    db.execute('BEGIN IMMEDIATE')
    expire(db)
    item = owned_appointment(db, role, actor, data.get('id'))
    if item['cancelled']:
        return item
    check_revision(item, data)
    check_future(item)
    db.execute('UPDATE appointments SET cancelled=1,cancelled_by=?,revision=revision+1 WHERE id=?', (role, item['id']))
    db.execute("UPDATE appointment_changes SET status='cancelled' WHERE appointment_id=? AND status='pending'", (item['id'],))
    audit(db, item, 'cancelled', role)
    return dict(db.execute('SELECT * FROM appointments WHERE id=?', (item['id'],)).fetchone())


def busy_for_change(db, item, start, end, change_id=''):
    stamp = now()
    booked = db.execute('SELECT 1 FROM appointments a WHERE a.id<>? AND (a.profile_id=? OR a.client_id=?) '
                         'AND a.start_at<? AND a.end_at>? AND ' + active_clause(),
                         (item['id'], item['profile_id'], item['client_id'], end, start, stamp, stamp)).fetchone()
    return bool(booked) or change_collision(db, item['profile_id'], item['client_id'], start, end, change_id)


def change_options(db, role, actor, ident):
    item = owned_appointment(db, role, actor, ident)
    check_future(item, confirmed=True)
    result = []
    for start in available_slots(db, item['profile_id']):
        end = (instant(start) + timedelta(minutes=50)).isoformat()
        if not busy_for_change(db, item, start, end):
            result.append(start)
    return {'slots': result, 'revision': item['revision']}


def apply_change(db, item, change, actor):
    slot = db.execute('SELECT * FROM slots WHERE id=? AND active=1', (change['slot_id'],)).fetchone()
    if not slot or slot['start_at'] <= now() or busy_for_change(db, item, change['start_at'], change['end_at'], change['id']):
        raise Conflict('Предложенное время уже недоступно. Исходная встреча сохранена.')
    db.execute('UPDATE appointments SET slot_id=?,start_at=?,end_at=?,revision=revision+1 WHERE id=?',
               (slot['id'], slot['start_at'], slot['end_at'], item['id']))
    db.execute("UPDATE appointment_changes SET status='accepted' WHERE id=?", (change['id'],))
    audit(db, item, 'rescheduled', actor, change['start_at'])


def propose_change(db, role, actor, data):
    key = data.get('request_key')
    if not isinstance(key, str) or not 16 <= len(key) <= 100:
        raise ValueError('Обновите форму переноса.')
    start = instant(data.get('start_at')).isoformat()
    db.execute('BEGIN IMMEDIATE')
    expire(db)
    item = owned_appointment(db, role, actor, data.get('id'))
    previous = db.execute('SELECT * FROM appointment_changes WHERE appointment_id=? AND request_key=?', (item['id'], key)).fetchone()
    if previous:
        if previous['offered_by'] != role or previous['start_at'] != start:
            raise Conflict('Этот запрос уже использован для другого переноса.')
        return item
    check_revision(item, data)
    check_future(item, confirmed=True)
    if pending_change(db, item['id']):
        raise Conflict('Сначала примите, отклоните или отзовите текущее предложение переноса.')
    slot = db.execute('SELECT * FROM slots WHERE profile_id=? AND start_at=? AND active=1', (item['profile_id'], start)).fetchone()
    if not slot or start <= now() or start == item['start_at'] or busy_for_change(db, item, start, slot['end_at']):
        raise Conflict('Это время недоступно. Исходная встреча сохранена.')
    deadline = min(datetime.now(timezone.utc)+timedelta(hours=APPROVAL_HOURS), instant(item['start_at']), instant(start)).isoformat()
    ident = secrets.token_hex(12)
    db.execute('INSERT INTO appointment_changes VALUES(?,?,?,?,?,?,?,?,?,?)',
               (ident, item['id'], slot['id'], start, slot['end_at'], role, 'pending', deadline, now(), key))
    change = dict(db.execute('SELECT * FROM appointment_changes WHERE id=?', (ident,)).fetchone())
    setting = db.execute('SELECT auto_approve FROM booking_settings WHERE profile_id=?', (item['profile_id'],)).fetchone()
    if role == 'client' and setting and setting[0]:
        apply_change(db, item, change, role)
    else:
        db.execute('UPDATE appointments SET revision=revision+1 WHERE id=?', (item['id'],))
        audit(db, item, 'change_proposed', role, start)
    return dict(db.execute('SELECT * FROM appointments WHERE id=?', (item['id'],)).fetchone())


def respond_change(db, role, actor, data):
    if data.get('action') not in {'accept', 'decline', 'withdraw'} or not isinstance(data.get('change_id'), str):
        raise ValueError('Выберите предложение и действие.')
    db.execute('BEGIN IMMEDIATE')
    expire(db)
    item = owned_appointment(db, role, actor, data.get('id'))
    row = db.execute('SELECT * FROM appointment_changes WHERE id=? AND appointment_id=?', (data['change_id'], item['id'])).fetchone()
    if not row:
        raise Conflict('Предложение не найдено.')
    change = dict(row)
    action = data['action']
    if (action == 'withdraw') != (role == change['offered_by']):
        raise Conflict('Подтвердить перенос может только другая сторона.')
    target = {'accept': 'accepted', 'decline': 'declined', 'withdraw': 'cancelled'}[action]
    if change['status'] == target:
        return item
    check_future(item, confirmed=True)
    if change['status'] != 'pending':
        raise Conflict('Предложение уже рассмотрено или срок ответа истёк.')
    if action == 'accept':
        apply_change(db, item, change, role)
    else:
        db.execute('UPDATE appointment_changes SET status=? WHERE id=?', (target, change['id']))
        db.execute('UPDATE appointments SET revision=revision+1 WHERE id=?', (item['id'],))
        audit(db, item, 'change_' + target, role, change['start_at'])
    return dict(db.execute('SELECT * FROM appointments WHERE id=?', (item['id'],)).fetchone())
