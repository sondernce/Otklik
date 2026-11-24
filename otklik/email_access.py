"""Single-use email verification and password recovery, using the transactional outbox."""
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

import access
import mail_queue

TABLES={'client':'clients','psychologist':'psychologists'}
VERIFY_SECONDS=24*3600
RESET_SECONDS=30*60
RESET_REPLY='Если аккаунт с такой почтой существует, письмо для восстановления поставлено в очередь. Проверьте последние письма.'
INVALID_LINK='Ссылка недействительна, уже использована или срок её действия истёк. Запросите новую.'


def install(db):
    db.execute("CREATE TABLE IF NOT EXISTS email_status (role TEXT NOT NULL,account_id TEXT NOT NULL,email TEXT NOT NULL,verified_at REAL,PRIMARY KEY(role,account_id))")
    db.execute("""CREATE TABLE IF NOT EXISTS email_tokens (
        token_hash TEXT PRIMARY KEY,role TEXT NOT NULL,account_id TEXT NOT NULL,email TEXT NOT NULL,
        purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')),created_at REAL NOT NULL,
        expires_at REAL NOT NULL,used_at REAL,revoked INTEGER NOT NULL DEFAULT 0,
        mail_id TEXT NOT NULL REFERENCES mail_outbox(id))""")
    db.execute('CREATE INDEX IF NOT EXISTS email_token_account ON email_tokens(role,account_id,purpose,created_at)')
    db.execute('CREATE TABLE IF NOT EXISTS email_requests (bucket TEXT NOT NULL,requested_at REAL NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS email_request_bucket ON email_requests(bucket,requested_at)')


def settings(origin):
    transport=os.environ.get('OTKLIK_MAIL_TRANSPORT','local')
    if transport not in ('local','smtp'):raise ValueError('Проверьте режим отправки почты.')
    base=origin if transport=='local' else os.environ.get('OTKLIK_PUBLIC_URL','')
    try:
        parsed=urlsplit(base);port=parsed.port
    except (ValueError,TypeError):
        raise ValueError('Укажите допустимый адрес сайта для почтовых ссылок.') from None
    if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ('','/') or '\\' in base or (port is not None and not 1<=port<=65535) or any(c.isspace() or ord(c)<32 for c in base)
            or (transport=='smtp' and parsed.scheme!='https')
            or (transport=='local' and parsed.hostname not in ('127.0.0.1','localhost'))):
        raise ValueError('Укажите допустимый адрес сайта для почтовых ссылок.')
    return transport,base.rstrip('/')


def normalize_email(value):
    if not isinstance(value,str) or len(value)>200:raise ValueError('Введите корректную электронную почту.')
    value=value.strip().lower()
    try:
        local,domain=value.rsplit('@',1)
        value=local+'@'+domain.encode('idna').decode('ascii')
        return mail_queue.address(value)
    except (ValueError,UnicodeError):
        raise ValueError('Введите корректную электронную почту с именем ящика латиницей.') from None


def verified(db,role,account_id,email):
    return db.execute('SELECT 1 FROM email_status WHERE role=? AND account_id=? AND email=? AND verified_at IS NOT NULL',(role,account_id,email)).fetchone() is not None


def account(db,role,account_id):
    return db.execute(f'SELECT id,email FROM {TABLES[role]} WHERE id=?',(account_id,)).fetchone()


def throttle(db,role,purpose,address,email):
    """Independent of account existence; suppressed requests get the same public reply."""
    stamp=time.time();db.execute('DELETE FROM email_requests WHERE requested_at<?',(stamp-3600,))
    ip=access.digest(f'email-ip:{address}')
    if db.execute('SELECT count(*) FROM email_requests WHERE bucket=? AND requested_at>?',(ip,stamp-3600)).fetchone()[0]>=30:
        raise access.Limited('Слишком много запросов. Попробуйте позже.')
    db.execute('INSERT INTO email_requests VALUES(?,?)',(ip,stamp))
    identity=access.digest(f'email-account:{role}:{purpose}:{email}')
    recent=db.execute('SELECT requested_at FROM email_requests WHERE bucket=? AND requested_at>? ORDER BY requested_at DESC',(identity,stamp-900)).fetchall()
    if len(recent)>=3 or (recent and recent[0][0]>stamp-60):return False
    db.execute('INSERT INTO email_requests VALUES(?,?)',(identity,stamp))
    return True


def invalidate(db,role,account_id,purpose=None):
    query='role=? AND account_id=? AND used_at IS NULL AND revoked=0';args=[role,account_id]
    if purpose:query+=' AND purpose=?';args.append(purpose)
    db.execute("UPDATE mail_outbox SET status='cancelled',recipient='',subject='',body='',lease=NULL,lease_until=NULL WHERE id IN (SELECT mail_id FROM email_tokens WHERE "+query+") AND status IN ('pending','unknown')",args)
    db.execute('UPDATE email_tokens SET revoked=1 WHERE '+query,args)


