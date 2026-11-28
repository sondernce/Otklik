"""Temporary registration challenges: accounts exist only after email-code confirmation."""
from http.cookies import SimpleCookie,CookieError
import hmac
import json
import re
import os
import secrets
import time
import access
import accounts
import client_profiles
import email_access
import mail_queue

CODE_SECONDS=600
PENDING_SECONDS=86400
MAX_ATTEMPTS=5


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS pending_registrations (
        id TEXT PRIMARY KEY,role TEXT NOT NULL CHECK(role IN ('client','psychologist')),
        email TEXT NOT NULL,payload TEXT NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,
        code_hash TEXT NOT NULL,code_expires_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,expires_at REAL NOT NULL,resend_at REAL NOT NULL,
        mail_id TEXT REFERENCES mail_outbox(id),completed_at REAL,account_id TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS pending_registration_expiry ON pending_registrations(expires_at)')


def token(header,role):
    jar=SimpleCookie()
    try:jar.load(header or '')
    except CookieError:return ''
    item=jar.get('otklik_registration_'+role)
    value=item.value if item else ''
    return value if re.fullmatch(r'[A-Za-z0-9_-]{32,100}',value) else ''


def cookie(role,value):
    return f'otklik_registration_{role}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={PENDING_SECONDS}'


def clear_mail(db,ident):
    if ident:db.execute("UPDATE mail_outbox SET recipient='',subject='',body='',status=CASE WHEN status IN ('pending','sending','unknown') THEN 'cancelled' ELSE status END,lease=NULL,lease_until=NULL WHERE id=?",(ident,))


def cleanup(db):
    for row in db.execute('SELECT mail_id FROM pending_registrations WHERE expires_at<=?',(time.time(),)).fetchall():clear_mail(db,row[0])
    db.execute('DELETE FROM pending_registrations WHERE expires_at<=?',(time.time(),))


def public(row):
    return {'transport':os.environ.get('OTKLIK_MAIL_TRANSPORT','local'),'verification_required':True,'role':row['role'],'email':row['email'],
            'resend_at':row['resend_at'],'code_expires_at':row['code_expires_at'],
            'completed':row['completed_at'] is not None}


def find(db,role,key):
    if not key:return None
    return db.execute('SELECT * FROM pending_registrations WHERE id=? AND role=? AND expires_at>?',(access.digest(role+':'+key),role,time.time())).fetchone()


def issue(db,row,origin):
    transport,_=email_access.settings(origin)
    code=f'{secrets.randbelow(1000000):06d}'
    # A resend never deliberately reissues the preceding code.
    while hmac.compare_digest(row['code_hash'],access.digest(row['id']+':'+code)):code=f'{secrets.randbelow(1000000):06d}'
    stamp=time.time();clear_mail(db,row['mail_id'])
    mail=mail_queue.enqueue(db,event_key='registration:'+secrets.token_hex(20),kind='verification',recipient=row['email'],subject='Код регистрации в Отклике',
        body='Код подтверждения: '+code+'\n\nВведите код на странице регистрации «Отклика». Он действует 10 минут. Никому не сообщайте код.\n\nЕсли вы не регистрировались, ничего не делайте: аккаунт не будет создан.',transport=transport,expires_at=stamp+CODE_SECONDS)
    db.execute('UPDATE pending_registrations SET code_hash=?,code_expires_at=?,attempts=0,resend_at=?,mail_id=? WHERE id=?',(access.digest(row['id']+':'+code),stamp+CODE_SECONDS,stamp+60,mail['id'],row['id']))


def start(db,role,data,address,origin):
    email=email_access.normalize_email(data.get('email'));password=data.get('password')
    if not isinstance(password,str) or not 8<=len(password)<=200:raise ValueError('Пароль должен содержать от 8 до 200 символов.')
    payload={}
    if role=='client':
        name=data.get('name');age=data.get('age')
        if not isinstance(name,str) or not 2<=len(name.strip())<=100:raise ValueError('Имя должно содержать от 2 до 100 символов.')
        if age is not None:client_profiles.validate_age(age)
        payload={'name':name.strip(),'age':age}
    key=data.get('request_key') or secrets.token_urlsafe(32)
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}',key):raise ValueError('Обновите форму регистрации и попробуйте ещё раз.')
    db.execute('BEGIN IMMEDIATE');cleanup(db)
    row=find(db,role,key)
    if row:
        if row['completed_at'] is not None:return 409,{'error':'Аккаунт уже создан. Войдите с указанным паролем.','completed':True},None
        if row['email']!=email or json.loads(row['payload'])!=payload or not hmac.compare_digest(row['password_hash'],access.password_hash(password,row['salt'])):
            return 409,{'error':'Данные регистрации изменились. Вернитесь к форме и отправьте её ещё раз.'},None
        return 200,public(row),cookie(role,key)
    if accounts.address_in_use(db,role,email):return 409,{'error':'Эта почта уже зарегистрирована. Войдите в кабинет.','field':'email'},None
    if not email_access.throttle(db,role,'registration',address,email):return 429,{'error':'Письмо уже запрошено. Подождите минуту перед повторной регистрацией.'},None
    salt=secrets.token_hex(16);stamp=time.time();ident=access.digest(role+':'+key)
    db.execute('INSERT INTO pending_registrations VALUES(?,?,?,?,?,?,?,?,0,?,?,0,NULL,NULL,NULL)',(ident,role,email,json.dumps(payload,ensure_ascii=False),salt,access.password_hash(password,salt),'',0,stamp,stamp+PENDING_SECONDS))
    issue(db,find(db,role,key),origin)
    return 200,public(find(db,role,key)),cookie(role,key)


