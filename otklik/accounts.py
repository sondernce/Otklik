"""Account access, verified email changes and explicit profile erasure."""
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import access
import booking
import email_access
import mail_queue

TABLES=email_access.TABLES


def install(db):
    db.execute("""CREATE TABLE IF NOT EXISTS account_states (
        role TEXT NOT NULL,account_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','blocked','deleted')),revision INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT,PRIMARY KEY(role,account_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS email_changes (
        token_hash TEXT PRIMARY KEY,role TEXT NOT NULL,account_id TEXT NOT NULL,
        old_email TEXT NOT NULL,new_email TEXT NOT NULL,created_at REAL NOT NULL,
        expires_at REAL NOT NULL,used_at REAL,revoked INTEGER NOT NULL DEFAULT 0,
        request_key TEXT NOT NULL,mail_id TEXT NOT NULL REFERENCES mail_outbox(id),
        UNIQUE(role,account_id,request_key))""")


def state(db,role,ident):
    row=db.execute('SELECT status,revision FROM account_states WHERE role=? AND account_id=?',(role,ident)).fetchone()
    return dict(row) if row else {'status':'active','revision':0}


def audit(db,role,ident,kind,actor,details=None):
    db.execute('INSERT INTO admin_events(created_at,category,kind,actor,target_kind,target_id,result,details) VALUES(?,?,?,?,?,?,?,?)',
        (booking.now(),'admin' if actor=='admin' else 'platform',kind,actor,'account',role+':'+ident,'success',json.dumps(details or {})))


def password(db,role,ident,value):
    if not isinstance(value,str) or not 8<=len(value)<=200:raise ValueError('Введите текущий пароль.')
    item=db.execute(f'SELECT * FROM {TABLES[role]} WHERE id=?',(ident,)).fetchone()
    if not item or not hmac.compare_digest(item['hash'],access.password_hash(value,item['salt'])):
        raise ValueError('Текущий пароль неверен.')
    return item


def invalidate_changes(db,role,ident):
    db.execute("UPDATE mail_outbox SET status=CASE WHEN status IN ('pending','unknown') THEN 'cancelled' ELSE status END,recipient='',subject='',body='' WHERE id IN (SELECT mail_id FROM email_changes WHERE role=? AND account_id=? AND used_at IS NULL)",(role,ident))
    db.execute('UPDATE email_changes SET revoked=1 WHERE role=? AND account_id=? AND used_at IS NULL',(role,ident))


def revoke(db,role,ident):
    table,column=access.TABLES[role]
    db.execute(f'DELETE FROM {table} WHERE {column}=?',(ident,))
    email_access.invalidate(db,role,ident)
    invalidate_changes(db,role,ident)


def set_status(db,data):
    role,ident,target=data.get('role'),data.get('account_id'),data.get('status')
    if role not in TABLES or target not in ('active','blocked') or not isinstance(ident,str):
        raise ValueError('Выберите аккаунт и действие.')
    db.execute('BEGIN IMMEDIATE')
    if not email_access.account(db,role,ident):raise booking.Conflict('Аккаунт не найден.')
    current=state(db,role,ident)
    if current['status']=='deleted':raise booking.Conflict('Удалённый аккаунт нельзя восстановить.')
    if current['status']==target:return {'account':current}
    if type(data.get('revision')) is not int or data['revision']!=current['revision']:
        raise booking.Conflict('Состояние аккаунта изменилось. Обновите список.')
    db.execute('INSERT INTO account_states VALUES(?,?,?,?,?) ON CONFLICT(role,account_id) DO UPDATE SET status=excluded.status,revision=excluded.revision,updated_at=excluded.updated_at',
        (role,ident,target,current['revision']+1,booking.now()))
    if target=='blocked':revoke(db,role,ident)
    audit(db,role,ident,'account_blocked' if target=='blocked' else 'account_unblocked','admin')
    return {'account':state(db,role,ident)}


