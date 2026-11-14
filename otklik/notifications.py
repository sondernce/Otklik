"""Durable, private notifications created in the same transaction as their event."""
from datetime import datetime, timezone


def install(db):
    db.execute("""CREATE TABLE notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_role TEXT NOT NULL CHECK(recipient_role IN ('client','psychologist','admin')),
        recipient_id TEXT NOT NULL,
        event_key TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('appointment','appointment_message','support_message','support_status')),
        target_kind TEXT NOT NULL CHECK(target_kind IN ('appointment','support')),
        target_id TEXT NOT NULL,
        source_id INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        read_at TEXT,
        UNIQUE(recipient_role,recipient_id,event_key)
    )""")
    db.execute('CREATE INDEX notification_feed ON notifications(recipient_role,recipient_id,id)')
    db.execute('CREATE INDEX notification_unread ON notifications(recipient_role,recipient_id,id) WHERE read_at IS NULL')

    # Fixed SQL expressions only. No request values or message contents enter these triggers.
    stamp = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"
    columns = 'recipient_role,recipient_id,event_key,kind,target_kind,target_id,source_id,title,created_at'

    def insert(role, recipient, key, kind, target, ident, title, source='0', suffix=''):
        return (f"INSERT OR IGNORE INTO notifications({columns}) SELECT '{role}',{recipient},{key},"
                f"'{kind}','{target}',{ident},{source},{title},{stamp} {suffix};")

    def appointment_pair(key, title, prefix='NEW', suffix=''):
        return (insert('client', f'{prefix}.client_id', key, 'appointment', 'appointment', f'{prefix}.id', title, suffix=suffix) +
                insert('psychologist', "json_extract(p.data,'$.owner_id')", key, 'appointment', 'appointment', f'{prefix}.id', title,
                       suffix=f'FROM profiles p WHERE p.id={prefix}.profile_id'))

    db.execute("CREATE TRIGGER notify_booking_created AFTER INSERT ON appointments BEGIN " +
               appointment_pair("'booking:'||NEW.id", "CASE WHEN NEW.status='confirmed' THEN 'Встреча подтверждена' ELSE 'Новая заявка на встречу' END") + ' END')
    db.execute("CREATE TRIGGER notify_booking_status AFTER UPDATE OF status ON appointments WHEN OLD.status<>NEW.status BEGIN " +
               appointment_pair("'booking-status:'||NEW.id||':'||NEW.status||':'||NEW.revision",
                                "CASE NEW.status WHEN 'confirmed' THEN 'Встреча подтверждена' WHEN 'declined' THEN 'Заявка отклонена' WHEN 'expired' THEN 'Срок подтверждения заявки истёк' ELSE 'Статус записи изменился' END") + ' END')
    db.execute("CREATE TRIGGER notify_booking_cancelled AFTER UPDATE OF cancelled ON appointments WHEN OLD.cancelled=0 AND NEW.cancelled=1 BEGIN " +
               appointment_pair("'booking-cancelled:'||NEW.id", "'Встреча отменена'") + ' END')
    event_title = ("CASE NEW.action WHEN 'change_proposed' THEN 'Предложено другое время встречи' "
                   "WHEN 'rescheduled' THEN 'Встреча перенесена' WHEN 'change_declined' THEN 'Перенос отклонён' "
                   "WHEN 'change_cancelled' THEN 'Предложение переноса отозвано' END")
    db.execute("CREATE TRIGGER notify_booking_change AFTER INSERT ON appointment_events "
               "WHEN NEW.action IN ('change_proposed','rescheduled','change_declined','change_cancelled') BEGIN " +
               insert('client', 'a.client_id', "'booking-event:'||NEW.id", 'appointment', 'appointment', 'a.id', event_title,
                      suffix='FROM appointments a WHERE a.id=NEW.appointment_id') +
               insert('psychologist', "json_extract(p.data,'$.owner_id')", "'booking-event:'||NEW.id", 'appointment', 'appointment', 'a.id', event_title,
                      suffix='FROM appointments a JOIN profiles p ON p.id=a.profile_id WHERE a.id=NEW.appointment_id') + ' END')
    db.execute("CREATE TRIGGER notify_change_expired AFTER UPDATE OF status ON appointment_changes WHEN OLD.status='pending' AND NEW.status='expired' BEGIN " +
               insert('client', 'a.client_id', "'change-expired:'||NEW.id", 'appointment', 'appointment', 'a.id', "'Срок ответа на перенос истёк'",
                      suffix='FROM appointments a WHERE a.id=NEW.appointment_id') +
               insert('psychologist', "json_extract(p.data,'$.owner_id')", "'change-expired:'||NEW.id", 'appointment', 'appointment', 'a.id', "'Срок ответа на перенос истёк'",
                      suffix='FROM appointments a JOIN profiles p ON p.id=a.profile_id WHERE a.id=NEW.appointment_id') + ' END')
    db.execute("CREATE TRIGGER notify_appointment_message AFTER INSERT ON appointment_messages BEGIN " +
               insert('client', 'a.client_id', "'appointment-message:'||NEW.id", 'appointment_message', 'appointment', 'a.id', "'Новое сообщение от психолога'", 'NEW.id',
                      "FROM appointments a WHERE a.id=NEW.appointment_id AND NEW.sender_role='psychologist'") +
               insert('psychologist', "json_extract(p.data,'$.owner_id')", "'appointment-message:'||NEW.id", 'appointment_message', 'appointment', 'a.id', "'Новое сообщение от клиента'", 'NEW.id',
                      "FROM appointments a JOIN profiles p ON p.id=a.profile_id WHERE a.id=NEW.appointment_id AND NEW.sender_role='client'") + ' END')
    db.execute("CREATE TRIGGER notify_support_message AFTER INSERT ON support_messages BEGIN " +
               insert('admin', "'administrator'", "'support-message:'||NEW.id", 'support_message', 'support', 'NEW.ticket_id', "'Новое сообщение в поддержку'", 'NEW.id',
                      "WHERE NEW.sender_role<>'admin'") +
               f"INSERT OR IGNORE INTO notifications({columns}) SELECT t.owner_role,t.owner_id,'support-message:'||NEW.id,'support_message','support',t.id,NEW.id,'Ответ поддержки',{stamp} "
               "FROM support_tickets t WHERE t.id=NEW.ticket_id AND NEW.sender_role='admin'; END")
    db.execute("CREATE TRIGGER notify_support_status AFTER UPDATE OF status ON support_tickets WHEN OLD.status<>NEW.status BEGIN " +
               f"INSERT OR IGNORE INTO notifications({columns}) SELECT NEW.owner_role,NEW.owner_id,'support-status:'||NEW.id||':'||NEW.revision,'support_status','support',NEW.id,0,"
               f"CASE NEW.status WHEN 'closed' THEN 'Обращение закрыто' ELSE 'Обращение снова открыто' END,{stamp};" +
               insert('admin', "'administrator'", "'support-status:'||NEW.id||':'||NEW.revision", 'support_status', 'support', 'NEW.id',
                      "CASE NEW.status WHEN 'closed' THEN 'Обращение закрыто' ELSE 'Обращение снова открыто' END") + ' END')


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Неверный указатель уведомлений.')
    try:
        result = int(value)
    except ValueError:
        raise ValueError('Неверный указатель уведомлений.') from None
    if result < 0 or result > 2**63-1:
        raise ValueError('Неверный указатель уведомлений.')
    return result