def issue(db,role,item,purpose,origin):
    transport,base=settings(origin)
    stamp=time.time();expiry=stamp+(VERIFY_SECONDS if purpose=='verify' else RESET_SECONDS)
    token=secrets.token_urlsafe(32);digest=access.digest(token)
    invalidate(db,role,item['id'],purpose)
    link=base+'/account-access#'+urlencode({'role':role,'purpose':purpose,'token':token})
    subject='Подтверждение почты в Отклике' if purpose=='verify' else 'Восстановление пароля в Отклике'
    text=('Чтобы подтвердить адрес, откройте ссылку и нажмите «Подтвердить почту». Ссылка действует 24 часа.' if purpose=='verify' else 'Чтобы установить новый пароль, откройте ссылку. Ссылка действует 30 минут.')
    mail=mail_queue.enqueue(db,event_key='access:'+digest,kind='verification' if purpose=='verify' else 'password_reset',recipient=normalize_email(item['email']),subject=subject,
        body=text+'\n\n'+link+'\n\nЕсли вы не запрашивали это письмо, ничего не делайте. Ссылку никому не передавайте.',transport=transport,expires_at=expiry)
    db.execute('INSERT INTO email_tokens VALUES(?,?,?,?,?,?,?,NULL,0,?)',(digest,role,item['id'],item['email'],purpose,stamp,expiry,mail['id']))
    return mail['id']


def request_verification(db,role,account_id,address,origin):
    db.execute('BEGIN IMMEDIATE')
    item=account(db,role,account_id)
    if not item:raise ValueError('Аккаунт недоступен.')
    access.ensure_active(db,role,account_id)
    if verified(db,role,account_id,item['email']):return {'ok':True,'message':'Адрес уже подтверждён.'}
    allowed=throttle(db,role,'verify',address,item['email'])
    recent=db.execute("SELECT 1 FROM email_tokens WHERE role=? AND account_id=? AND purpose='verify' AND created_at>?",(role,account_id,time.time()-60)).fetchone()
    if allowed and not recent:issue(db,role,item,'verify',origin)
    return {'ok':True,'message':'Письмо подтверждения поставлено в очередь. Повторный запрос доступен через минуту. Используйте ссылку из последнего письма.'}


def request_reset(db,role,email,address,origin):
    original=email.strip().lower() if isinstance(email,str) else ''
    email=normalize_email(email)
    settings(origin)
    db.execute('BEGIN IMMEDIATE')
    if throttle(db,role,'reset',address,email):
        item=db.execute(f'SELECT id,email FROM {TABLES[role]} WHERE email IN (?,?)',(email,original)).fetchone()
        if item and access.active(db,role,item['id']):issue(db,role,item,'reset',origin)
    return {'ok':True,'message':RESET_REPLY}


def consume(db,role,purpose,token,password=None):
    if not isinstance(token,str) or not re.fullmatch('[A-Za-z0-9_-]{43}',token):raise ValueError(INVALID_LINK)
    if purpose=='reset' and (not isinstance(password,str) or not 8<=len(password)<=200):
        raise ValueError('Пароль должен содержать от 8 до 200 символов.')
    db.execute('BEGIN IMMEDIATE')
    row=db.execute('SELECT * FROM email_tokens WHERE token_hash=? AND role=? AND purpose=?',(access.digest(token),role,purpose)).fetchone()
    if not row or row['used_at'] is not None or row['revoked'] or row['expires_at']<=time.time():raise ValueError(INVALID_LINK)
    item=account(db,role,row['account_id'])
    if not item or item['email']!=row['email']:raise ValueError(INVALID_LINK)
    access.ensure_active(db,role,item['id'])
    stamp=time.time()
    if purpose=='reset':
        import accounts
        accounts.invalidate_changes(db,role,item['id'])
        salt=secrets.token_hex(16)
        db.execute(f'UPDATE {TABLES[role]} SET salt=?,hash=? WHERE id=?',(salt,access.password_hash(password,salt),item['id']))
        table,column=access.TABLES[role]
        db.execute(f'DELETE FROM {table} WHERE {column}=?',(item['id'],))
    db.execute('UPDATE email_tokens SET used_at=? WHERE token_hash=?',(stamp,row['token_hash']))
    invalidate(db,role,item['id'])
    db.execute('INSERT INTO email_status VALUES(?,?,?,?) ON CONFLICT(role,account_id) DO UPDATE SET email=excluded.email,verified_at=excluded.verified_at',(role,item['id'],item['email'],stamp))
    # Do not retain a live link in a queue entry after consumption.
    db.execute("UPDATE mail_outbox SET recipient='',subject='',body='',status=CASE WHEN status IN ('pending','unknown') THEN 'cancelled' ELSE status END WHERE id=?",(row['mail_id'],))
    target=item['id']
    if role=='psychologist':
        profile=db.execute("SELECT id FROM profiles WHERE json_extract(data,'$.owner_id')=?",(item['id'],)).fetchone()
        target=profile['id'] if profile else item['id']
    db.execute('INSERT INTO admin_events(created_at,category,kind,actor,target_kind,target_id,result,details) VALUES(?,?,?,?,?,?,?,?)',
        (time_iso(stamp),'platform','email_verified' if purpose=='verify' else 'password_reset',role,'profile' if role=='psychologist' else 'client',target,'success','{}'))
    return {'ok':True,'message':'Почта подтверждена.' if purpose=='verify' else 'Пароль изменён. Войдите с новым паролем.'}


def time_iso(stamp):
    from datetime import datetime,timezone
    return datetime.fromtimestamp(stamp,timezone.utc).isoformat(timespec='microseconds')
