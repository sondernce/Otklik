"""SQLite connections, transactional migrations and pre-migration backups."""
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from timestamps import instant
from publications import publish

MIGRATION_LOCK = threading.RLock()
SCHEMA_VERSION = 6

class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
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
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
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
        if db.execute('PRAGMA user_version').fetchone()[0] == 4:
            db.execute('CREATE TABLE client_details (client_id TEXT PRIMARY KEY REFERENCES clients(id), age INTEGER NOT NULL CHECK(age BETWEEN 1 AND 120))')
            db.execute('CREATE TABLE appointment_video (appointment_id TEXT PRIMARY KEY REFERENCES appointments(id), url TEXT NOT NULL)')
            db.execute('PRAGMA user_version=5')
        if db.execute('PRAGMA user_version').fetchone()[0] == 5:
            db.execute('CREATE TABLE psychologist_sessions (token_hash TEXT PRIMARY KEY, psychologist_id TEXT NOT NULL REFERENCES psychologists(id), expires REAL NOT NULL)')
            db.execute('CREATE TABLE admin_sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL)')
            db.execute('CREATE INDEX psychologist_session_expiry ON psychologist_sessions(expires)')
            db.execute('CREATE INDEX admin_session_expiry ON admin_sessions(expires)')
            db.execute('CREATE INDEX client_session_expiry ON client_sessions(expires)')
            db.execute('CREATE TABLE login_attempts (bucket TEXT NOT NULL, attempted_at REAL NOT NULL)')
            db.execute('CREATE INDEX login_attempt_bucket ON login_attempts(bucket)')
            db.execute('CREATE INDEX login_attempt_expiry ON login_attempts(attempted_at)')
            db.execute('PRAGMA user_version=6')
        db.commit()
    except Exception:
        db.rollback()
        raise