def listing(db,params):
    role=params.get('role','all');status=params.get('status','all');search=params.get('search','')
    if role not in ('all','client','psychologist') or status not in ('all','active','blocked','deleted') or not isinstance(search,str) or len(search)>200:
        raise ValueError('Проверьте фильтры аккаунтов.')
    after=params.get('after','');conditions=[];args=[]
    if after:
        parts=after.split('|')
        if len(parts)!=2 or parts[0] not in TABLES or not parts[1].isalnum() or len(parts[1])>50:raise ValueError('Обновите список аккаунтов.')
        conditions.append('(role>? OR (role=? AND id>?))');args.extend((parts[0],parts[0],parts[1]))
    ident=params.get('account_id')
    if ident is not None:
        if not isinstance(ident,str) or not ident.isalnum() or len(ident)>50:raise ValueError('Выберите аккаунт.')
        conditions.append('id=?');args.append(ident)
    if role!='all':conditions.append('role=?');args.append(role)
    if status!='all':conditions.append('status=?');args.append(status)
    if search:conditions.append('(name LIKE ? OR email LIKE ?)');args.extend(('%'+search+'%',)*2)
    query="""SELECT * FROM (
        SELECT 'client' role,c.id,c.name,c.email,COALESCE(s.status,'active') status,COALESCE(s.revision,0) revision
        FROM clients c LEFT JOIN account_states s ON s.role='client' AND s.account_id=c.id
        UNION ALL
        SELECT 'psychologist',u.id,COALESCE(json_extract(p.data,'$.name'),''),u.email,COALESCE(s.status,'active'),COALESCE(s.revision,0)
        FROM psychologists u LEFT JOIN profiles p ON json_extract(p.data,'$.owner_id')=u.id
        LEFT JOIN account_states s ON s.role='psychologist' AND s.account_id=u.id)"""
    rows=db.execute(query+(' WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY role,id LIMIT 101',args).fetchall()
    items=[dict(row) for row in rows[:100]]
    return {'accounts':items,'next_after':items[-1]['role']+'|'+items[-1]['id'] if len(rows)>100 else None}


def address_in_use(db,role,email):
    if db.execute(f'SELECT 1 FROM {TABLES[role]} WHERE email=? COLLATE NOCASE',(email,)).fetchone():return True
    # Older accounts may have stored the domain in Unicode instead of IDNA.
    for row in db.execute(f'SELECT email FROM {TABLES[role]}'):
        if row[0].isascii():continue
        try:
            if email_access.normalize_email(row[0])==email:return True
        except ValueError:continue
    return False


def request_change(db,role,ident,data,address,origin):
    new_email=email_access.normalize_email(data.get('email'));key=data.get('request_key')
    if not isinstance(key,str) or not 16<=len(key)<=100:raise ValueError('Обновите форму смены почты.')
    transport,base=email_access.settings(origin)
    db.execute('BEGIN IMMEDIATE');access.ensure_active(db,role,ident)
    item=password(db,role,ident,data.get('password'))
    if new_email==email_access.normalize_email(item['email']):raise ValueError('Укажите другой адрес почты.')
    previous=db.execute('SELECT new_email FROM email_changes WHERE role=? AND account_id=? AND request_key=?',(role,ident,key)).fetchone()
    if previous:
        if previous[0]!=new_email:raise booking.Conflict('Запрос уже использован для другого адреса.')
        return {'ok':True,'message':'Проверьте письмо на новом адресе. До подтверждения действует прежняя почта.'}
    # Do not disclose whether a different user owns the new address.
    allowed=email_access.throttle(db,role,'email-change',address,new_email)
    recent=db.execute('SELECT 1 FROM email_changes WHERE role=? AND account_id=? AND created_at>?',(role,ident,time.time()-60)).fetchone()
    if allowed and not recent and not address_in_use(db,role,new_email):
        invalidate_changes(db,role,ident)
        secret=secrets.token_urlsafe(32);digest=access.digest(secret);stamp=time.time()
        link=base+'/account-access#'+urlencode({'role':role,'purpose':'change','token':secret})
        mail=mail_queue.enqueue(db,event_key='email-change:'+digest,kind='verification',recipient=new_email,subject='Смена почты в Отклике',body='Чтобы заменить адрес почты, откройте ссылку и подтвердите действие. Ссылка действует 30 минут. После смены потребуется войти заново.\n\n'+link,transport=transport,expires_at=stamp+1800)
        db.execute('INSERT INTO email_changes VALUES(?,?,?,?,?,?,?,NULL,0,?,?)',(digest,role,ident,item['email'],new_email,stamp,stamp+1800,key,mail['id']))
    return {'ok':True,'message':'Если адрес доступен, письмо отправлено в очередь. Проверьте новый ящик. До подтверждения действует прежняя почта; повторный запрос доступен через минуту.'}


def confirm_change(db,role,token):
    import re
    if not isinstance(token,str) or not re.fullmatch('[A-Za-z0-9_-]{43}',token):raise ValueError(email_access.INVALID_LINK)
    db.execute('BEGIN IMMEDIATE')
    row=db.execute('SELECT * FROM email_changes WHERE token_hash=? AND role=?',(access.digest(token),role)).fetchone()
    if not row or row['revoked'] or row['used_at'] is not None or row['expires_at']<=time.time():raise ValueError(email_access.INVALID_LINK)
    access.ensure_active(db,role,row['account_id'])
    item=email_access.account(db,role,row['account_id'])
    if not item or item['email']!=row['old_email'] or address_in_use(db,role,row['new_email']):
        raise booking.Conflict('Адрес уже недоступен или аккаунт изменился. Запросите смену заново.')
    db.execute(f'UPDATE {TABLES[role]} SET email=? WHERE id=?',(row['new_email'],item['id']))
    if role=='psychologist':
        db.execute("UPDATE profiles SET data=json_set(data,'$.email',?,'$.revision',COALESCE(json_extract(data,'$.revision'),0)+1) WHERE json_extract(data,'$.owner_id')=?",(row['new_email'],item['id']))
    db.execute('UPDATE email_changes SET used_at=? WHERE token_hash=?',(time.time(),row['token_hash']))
    revoke(db,role,item['id'])
    db.execute("UPDATE mail_outbox SET recipient='',subject='',body='',status=CASE WHEN status IN ('pending','unknown') THEN 'cancelled' ELSE status END WHERE id=?",(row['mail_id'],))
    db.execute('INSERT INTO email_status VALUES(?,?,?,?) ON CONFLICT(role,account_id) DO UPDATE SET email=excluded.email,verified_at=excluded.verified_at',(role,item['id'],row['new_email'],time.time()))
    audit(db,role,item['id'],'email_changed',role)
    return {'ok':True,'message':'Адрес изменён. Войдите с новой почтой и прежним паролем.'}


def erase(db,role,ident,data):
    if data.get('confirmation')!='УДАЛИТЬ':raise ValueError('Для подтверждения введите УДАЛИТЬ.')
    db.execute('BEGIN IMMEDIATE');access.ensure_active(db,role,ident)
    password(db,role,ident,data.get('password'));booking.expire(db)
    condition='a.client_id=?' if role=='client' else "json_extract(p.data,'$.owner_id')=?"
    if db.execute('SELECT 1 FROM appointments a JOIN profiles p ON p.id=a.profile_id WHERE '+condition+" AND a.cancelled=0 AND a.status IN ('pending','confirmed') AND a.end_at>?",(ident,booking.now())).fetchone():
        raise booking.Conflict('Сначала отмените будущие встречи. Текущая встреча должна завершиться.')
    revoke(db,role,ident)
    # Conversation bodies and operational history are retained by the agreed policy.
    replacement='Удалённый пользователь' if role=='client' else 'Удалённый психолог'
    salt=secrets.token_hex(16)
    db.execute(f'UPDATE {TABLES[role]} SET email=?,salt=?,hash=? WHERE id=?',(ident+'@deleted.invalid',salt,access.password_hash(secrets.token_urlsafe(32),salt),ident))
    if role=='client':
        db.execute('UPDATE clients SET name=? WHERE id=?',(replacement,ident))
        db.execute('DELETE FROM client_details WHERE client_id=?',(ident,))
    else:
        rows=db.execute("SELECT id,data FROM profiles WHERE json_extract(data,'$.owner_id')=?",(ident,)).fetchall()
        for row in rows:
            profile=json.loads(row['data'])
            for key in ('bio','education','approach','contact','photo','email','review_note'):profile[key]=''
            profile.update(name=replacement,initials='У',topics=[],slots=[],hidden=True,deleted=True,published=False,revision=profile.get('revision',0)+1)
            profile['history']=[{k:v for k,v in item.items() if k in ('at','action','actor')} for item in profile.get('history',[])]
            db.execute('UPDATE profiles SET data=? WHERE id=?',(json.dumps(profile,ensure_ascii=False),row['id']))
            db.execute('DELETE FROM profile_publications WHERE profile_id=?',(row['id'],))
            db.execute('UPDATE slots SET active=0 WHERE profile_id=?',(row['id'],))
            db.execute('UPDATE appointments SET psychologist_name=? WHERE profile_id=?',(replacement,row['id']))
    db.execute('DELETE FROM email_status WHERE role=? AND account_id=?',(role,ident))
    for table in ('email_tokens','email_changes'):
        db.execute("UPDATE mail_outbox SET recipient='',subject='',body='',status=CASE WHEN status IN ('pending','unknown') THEN 'cancelled' ELSE status END WHERE id IN (SELECT mail_id FROM "+table+' WHERE role=? AND account_id=?)',(role,ident))
        db.execute('DELETE FROM '+table+' WHERE role=? AND account_id=?',(role,ident))
    current=state(db,role,ident)
    db.execute("INSERT INTO account_states VALUES(?,?,'deleted',?,?) ON CONFLICT(role,account_id) DO UPDATE SET status='deleted',revision=excluded.revision,updated_at=excluded.updated_at",(role,ident,current['revision']+1,booking.now()))
    audit(db,role,ident,'account_deleted',role)
    return {'ok':True,'message':'Аккаунт удалён. Переписка и служебная история сохранены.'}