def counts(db, role, actor):
    rows = db.execute('SELECT kind,count(*) n FROM notifications WHERE recipient_role=? AND recipient_id=? AND read_at IS NULL GROUP BY kind', (role, actor)).fetchall()
    by_kind = {row['kind']: row['n'] for row in rows}
    return {'unread_count': sum(by_kind.values()), 'unread_by_kind': by_kind}


def listing(db, role, actor, before=None):
    params = [role, actor]
    condition = ''
    if before is not None:
        condition = ' AND id<?'
        params.append(number(before))
    rows = db.execute('SELECT id,kind,target_kind,target_id,source_id,title,created_at,read_at FROM notifications '
                      'WHERE recipient_role=? AND recipient_id=?' + condition + ' ORDER BY id DESC LIMIT 101', params).fetchall()
    high = db.execute('SELECT COALESCE(MAX(id),0) FROM notifications WHERE recipient_role=? AND recipient_id=?', (role, actor)).fetchone()[0]
    return {'notifications': [dict(row) for row in rows[:100]], 'next_before': rows[99]['id'] if len(rows)>100 else None,
            'through_id': high} | counts(db, role, actor)


def mark_read(db, role, actor, data):
    fields = set(data)
    if fields == {'ids'} and isinstance(data['ids'], list) and 1 <= len(data['ids']) <= 100:
        ids = [number(value) for value in data['ids']]
        condition, values = 'id IN (' + ','.join('?' for _ in ids) + ')', ids
    elif fields == {'through_id'}:
        condition, values = 'id<=?', [number(data['through_id'])]
    elif fields == {'target_kind', 'target_id', 'through_source_id'}:
        if data['target_kind'] not in {'appointment', 'support'} or not isinstance(data['target_id'], str) or not 1 <= len(data['target_id']) <= 100:
            raise ValueError('Выберите переписку.')
        condition = "target_kind=? AND target_id=? AND kind IN ('appointment_message','support_message') AND source_id<=?"
        values = [data['target_kind'], data['target_id'], number(data['through_source_id'])]
    else:
        raise ValueError('Выберите уведомления для отметки прочитанными.')
    db.execute('BEGIN IMMEDIATE')
    db.execute('UPDATE notifications SET read_at=? WHERE recipient_role=? AND recipient_id=? AND read_at IS NULL AND ' + condition,
               [datetime.now(timezone.utc).isoformat(), role, actor, *values])
    return counts(db, role, actor)
