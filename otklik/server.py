"""Local-only Otklik profile administration. Python standard library, SQLite."""
import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import booking
import communications
import notifications
import client_profiles
import video_links
import web_assets

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('OTKLIK_DB', ROOT / '.local' / 'otklik.sqlite3'))
PORT = int(os.environ.get('OTKLIK_PORT', '4173'))
SESSIONS = {}
PSY_SESSIONS = {}
TOPICS = {'anxiety', 'relationships', 'self', 'burnout'}


def connection():
    return booking.connect(DB)


def has_admin():
    with connection() as db:
        return db.execute("SELECT 1 FROM settings WHERE key='password'").fetchone() is not None


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def validate(data):
    def text(key, minimum, maximum):
        value = data.get(key)
        if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
            raise ValueError(f'Проверьте поле «{key}» (от {minimum} до {maximum} символов).')
        return value.strip()
    p = {k: text(k, a, b) for k, a, b in [('name', 2, 100), ('bio', 20, 1500), ('approach', 3, 150), ('education', 5, 2000)]}
    if data.get('gender') not in {'female', 'male'} or data.get('style') not in {'gentle', 'structured'}:
        raise ValueError('Выберите пол и формат разговора.')
    p.update(gender=data['gender'], style=data['style'])
    price = data.get('price')
    if type(price) is not int or not 500 <= price <= 30000:
        raise ValueError('Цена должна быть целым числом от 500 до 30 000 ₽.')
    p['price'] = price
    experience = data.get('experience')
    if type(experience) is not int or not 0 <= experience <= 70:
        raise ValueError('Опыт должен быть от 0 до 70 лет.')
    p['experience'] = experience
    topics = data.get('topics')
    if not isinstance(topics, list) or not topics or any(t not in TOPICS for t in topics):
        raise ValueError('Выберите хотя бы одну тему работы.')
    p['topics'] = sorted(set(topics))
    if type(data.get('published')) is not bool:
        raise ValueError('Неверный статус публикации.')
    slots = data.get('slots', [])
    if not isinstance(slots, list) or len(slots) > 100:
        raise ValueError('Можно добавить до 100 слотов.')
    normalized = []
    for slot in slots:
        if not isinstance(slot, str):
            raise ValueError('Проверьте дату и время.')
        try:
            dt = datetime.fromisoformat(slot)
            if not dt.tzinfo or dt <= datetime.now(timezone.utc):
                raise ValueError()
            if dt.second or dt.microsecond:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Все слоты должны быть в будущем и содержать часовой пояс.')
        normalized.append(dt.astimezone(timezone.utc))
    dates = sorted(set(normalized))
    if any((b - a).total_seconds() < 3000 for a, b in zip(dates, dates[1:])):
        raise ValueError('Слоты не должны пересекаться: встреча длится 50 минут.')
    p['slots'] = [d.isoformat() for d in dates]
    if data['published'] and not dates:
        raise ValueError('Для публикации добавьте хотя бы один будущий слот.')
    p['published'] = data['published']
    photo = data.get('photo', '')
    if not isinstance(photo, str):
        raise ValueError('Неверное фото.')
    if photo:
        try:
            prefix, encoded = photo.split(',', 1)
            raw = base64.b64decode(encoded, validate=True)
            valid = ((prefix == 'data:image/png;base64' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or
                     (prefix == 'data:image/jpeg;base64' and raw.startswith(b'\xff\xd8\xff')) or
                     (prefix == 'data:image/webp;base64' and raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'))
            if not valid or len(raw) > 2 * 1024 * 1024:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Нужно фото PNG, JPEG или WebP до 2 МБ.')
    p['photo'] = photo
    p['id'] = data.get('id') or secrets.token_hex(12)
    if not isinstance(p['id'], str) or len(p['id']) > 50 or not p['id'].isalnum():
        raise ValueError('Неверный идентификатор.')
    p['initials'] = p['name'][0].upper()
    return p


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def future_slots(p, db=None):
    if db is None:
        with connection() as db:
            return booking.available_slots(db, p['id'])
    return booking.available_slots(db, p['id'])


def decorate(p, db=None):
    if db is None:
        with connection() as db:
            return decorate(p, db)
    p = dict(p)
    p.setdefault('status', 'pending')
    p.setdefault('hidden', False)
    p.setdefault('deleted', False)
    p.setdefault('revision', 0)
    p.setdefault('history', [])
    p['has_publication'] = booking.publication(db, p['id']) is not None
    p['published'] = p['has_publication'] and not p['hidden'] and not p['deleted'] and bool(future_slots(p, db))
    setting = db.execute('SELECT auto_approve FROM booking_settings WHERE profile_id=?', (p['id'],)).fetchone()
    p['auto_approve'] = bool(setting and setting[0])
    return p


def event(p, action, actor, note=''):
    p.setdefault('history', []).append(dict(at=now_iso(), action=action, actor=actor, note=note))
    p['revision'] = p.get('revision', 0) + 1
    p['updated_at'] = now_iso()
    return p


def save_record(db, p):
    db.execute('INSERT INTO profiles VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (p['id'], json.dumps(p, ensure_ascii=False)))
    booking.sync_slots(db, p['id'], p.get('slots', []))


def owned_profile(db, owner):
    for row in db.execute('SELECT data FROM profiles'):
        p = json.loads(row[0])
        if p.get('owner_id') == owner:
            return decorate(p, db)
    return None


class Handler(BaseHTTPRequestHandler):
    attempts = {}

    def log_message(self, *_):
        pass

    def send(self, code, body, cookie=None, content_type='application/json; charset=utf-8'):
        raw = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, (dict, list)) else body
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        cookies = dict(x.strip().split('=', 1) for x in self.headers.get('Cookie', '').split(';') if '=' in x)
        session = SESSIONS.get(cookies.get('otklik_admin'))
        return session and session > time.time()

    def psychologist(self):
        cookies = dict(x.strip().split('=', 1) for x in self.headers.get('Cookie', '').split(';') if '=' in x)
        session = PSY_SESSIONS.get(cookies.get('otklik_psy'))
        return session['user'] if session and session['expires'] > time.time() else None

    def client(self):
        cookies = dict(x.strip().split('=', 1) for x in self.headers.get('Cookie', '').split(';') if '=' in x)
        token = cookies.get('otklik_client', '')
        if not token:
            return None
        with connection() as db:
            row = db.execute('SELECT c.id,c.name,c.email,d.age FROM clients c JOIN client_sessions s ON s.client_id=c.id LEFT JOIN client_details d ON d.client_id=c.id '
                             'WHERE s.token_hash=? AND s.expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def do_GET(self):
        try:
            return self.get_request()
        except communications.RateLimit as e:
            return self.send(429, {'error': str(e)})
        except booking.Conflict as e:
            return self.send(409, {'error': str(e)})
        except (ValueError, TypeError) as e:
            return self.send(400, {'error': str(e) or 'Проверьте запрос.'})
        except sqlite3.OperationalError:
            logging.exception('Database error during GET')
            return self.send(503, {'error': 'Сервис занят. Попробуйте ещё раз через несколько секунд.'})

    def get_request(self):
        path = urlsplit(self.path).path
        if self.is_communication_path(path):
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            return self.communication_request(path, {key: values[0] for key, values in query.items()})
        if path in {'/api/client/me', '/api/client/appointments'}:
            client = self.client()
            if not client:
                return self.send(401, {'error': 'Войдите в кабинет клиента.'})
            if path.endswith('/me'):
                return self.send(200, {'client': client})
            with connection() as db:
                items = booking.listings(db, 'client', client['id'])
            return self.send(200, {'appointments': items})
        if path in {'/api/psychologist/appointments', '/api/admin/appointments'}:
            owner = self.psychologist() if path.startswith('/api/psychologist') else None
            if not owner and not (path.startswith('/api/admin') and self.authorized()):
                return self.send(401, {'error': 'Войдите в соответствующий кабинет.'})
            with connection() as db:
                items = booking.listings(db, 'psychologist' if owner else 'admin', owner)
            return self.send(200, {'appointments': items})
        if path == '/api/psychologist/me':
            owner = self.psychologist()
            if not owner:
                return self.send(401, {'error': 'Войдите в кабинет психолога.'})
            with connection() as db:
                user = db.execute('SELECT email FROM psychologists WHERE id=?', (owner,)).fetchone()
                p = owned_profile(db, owner)
            return self.send(200, {'email': user[0], 'profile': p})
        if path == '/api/status':
            return self.send(200, {'setup': not has_admin(), 'authenticated': bool(self.authorized())})
        if path in {'/api/profiles', '/api/admin/profiles'}:
            private = path.startswith('/api/admin')
            if private and not self.authorized():
                return self.send(401, {'error': 'Войдите в кабинет.'})
            with connection() as db:
                records = [decorate(json.loads(row[0]), db) for row in db.execute('SELECT data FROM profiles ORDER BY rowid DESC')]
                if not private:
                    records = [dict(booking.publication(db, p['id']), slots=future_slots(p, db), auto_approve=p['auto_approve']) for p in records if p['published']]
            return self.send(200, {'profiles': records})
        file = web_assets.resolve(path)
        if file is None:
            return self.send(404, {'error': 'Страница не найдена.'})
        return self.send(200, file.read_bytes(), content_type=web_assets.content_type(file))

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin not in {f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'}:
            return self.send(403, {'error': 'Запрос разрешён только из локального приложения.'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': 'Ожидается JSON.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 3000000:
                return self.send(413, {'error': 'Слишком большой запрос.'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Неверный запрос.')
            path = urlsplit(self.path).path
            if self.is_communication_path(path):
                return self.communication_request(path, data, write=True)
            if path.startswith('/api/client/'):
                return self.client_request(path, data)
            if path.startswith('/api/psychologist/appointments/') and path != '/api/psychologist/appointments/decision':
                owner = self.psychologist()
                if not owner:
                    return self.send(401, {'error': 'Войдите в кабинет психолога.'})
                return self.appointment_action('psychologist', owner, path, data)
            if path in {'/api/psychologist/booking-settings', '/api/psychologist/appointments/decision'}:
                owner = self.psychologist()
                if not owner:
                    return self.send(401, {'error': 'Войдите в кабинет психолога.'})
                with connection() as db:
                    if path.endswith('/decision'):
                        appointment = booking.decide(db, owner, data.get('id'), data.get('action'))
                        response = {'appointment': booking.public_appointment(appointment)}
                    else:
                        if type(data.get('auto_approve')) is not bool:
                            raise ValueError('Выберите способ подтверждения записи.')
                        db.execute('BEGIN IMMEDIATE')
                        p = owned_profile(db, owner)
                        if not p or p['deleted']:
                            return self.send(403, {'error': 'Настройка недоступна.'})
                        db.execute('INSERT INTO booking_settings VALUES(?,?) ON CONFLICT(profile_id) '
                                   'DO UPDATE SET auto_approve=excluded.auto_approve', (p['id'], int(data['auto_approve'])))
                        response = {'auto_approve': data['auto_approve']}
                return self.send(200, response)
            if path in {'/api/setup', '/api/login'}:
                recent = [x for x in self.attempts.get('local', []) if x > time.time() - 60]
                if len(recent) >= 8:
                    return self.send(429, {'error': 'Слишком много попыток. Подождите минуту.'})
                self.attempts['local'] = recent + [time.time()]
                password = data.get('password', '')
                if not isinstance(password, str) or not 8 <= len(password) <= 200:
                    raise ValueError('Пароль должен содержать от 8 до 200 символов.')
                with connection() as db:
                    if path == '/api/setup':
                        salt = secrets.token_hex(16)
                        try:
                            db.execute('INSERT INTO settings VALUES (?, ?)', ('password', json.dumps({'salt': salt, 'hash': password_hash(password, salt)})))
                        except sqlite3.IntegrityError:
                            return self.send(409, {'error': 'Кабинет уже настроен. Войдите с паролем.'})
                    else:
                        row = db.execute("SELECT value FROM settings WHERE key='password'").fetchone()
                        if not row:
                            return self.send(409, {'error': 'Сначала настройте кабинет.'})
                        saved = json.loads(row[0])
                        if not hmac.compare_digest(saved['hash'], password_hash(password, saved['salt'])):
                            return self.send(401, {'error': 'Неверный пароль.'})
                token = secrets.token_urlsafe(32)
                SESSIONS[token] = time.time() + 28800
                return self.send(200, {'ok': True}, f'otklik_admin={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
            if path in {'/api/psychologist/register', '/api/psychologist/login'}:
                recent = [x for x in self.attempts.get('psy', []) if x > time.time() - 60]
                if len(recent) >= 12:
                    return self.send(429, {'error': 'Слишком много попыток. Подождите минуту.'})
                self.attempts['psy'] = recent + [time.time()]
                email, password = data.get('email', ''), data.get('password', '')
                if not isinstance(email, str) or not isinstance(password, str):
                    raise ValueError('Проверьте почту и пароль.')
                email = email.strip().lower()
                if len(email) > 200 or email.count('@') != 1 or '.' not in email.split('@')[-1] or any(c.isspace() for c in email):
                    raise ValueError('Введите корректную электронную почту.')
                if not 8 <= len(password) <= 200:
                    raise ValueError('Пароль должен содержать от 8 до 200 символов.')
                with connection() as db:
                    if path.endswith('/register'):
                        owner, salt = secrets.token_hex(12), secrets.token_hex(16)
                        try:
                            db.execute('INSERT INTO psychologists VALUES (?,?,?,?)', (owner, email, salt, password_hash(password, salt)))
                        except sqlite3.IntegrityError:
                            return self.send(409, {'error': 'Эта почта уже зарегистрирована. Войдите в кабинет.'})
                        p = dict(id=secrets.token_hex(12), owner_id=owner, email=email, contact='', status='draft', name='', gender='female', price=3000, experience=0, style='gentle', approach='', bio='', education='', topics=[], photo='', slots=[], hidden=False, deleted=False, revision=0, history=[])
                        event(p, 'Регистрация', 'Психолог')
                        save_record(db, p)
                    else:
                        row = db.execute('SELECT id,salt,hash FROM psychologists WHERE email=?', (email,)).fetchone()
                        if not row or not hmac.compare_digest(row[2], password_hash(password, row[1])):
                            return self.send(401, {'error': 'Неверная почта или пароль.'})
                        owner = row[0]
                token = secrets.token_urlsafe(32)
                PSY_SESSIONS[token] = dict(user=owner, expires=time.time()+28800)
                return self.send(200, {'ok': True}, f'otklik_psy={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
            if path.startswith('/api/psychologist/'):
                owner = self.psychologist()
                if not owner:
                    return self.send(401, {'error': 'Войдите в кабинет психолога.'})
                if path.endswith('/logout'):
                    for item in self.headers.get('Cookie', '').split(';'):
                        if item.strip().startswith('otklik_psy='):
                            PSY_SESSIONS.pop(item.strip().split('=',1)[1], None)
                    return self.send(200, {'ok': True}, 'otklik_psy=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                if path not in {'/api/psychologist/profile', '/api/psychologist/submit', '/api/psychologist/schedule'}:
                    return self.send(404, {'error': 'Маршрут не найден.'})
                with connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    p = owned_profile(db, owner)
                    if not p or p['deleted']:
                        return self.send(403, {'error': 'Профиль удалён администратором. Обратитесь к владельцу сервиса.'})
                    if data.get('revision') != p['revision']:
                        return self.send(409, {'error': 'Анкета изменилась. Обновите страницу и повторите действие.'})
                    if path.endswith('/profile'):
                        if p['status'] == 'pending':
                            return self.send(409, {'error': 'Анкета на рассмотрении. Дождитесь решения администратора.'})
                        fields = {'name', 'gender', 'price', 'experience', 'style', 'approach', 'bio', 'education', 'topics', 'photo', 'contact', 'education'}
                        supplied = {k: v for k, v in data.items() if k in fields}
                        proposed = p | supplied
                        # Drafts can be incomplete; validate a completed substitute for shape and limits.
                        check = proposed | {'published': False, 'slots': [], 'name': proposed.get('name') or 'Черновик', 'bio': proposed.get('bio') or 'Незаполненное описание профиля', 'approach': proposed.get('approach') or 'Не заполнено', 'education': proposed.get('education') or 'Не заполнено', 'topics': proposed.get('topics') or ['anxiety']}
                        for field, minimum in [('name',2),('bio',20),('approach',3),('education',5)]:
                            original = proposed.get(field, '')
                            if not isinstance(original, str):
                                raise ValueError('Проверьте текстовые поля анкеты.')
                            check[field] = (original + 'x' * max(0, minimum - len(original))) if original.strip() else check[field]
                        validate(check)
                        contact = proposed.get('contact', '')
                        if not isinstance(contact, str) or len(contact) > 200:
                            raise ValueError('Контакт — до 200 символов.')
                        p.update(supplied)
                        p['status'] = 'draft'
                        p['review_note'] = ''
                        event(p, 'Анкета сохранена; требуется рассмотрение', 'Психолог')
                    elif path.endswith('/submit'):
                        if p['status'] not in {'draft', 'rejected'}:
                            return self.send(409, {'error': 'Эту анкету сейчас нельзя отправить.'})
                        validate(p | {'published': False, 'slots': []})
                        p['initials'] = p['name'][0].upper()
                        p['status'] = 'pending'
                        p['review_note'] = ''
                        event(p, 'Заявка отправлена', 'Психолог')
                    else:
                        if not p['has_publication']:
                            return self.send(403, {'error': 'Расписание доступно после одобрения анкеты.'})
                        p['slots'] = validate((booking.publication(db, p['id']) or p) | {'slots': data.get('slots'), 'published': False})['slots']
                        event(p, 'Расписание обновлено', 'Психолог')
                    save_record(db, p)
                return self.send(200, {'profile': decorate(p)})
            if not self.authorized():
                return self.send(401, {'error': 'Войдите в кабинет.'})
            if path == '/api/logout':
                for item in self.headers.get('Cookie', '').split(';'):
                    if item.strip().startswith('otklik_admin='):
                        SESSIONS.pop(item.strip().split('=', 1)[1], None)
                return self.send(200, {'ok': True}, 'otklik_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            if path in {'/api/admin/profiles', '/api/admin/review'}:
                with connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT data FROM profiles WHERE id=?', (data.get('id'),)).fetchone()
                    if not row:
                        return self.send(404, {'error': 'Профиль не найден. Психолог заполняет анкету самостоятельно.'})
                    p = decorate(json.loads(row[0]), db)
                    if data.get('revision') != p['revision']:
                        return self.send(409, {'error': 'Анкета изменилась. Обновите список перед действием.'})
                    if path.endswith('/review'):
                        action = data.get('action')
                        note = data.get('note', '')
                        if not isinstance(note, str) or len(note) > 1500:
                            raise ValueError('Комментарий — до 1500 символов.')
                        if action in {'approve', 'reject'}:
                            if p['status'] != 'pending' or p['deleted']:
                                return self.send(409, {'error': 'Решение можно принять только по заявке на рассмотрении.'})
                            if action == 'reject' and not note.strip():
                                raise ValueError('Укажите причину отказа — её увидит психолог.')
                            validate(p | {'slots': [], 'published': False})
                            p['initials'] = p['name'][0].upper()
                            p['status'] = 'approved' if action == 'approve' else 'rejected'
                            p['review_note'] = note.strip()
                            if action == 'approve':
                                booking.publish(db, p)
                        elif action == 'hide':
                            p['hidden'] = True
                        elif action == 'restore':
                            p['deleted'] = False
                            p['hidden'] = False
                        elif action == 'delete':
                            p['deleted'] = True
                            p['hidden'] = True
                        else:
                            raise ValueError('Неизвестное действие.')
                        names = {'approve': 'Заявка одобрена', 'reject': 'Заявка отклонена', 'hide': 'Скрыт из каталога', 'restore': 'Профиль восстановлен / показ разрешён', 'delete': 'Профиль удалён в архив'}
                        event(p, names[action], 'Администратор', note.strip())
                    else:
                        if p['deleted']:
                            return self.send(409, {'error': 'Сначала восстановите профиль из архива.'})
                        clean = validate(p | {k: data[k] for k in ['name','bio','approach','education','gender','style','price','experience','topics','photo'] if k in data} | {'slots': [], 'published': False})
                        p.update({k: clean[k] for k in ['name','bio','approach','education','gender','style','price','experience','topics','photo','initials']})
                        event(p, 'Профиль отредактирован', 'Администратор')
                        if p['status'] == 'approved':
                            booking.publish(db, p)
                    save_record(db, p)
                return self.send(200, {'profile': decorate(p)})
            return self.send(404, {'error': 'Маршрут не найден.'})
        except communications.RateLimit as e:
            return self.send(429, {'error': str(e)})
        except booking.Conflict as e:
            return self.send(409, {'error': str(e)})
        except sqlite3.OperationalError:
            logging.exception('Database error during POST')
            return self.send(503, {'error': 'Сервис занят. Повторите запрос через несколько секунд.'})
        except (ValueError, TypeError) as e:
            text = str(e) or 'Проверьте данные.'
            field = None
            for key, label in {'name':'Имя и фамилия','bio':'О себе','approach':'Подход','education':'Образование'}.items():
                if f'«{key}»' in text:
                    field = key
                    text = text.replace(f'«{key}»', f'«{label}»')
                    break
            if not field:
                for fragment, key in [('Цена','price'), ('Опыт','experience'), ('тему','topics'), ('фото','photo'), ('фото','photo'), ('Пароль','password'), ('почту','email'), ('Контакт','contact'), ('слот','slots'), ('Слоты','slots')]:
                    if fragment in text:
                        field = key
                        break
            return self.send(400, {'error': text, 'field': field})
        except Exception:
            logging.exception('Unexpected request error')
            return self.send(500, {'error': 'Не удалось обработать запрос. Попробуйте ещё раз.'})

    def client_request(self, path, data):
        if path in {'/api/client/register', '/api/client/login'}:
            recent = [stamp for stamp in self.attempts.get('client', []) if stamp > time.time() - 60]
            if len(recent) >= 12:
                return self.send(429, {'error': 'Слишком много попыток. Подождите минуту.'})
            self.attempts['client'] = recent + [time.time()]
            email, password = data.get('email'), data.get('password')
            if not isinstance(email, str) or len(email) > 200 or email.count('@') != 1 or '.' not in email.split('@')[-1] or any(c.isspace() for c in email):
                raise ValueError('Введите корректную электронную почту.')
            if not isinstance(password, str) or not 8 <= len(password) <= 200:
                raise ValueError('Пароль должен содержать от 8 до 200 символов.')
            email = email.strip().lower()
            with connection() as db:
                if path.endswith('/register'):
                    name = data.get('name')
                    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100:
                        raise ValueError('Имя должно содержать от 2 до 100 символов.')
                    age = data.get('age')
                    if age is not None:
                        client_profiles.validate_age(age)
                    ident, salt = secrets.token_hex(12), secrets.token_hex(16)
                    try:
                        db.execute('INSERT INTO clients VALUES(?,?,?,?,?)', (ident, email, name.strip(), salt, password_hash(password, salt)))
                    except sqlite3.IntegrityError:
                        return self.send(409, {'error': 'Эта почта уже зарегистрирована. Войдите в кабинет.'})
                else:
                    row = db.execute('SELECT id,salt,hash FROM clients WHERE email=?', (email,)).fetchone()
                    if not row or not hmac.compare_digest(row['hash'], password_hash(password, row['salt'])):
                        return self.send(401, {'error': 'Неверная почта или пароль.'})
                    ident = row['id']
                if path.endswith('/register') and age is not None:
                    db.execute('INSERT INTO client_details VALUES (?,?)', (ident, age))
                token = secrets.token_urlsafe(32)
                db.execute('DELETE FROM client_sessions WHERE expires<=?', (time.time(),))
                db.execute('INSERT INTO client_sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), ident, time.time()+28800))
            return self.send(200, {'ok': True}, f'otklik_client={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
        client = self.client()
        if not client:
            return self.send(401, {'error': 'Войдите в кабинет клиента.'})
        if path == '/api/client/logout':
            cookies = dict(x.strip().split('=', 1) for x in self.headers.get('Cookie', '').split(';') if '=' in x)
            digest = hashlib.sha256(cookies.get('otklik_client', '').encode()).hexdigest()
            with connection() as db:
                db.execute('DELETE FROM client_sessions WHERE token_hash=?', (digest,))
            return self.send(200, {'ok': True}, 'otklik_client=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        if path == '/api/client/settings':
            with connection() as db:
                client_profiles.update(db, client['id'], data)
            return self.send(200, {'ok': True})
        if path == '/api/client/appointments':
            with connection() as db:
                appointment = booking.create(db, client['id'], data)
            return self.send(200, {'appointment': booking.public_appointment(appointment)})
        if path.startswith('/api/client/appointments/'):
            return self.appointment_action('client', client['id'], path, data)
        return self.send(404, {'error': 'Маршрут не найден.'})

    def appointment_action(self, role, actor, path, data):
        action = path.removeprefix(f'/api/{role}/appointments/')
        functions = {'cancel': booking.cancel, 'reschedule/propose': booking.propose_change, 'reschedule/respond': booking.respond_change}
        with connection() as db:
            if action == 'video-link' and role == 'psychologist':
                response = video_links.update(db, actor, data)
            elif action == 'reschedule/options':
                response = booking.change_options(db, role, actor, data.get('id'))
            elif action in functions:
                item = functions[action](db, role, actor, data)
                response = {'appointment': booking.public_appointment(item)}
            else:
                return self.send(404, {'error': 'Маршрут не найден.'})
        return self.send(200, response)

    @staticmethod
    def is_communication_path(path):
        parts = path.split('/')
        return len(parts) >= 4 and parts[1] == 'api' and parts[2] in {'client', 'psychologist', 'admin'} and parts[3] in {'messages', 'support', 'notifications'}

    def communication_request(self, path, data, write=False):
        parts = path.split('/')
        role = parts[2]
        if role == 'client':
            client = self.client()
            actor = client['id'] if client else None
        elif role == 'psychologist':
            actor = self.psychologist()
        else:
            actor = 'administrator' if self.authorized() else None
        if not actor:
            return self.send(401, {'error': 'Войдите в соответствующий кабинет.'})
        endpoint = '/'.join(parts[3:])
        with connection() as db:
            if endpoint == 'notifications' and not write:
                booking.expire(db)
                response = notifications.listing(db, role, actor, data.get('before'))
            elif endpoint == 'notifications/read' and write:
                response = notifications.mark_read(db, role, actor, data)
            elif endpoint == 'messages':
                if role == 'admin':
                    return self.send(403, {'error': 'Переписка встречи доступна только её участникам.'})
                response = communications.send_appointment(db, role, actor, data) if write else communications.appointment_feed(db, role, actor, data.get('appointment_id'), data.get('after', 0))
            elif endpoint == 'support/tickets':
                response = communications.create_ticket(db, role, actor, data) if write else communications.tickets(db, role, actor, data.get('before'))
            elif endpoint == 'support/messages':
                response = communications.send_support(db, role, actor, data) if write else communications.ticket_feed(db, role, actor, data.get('ticket_id'), data.get('after', 0))
            elif endpoint == 'support/status' and write:
                response = communications.ticket_status(db, role, actor, data)
            else:
                return self.send(404, {'error': 'Маршрут не найден.'})
        return self.send(200, response)


if __name__ == '__main__':
    connection().close()
    print(f'Отклик: http://127.0.0.1:{PORT}/ | Кабинет: http://127.0.0.1:{PORT}/admin', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
