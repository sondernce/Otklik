"""Private appointment conversations and separate support tickets."""
import json
from datetime import datetime, timedelta, timezone
import secrets

import booking
import access


class RateLimit(ValueError):
    pass


def text(value, maximum, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum or '\x00' in value:
        raise ValueError(f'{label}: от 1 до {maximum} символов.')
    return value.strip()


def request_key(data):
    key = data.get('request_key')
    if not isinstance(key, str) or not 16 <= len(key) <= 100:
        raise ValueError('Обновите форму сообщения и повторите отправку.')
    return key


def cursor(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise ValueError('Неверный указатель сообщений.') from None
    if number < 0 or number > 2**63-1:
        raise ValueError('Неверный указатель сообщений.')
    return number


def check_rate(db, role, actor):
    cutoff = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    total = db.execute('SELECT SUM(n) FROM ('
                       'SELECT count(*) n FROM appointment_messages WHERE sender_role=? AND sender_id=? AND created_at>? '
                       'UNION ALL SELECT count(*) n FROM support_messages WHERE sender_role=? AND sender_id=? AND created_at>?)',
                       (role, actor, cutoff, role, actor, cutoff)).fetchone()[0]
    if total >= 30:
        raise RateLimit('Слишком много сообщений. Подождите минуту.')


def public_message(row):
    return {key: row[key] for key in ('id', 'sender_role', 'body', 'created_at')}


def feed(db, table, column, ident, after):
    after = cursor(after)
    # Table and column are fixed by internal callers, never taken from a request.
    rows = db.execute(f'SELECT id,sender_role,body,created_at FROM {table} WHERE {column}=? AND id>? ORDER BY id LIMIT 101',
                      (ident, after)).fetchall()
    messages = [public_message(row) for row in rows[:100]]
    return {'messages': messages, 'last_id': messages[-1]['id'] if messages else after, 'has_more': len(rows)>100}


def appointment_feed(db, role, actor, ident, after=0):
    booking.owned_appointment(db, role, actor, ident)
    return feed(db, 'appointment_messages', 'appointment_id', ident, after)


def send_appointment(db, role, actor, data):
    body = text(data.get('body'), 4000, 'Сообщение')
    key = request_key(data)
    db.execute('BEGIN IMMEDIATE')
    item = booking.owned_appointment(db, role, actor, data.get('appointment_id'))
    previous = db.execute('SELECT * FROM appointment_messages WHERE appointment_id=? AND sender_role=? AND sender_id=? AND request_key=?',
                          (item['id'], role, actor, key)).fetchone()
    if previous:
        if previous['body'] != body:
            raise booking.Conflict('Этот запрос уже использован для другого сообщения.')
        return {'message': public_message(previous)}
    check_rate(db, role, actor)
    row = db.execute('INSERT INTO appointment_messages(appointment_id,sender_role,sender_id,body,request_key,created_at) VALUES(?,?,?,?,?,?)',
                     (item['id'], role, actor, body, key, booking.now()))
    return {'message': public_message(db.execute('SELECT * FROM appointment_messages WHERE id=?', (row.lastrowid,)).fetchone())}


def owned_ticket(db, role, actor, ident):
    access.ensure_active(db,role,actor)
    if not isinstance(ident, str):
        raise ValueError('Выберите обращение.')
    row = db.execute('SELECT * FROM support_tickets WHERE id=?', (ident,)).fetchone()
    if not row or (role != 'admin' and (row['owner_role'] != role or row['owner_id'] != actor)):
        raise booking.Conflict('Обращение не найдено или недоступно.')
    return dict(row)


def public_ticket(db, item):
    if item['owner_role'] == 'client':
        owner = db.execute('SELECT name FROM clients WHERE id=?', (item['owner_id'],)).fetchone()
    else:
        owner = db.execute("SELECT json_extract(data,'$.name') FROM profiles WHERE json_extract(data,'$.owner_id')=?", (item['owner_id'],)).fetchone()
    result = {key: item[key] for key in ('id', 'subject', 'status', 'revision', 'created_at', 'updated_at', 'owner_role')}
    result['owner_name'] = owner[0] if owner and owner[0] else ('Психолог' if item['owner_role']=='psychologist' else 'Клиент')
    return result


def tickets(db, role, actor, before=None):
    access.ensure_active(db,role,actor)
    conditions, params = [], []
    if role != 'admin':
        conditions.append('owner_role=? AND owner_id=?')
        params.extend((role, actor))
    if before is not None:
        if not isinstance(before, str) or len(before)>200 or '|' not in before:
            raise ValueError('Неверный указатель обращений.')
        stamp, ident = before.rsplit('|', 1)
        try:
            parsed = datetime.fromisoformat(stamp)
        except ValueError:
            raise ValueError('Неверный указатель обращений.') from None
        if not parsed.tzinfo or not ident.isalnum() or len(ident)>50:
            raise ValueError('Неверный указатель обращений.')
        conditions.append('(created_at<? OR (created_at=? AND id<?))')
        params.extend((stamp, stamp, ident))
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    rows = db.execute('SELECT * FROM support_tickets' + where + ' ORDER BY created_at DESC,id DESC LIMIT 101', params).fetchall()
    page = rows[:100]
    next_before = page[-1]['created_at']+'|'+page[-1]['id'] if len(rows)>100 else None
    return {'tickets': [public_ticket(db, row) for row in page], 'next_before': next_before}


def ticket_feed(db, role, actor, ident, after=0):
    item = owned_ticket(db, role, actor, ident)
    return feed(db, 'support_messages', 'ticket_id', ident, after) | {'ticket': public_ticket(db, item)}


def create_ticket(db, role, actor, data):
    if role not in {'client', 'psychologist'}:
        raise booking.Conflict('Создать обращение может клиент или психолог.')
    subject = text(data.get('subject'), 150, 'Тема')
    body = text(data.get('body'), 4000, 'Сообщение')
    key = request_key(data)
    db.execute('BEGIN IMMEDIATE')
    access.ensure_active(db,role,actor)
    previous = db.execute('SELECT * FROM support_tickets WHERE owner_role=? AND owner_id=? AND request_key=?', (role, actor, key)).fetchone()
    if previous:
        first = db.execute('SELECT body FROM support_messages WHERE ticket_id=? ORDER BY id LIMIT 1', (previous['id'],)).fetchone()
        if previous['subject'] != subject or not first or first[0] != body:
            raise booking.Conflict('Этот запрос уже использован для другого обращения.')
        return {'ticket': public_ticket(db, previous)}
    check_rate(db, role, actor)
    ident, stamp = secrets.token_hex(12), booking.now()
    db.execute('INSERT INTO support_tickets VALUES(?,?,?,?,?,?,?,?,?)', (ident, role, actor, subject, 'open', 0, stamp, stamp, key))
    db.execute('INSERT INTO support_messages(ticket_id,sender_role,sender_id,body,request_key,created_at) VALUES(?,?,?,?,?,?)',
               (ident, role, actor, body, key, stamp))
    return {'ticket': public_ticket(db, owned_ticket(db, role, actor, ident))}


def send_support(db, role, actor, data):
    body = text(data.get('body'), 4000, 'Сообщение')
    key = request_key(data)
    db.execute('BEGIN IMMEDIATE')
    item = owned_ticket(db, role, actor, data.get('ticket_id'))
    previous = db.execute('SELECT * FROM support_messages WHERE ticket_id=? AND sender_role=? AND sender_id=? AND request_key=?',
                          (item['id'], role, actor, key)).fetchone()
    if previous:
        if previous['body'] != body:
            raise booking.Conflict('Этот запрос уже использован для другого сообщения.')
        return {'message': public_message(previous)}
    if item['status'] != 'open':
        raise booking.Conflict('Обращение закрыто. Откройте его снова, чтобы написать.')
    check_rate(db, role, actor)
    stamp = booking.now()
    row = db.execute('INSERT INTO support_messages(ticket_id,sender_role,sender_id,body,request_key,created_at) VALUES(?,?,?,?,?,?)',
                     (item['id'], role, actor, body, key, stamp))
    db.execute('UPDATE support_tickets SET updated_at=?,revision=revision+1 WHERE id=?', (stamp, item['id']))
    return {'message': public_message(db.execute('SELECT * FROM support_messages WHERE id=?', (row.lastrowid,)).fetchone())}


def ticket_status(db, role, actor, data):
    if data.get('status') not in {'open', 'closed'}:
        raise ValueError('Выберите статус обращения.')
    db.execute('BEGIN IMMEDIATE')
    item = owned_ticket(db, role, actor, data.get('ticket_id'))
    if item['status'] == data['status']:
        return {'ticket': public_ticket(db, item)}
    if type(data.get('revision')) is not int or data['revision'] != item['revision']:
        raise booking.Conflict('Обращение изменилось. Обновите его и повторите действие.')
    db.execute("INSERT INTO admin_events(created_at,category,kind,actor,target_kind,target_id,result,details) VALUES(?,?,?,?,?,?,?,?)",
               (booking.now(), 'admin' if role=='admin' else 'platform', 'support_status', role, 'support', item['id'], 'success', json.dumps({'before':item['status'],'after':data['status']})))
    db.execute('UPDATE support_tickets SET status=?,updated_at=?,revision=revision+1 WHERE id=?',
               (data['status'], booking.now(), item['id']))
    return {'ticket': public_ticket(db, owned_ticket(db, role, actor, item['id']))}