def status(db,role,key):
    cleanup(db);row=find(db,role,key)
    if not row:return 401,{'error':'Регистрация не найдена или истекла. Заполните форму ещё раз.'},None
    return 200,public(row),None


def update(db,role,key,address,origin,email=None):
    db.execute('BEGIN IMMEDIATE');cleanup(db);row=find(db,role,key)
    if not row:return 401,{'error':'Регистрация не найдена. Заполните форму ещё раз.'},None
    if row['completed_at'] is not None:return 409,{'error':'Аккаунт уже создан. Войдите в кабинет.'},None
    target=email_access.normalize_email(email) if email is not None else row['email']
    changed=target!=row['email']
    if not changed and time.time()<row['resend_at']:return 429,{'error':'Повторный код можно запросить через минуту.'},None
    if accounts.address_in_use(db,role,target):return 409,{'error':'Эта почта уже зарегистрирована. Укажите другой адрес или войдите.','field':'email'},None
    if not email_access.throttle(db,role,'registration',address,target):return 429,{'error':'Слишком много запросов кода. Попробуйте позже.'},None
    db.execute('UPDATE pending_registrations SET email=? WHERE id=?',(target,row['id']))
    issue(db,find(db,role,key),origin)
    return 200,public(find(db,role,key)),None


def confirm(db,role,key,code,previous=''):
    db.execute('BEGIN IMMEDIATE');cleanup(db);row=find(db,role,key)
    if not row:return 401,{'error':'Регистрация не найдена. Заполните форму ещё раз.'},None
    if row['completed_at'] is not None:return 409,{'error':'Аккаунт уже создан. Войдите с указанным паролем.','completed':True},None
    if row['attempts']>=MAX_ATTEMPTS:return 429,{'error':'Попытки закончились. Запросите новый код.','field':'code'},None
    if row['code_expires_at']<=time.time():return 400,{'error':'Срок действия кода истёк. Запросите новый.','field':'code'},None
    if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code,flags=re.ASCII):return 400,{'error':'Введите шестизначный код из письма.','field':'code'},None
    if not hmac.compare_digest(row['code_hash'],access.digest(row['id']+':'+code)):
        db.execute('UPDATE pending_registrations SET attempts=attempts+1 WHERE id=?',(row['id'],))
        return 400,{'error':'Неверный код. Проверьте последнее письмо.','field':'code'},None
    if accounts.address_in_use(db,role,row['email']):return 409,{'error':'Эта почта уже зарегистрирована. Войдите с паролем или измените адрес.','field':'email'},None
    ident=secrets.token_hex(12);payload=json.loads(row['payload'])
    if role=='client':
        db.execute('INSERT INTO clients VALUES(?,?,?,?,?)',(ident,row['email'],payload['name'],row['salt'],row['password_hash']))
        if payload.get('age') is not None:db.execute('INSERT INTO client_details VALUES(?,?)',(ident,payload['age']))
    else:
        import server
        db.execute('INSERT INTO psychologists VALUES(?,?,?,?)',(ident,row['email'],row['salt'],row['password_hash']))
        p=dict(id=secrets.token_hex(12),owner_id=ident,email=row['email'],contact='',status='draft',name='',gender='female',price=3000,experience=0,style='gentle',approach='',bio='',education='',topics=[],photo='',slots=[],hidden=False,deleted=False,revision=0,history=[])
        server.event(p,'Регистрация','Психолог');server.save_record(db,p)
    db.execute('INSERT INTO email_status VALUES(?,?,?,?)',(role,ident,row['email'],time.time()))
    auth=access.issue(db,role,ident,previous)
    clear_mail(db,row['mail_id'])
    db.execute("UPDATE pending_registrations SET completed_at=?,account_id=?,payload='{}',salt='',password_hash='',code_hash='',expires_at=? WHERE id=?",(time.time(),ident,time.time()+CODE_SECONDS,row['id']))
    return 200,{'ok':True,'registered':True},access.cookie(role,auth)
