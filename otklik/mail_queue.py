"""Transactional mail outbox. SMTP submission is separate from HTTP transactions."""
from datetime import datetime, timezone
import hashlib
import json
import secrets
import time

KINDS={'verification','password_reset','appointment','reminder'}
TERMINAL=('sent','preview','failed','unknown','cancelled')


def install(db):
    db.execute("""CREATE TABLE IF NOT EXISTS mail_outbox (
        id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('verification','password_reset','appointment','reminder')),
        transport TEXT NOT NULL CHECK(transport IN ('local','smtp')),
        recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','preview','failed','unknown','cancelled')),
        attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
        next_attempt REAL NOT NULL, expires_at REAL NOT NULL,
        lease TEXT, lease_until REAL, last_error TEXT, finished_at TEXT
    )""")
    db.execute('CREATE INDEX IF NOT EXISTS mail_delivery_due ON mail_outbox(transport,status,next_attempt)')


def address(value):
    if not isinstance(value,str) or len(value)>254 or value.count('@')!=1 or any(ord(c)<33 or ord(c)>126 or c in '<>,;()\\\"' for c in value):
        raise ValueError('Укажите один адрес электронной почты без имени отправителя.')
    local,domain=value.rsplit('@',1)
    if not local or not domain or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
        raise ValueError('Проверьте адрес электронной почты.')
    return value


def enqueue(db, *, event_key, kind, recipient, subject, body, transport='local', expires_at=None):
    """Caller owns commit/rollback. Never call the transport from here."""
    if not isinstance(event_key,str) or not 1<=len(event_key)<=200 or kind not in KINDS or transport not in ('local','smtp'):
        raise ValueError('Неверные параметры почтового события.')
    recipient=address(recipient)
    if not isinstance(subject,str) or not 1<=len(subject)<=200 or any(ord(c)<32 for c in subject):
        raise ValueError('Неверная тема письма.')
    if not isinstance(body,str) or not 1<=len(body)<=20000 or '\x00' in body:
        raise ValueError('Неверное содержимое письма.')
    stamp=time.time();expiry=stamp+7*86400 if expires_at is None else expires_at
    if type(expiry) not in (int,float) or not 0<=expiry<=stamp+366*86400:
        raise ValueError('Неверный срок действия письма.')
    fingerprint=hashlib.sha256(json.dumps([kind,transport,recipient,subject,body],ensure_ascii=False).encode()).hexdigest()
    ident=secrets.token_hex(16)
    db.execute("INSERT INTO mail_outbox(id,event_key,fingerprint,kind,transport,recipient,subject,body,status,created_at,next_attempt,expires_at) VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?) ON CONFLICT(event_key) DO NOTHING",
               (ident,event_key,fingerprint,kind,transport,recipient,subject,body,datetime.fromtimestamp(stamp,timezone.utc).isoformat(),stamp,expiry))
    item=db.execute('SELECT id,fingerprint,status FROM mail_outbox WHERE event_key=?',(event_key,)).fetchone()
    if item['fingerprint']!=fingerprint:
        raise ValueError('Ключ письма уже использован для другого события.')
    return {'id':item['id'],'status':item['status']}


def claim(db,transport,stamp=None):
    if transport not in ('local','smtp'):raise ValueError('Неверный режим почты.')
    stamp=time.time() if stamp is None else stamp
    db.execute('BEGIN IMMEDIATE')
    # A dead local worker is safe to repeat: preview publication is idempotent.
    # An abandoned SMTP DATA may have been accepted; never automatically resend it.
    db.execute("UPDATE mail_outbox SET status=CASE WHEN transport='local' THEN 'pending' ELSE 'unknown' END,lease=NULL,lease_until=NULL,last_error='worker_interrupted' WHERE status='sending' AND lease_until<=?",(stamp,))
    db.execute("UPDATE mail_outbox SET status='cancelled',recipient='',subject='',body='',lease=NULL,lease_until=NULL,last_error='expired',finished_at=? WHERE status IN ('pending','failed','unknown') AND expires_at<=?",(datetime.fromtimestamp(stamp,timezone.utc).isoformat(),stamp))
    row=db.execute("SELECT * FROM mail_outbox WHERE transport=? AND status='pending' AND next_attempt<=? AND expires_at>? ORDER BY next_attempt,id LIMIT 1",(transport,stamp,stamp)).fetchone()
    if not row:return None
    lease=secrets.token_hex(16)
    db.execute("UPDATE mail_outbox SET status='sending',attempts=attempts+1,lease=?,lease_until=? WHERE id=?",(lease,stamp+120,row['id']))
    return dict(row)|{'lease':lease,'attempts':row['attempts']+1}


def finish(db,item,outcome,error=None,stamp=None):
    if outcome not in ('sent','preview','retry','failed','unknown'):
        raise ValueError('Неверный результат отправки.')
    if error not in (None,'smtp_temporary','smtp_rejected','connection_failed','delivery_unknown','preview_failed'):
        raise ValueError('Неверный код почтовой ошибки.')
    stamp=time.time() if stamp is None else stamp
    state='failed' if outcome=='retry' and item['attempts']>=6 else 'pending' if outcome=='retry' else outcome
    clear=state in ('sent','preview','failed')
    cursor=db.execute("UPDATE mail_outbox SET status=?,last_error=?,next_attempt=?,lease=NULL,lease_until=NULL,finished_at=?,recipient=CASE WHEN ? THEN '' ELSE recipient END,subject=CASE WHEN ? THEN '' ELSE subject END,body=CASE WHEN ? THEN '' ELSE body END WHERE id=? AND status='sending' AND lease=?",
                      (state,error,stamp+min(60*2**min(item['attempts']-1,6),3600),None if state=='pending' else datetime.fromtimestamp(stamp,timezone.utc).isoformat(),clear,clear,clear,item['id'],item['lease']))
    return cursor.rowcount==1


def summary(db):
    counts={state:0 for state in ('pending','sending','sent','preview','failed','unknown','cancelled')}
    counts.update({row['status']:row['n'] for row in db.execute('SELECT status,count(*) n FROM mail_outbox GROUP BY status')})
    return {'counts':counts,'latest':[dict(row) for row in db.execute('SELECT id,kind,transport,status,attempts,created_at,last_error FROM mail_outbox ORDER BY created_at DESC,id DESC LIMIT 20')]}
