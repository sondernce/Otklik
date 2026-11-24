"""Persistent sessions and transactional login throttling for the local server."""
import hashlib
from http.cookies import CookieError, SimpleCookie
import re
import secrets
import time

SESSION_SECONDS = 8 * 60 * 60
TABLES = {'client': ('client_sessions', 'client_id'),
          'psychologist': ('psychologist_sessions', 'psychologist_id'),
          'admin': ('admin_sessions', None)}
COOKIE_NAMES = {'client': 'otklik_client', 'psychologist': 'otklik_psy', 'admin': 'otklik_admin'}


class Limited(ValueError):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def token(header, role):
    if not isinstance(header, str) or len(header) > 8192:
        return ''
    cookies = SimpleCookie()
    try:
        cookies.load(header)
    except CookieError:
        return ''
    item = cookies.get(COOKIE_NAMES[role])
    value = item.value if item else ''
    return value if re.fullmatch(r'[A-Za-z0-9_-]{43}', value) else ''


def cookie(role, value='', clear=False):
    age = 0 if clear else SESSION_SECONDS
    return f'{COOKIE_NAMES[role]}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}'


def issue(db, role, actor=None, previous=''):
    if role!='admin':ensure_active(db,role,actor)
    table, column = TABLES[role]
    stamp = time.time()
    db.execute(f'DELETE FROM {table} WHERE expires<=?', (stamp,))
    if previous:
        revoke(db, role, previous)
    value = secrets.token_urlsafe(32)
    if column:
        db.execute(f'INSERT INTO {table}(token_hash,{column},expires) VALUES(?,?,?)', (digest(value), actor, stamp+SESSION_SECONDS))
    else:
        db.execute(f'INSERT INTO {table}(token_hash,expires) VALUES(?,?)', (digest(value), stamp+SESSION_SECONDS))
    return value


def identify(db, role, value):
    if not value:
        return None
    table, column = TABLES[role]
    if role == 'admin':
        row = db.execute('SELECT 1 FROM admin_sessions WHERE token_hash=? AND expires>? '
                         "AND EXISTS(SELECT 1 FROM settings WHERE key='password')", (digest(value), time.time())).fetchone()
        return 'administrator' if row else None
    owner_table = 'clients' if role == 'client' else 'psychologists'
    row = db.execute(f'SELECT u.id FROM {table} s JOIN {owner_table} u ON u.id=s.{column} WHERE s.token_hash=? AND s.expires>?',
                     (digest(value), time.time())).fetchone()
    return row['id'] if row and active(db,role,row['id']) else None


def revoke(db, role, value):
    if value:
        db.execute(f'DELETE FROM {TABLES[role][0]} WHERE token_hash=?', (digest(value),))


def throttle(db, role, address, identity='', registration=False):
    """Reserve an attempt before password verification; failed authentication cannot roll it back."""
    stamp = time.time()
    # Independent account limits prevent one account from using every login attempt on this server.
    subject = identity.strip().lower()[:200] if isinstance(identity, str) else ''
    buckets = [(digest(f'ip:{role}:{address}'), 60)]
    if registration:
        buckets.append((digest(f'register:{role}:{address}'), 12))
    else:
        buckets.append((digest(f'account:{role}:{subject}'), 8 if role == 'admin' else 12))
    db.execute('BEGIN IMMEDIATE')
    db.execute('DELETE FROM login_attempts WHERE attempted_at<=?', (stamp-60,))
    for key, limit in buckets:
        if db.execute('SELECT count(*) FROM login_attempts WHERE bucket=?', (key,)).fetchone()[0] >= limit:
            raise Limited('Слишком много попыток. Подождите минуту.')
    db.executemany('INSERT INTO login_attempts(bucket,attempted_at) VALUES(?,?)', [(key, stamp) for key, _ in buckets])


def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()


def active(db,role,actor):
    if role=='admin':return True
    row=db.execute('SELECT status FROM account_states WHERE role=? AND account_id=?',(role,actor)).fetchone()
    return row is None or row[0]=='active'


def ensure_active(db,role,actor):
    if not active(db,role,actor):raise ValueError('Аккаунт заблокирован или удалён. Войдите в доступный аккаунт.')
