from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import booking
import communications
import server
import test_booking


class CommunicationsTest(unittest.TestCase):
    for _name in ('setUp','tearDown','request','psy','saved','submit','review','slot','public','ready','client','book','appointments'):
        locals()[_name] = getattr(test_booking.BookingTest, _name)

    def conversation(self):
        p = self.ready()
        self.client()
        item = self.book(p)[1]['appointment']
        return p, item

    def send(self, ident, who='client', role='client', body='Вопрос о встрече', key='message-request-one'):
        return self.request(who,'POST',f'/api/{role}/messages',{'appointment_id':ident,'body':body,'request_key':key,'sender_role':'admin','sender_id':'forged'})

    def feed(self, ident, who='client', role='client', after=0):
        return self.request(who,'GET',f'/api/{role}/messages?appointment_id={ident}&after={after}')

    def ticket(self, who='client', role='client', key='support-request-one', subject='Вопрос о записи', body='Нужна помощь'):
        return self.request(who,'POST',f'/api/{role}/support/tickets',{'subject':subject,'body':body,'request_key':key})

    def support_send(self, ident, who='admin', role='admin', body='Ответ поддержки', key='support-reply-one'):
        return self.request(who,'POST',f'/api/{role}/support/messages',{'ticket_id':ident,'body':body,'request_key':key})

    def support_feed(self, ident, who='client', role='client'):
        return self.request(who,'GET',f'/api/{role}/support/messages?ticket_id={ident}')

    def test_only_participants_can_read_or_send_appointment_messages(self):
        p,item = self.conversation()
        code,data = self.send(item['id'])
        self.assertEqual(code,200,data)
        self.assertEqual(data['message']['sender_role'],'client')
        self.assertNotIn('sender_id',data['message'])
        self.assertEqual(self.feed(item['id'],'psy','psychologist')[1]['messages'][0]['body'],'Вопрос о встрече')
        self.client('stranger')
        self.psy('otherpsy')
        self.assertEqual(self.feed(item['id'],'stranger')[0],409)
        self.assertEqual(self.send(item['id'],'stranger')[0],409)
        self.assertEqual(self.feed(item['id'],'otherpsy','psychologist')[0],409)
        self.assertEqual(self.feed(item['id'],'admin','admin')[0],403)
        self.assertEqual(self.send(item['id'],'admin','admin')[0],403)
        self.assertEqual(self.feed(item['id'],'anonymous')[0],401)
        self.assertNotIn('Вопрос о встрече',str(self.request('admin','GET','/api/admin/appointments')[1]))

    def test_same_message_request_is_idempotent_even_when_concurrent(self):
        _,item = self.conversation()
        with ThreadPoolExecutor(2) as pool:
            responses=list(pool.map(lambda _:self.send(item['id']),range(2)))
        self.assertEqual([code for code,_ in responses],[200,200])
        self.assertEqual(responses[0][1]['message']['id'],responses[1][1]['message']['id'])
        self.assertEqual(self.send(item['id'],body='Другой текст')[0],409)
        self.assertEqual(self.send(item['id'],'psy','psychologist')[0],200)
        self.assertEqual(len(self.feed(item['id'])[1]['messages']),2)

    def test_invalid_messages_do_not_write_data(self):
        _,item = self.conversation()
        for body in ['', '  ', 'x'*4001, None, [], 'text\x00hidden']:
            with self.subTest(body_type=type(body).__name__):
                self.assertEqual(self.send(item['id'],body=body)[0],400)
        self.assertEqual(self.feed(item['id'])[1]['messages'],[])

    def test_pagination_is_scoped_and_does_not_repeat_or_skip_messages(self):
        _,item = self.conversation()
        client_id=self.request('client','GET','/api/client/me')[1]['client']['id']
        with server.connection() as db:
            db.executemany('INSERT INTO appointment_messages(appointment_id,sender_role,sender_id,body,request_key,created_at) VALUES(?,?,?,?,?,?)',
                           [(item['id'],'client',client_id,str(n),f'pagination-request-{n}',booking.now()) for n in range(101)])
        page=self.feed(item['id'])[1]
        self.assertEqual(len(page['messages']),100)
        self.assertTrue(page['has_more'])
        last=self.feed(item['id'],after=page['last_id'])[1]
        self.assertEqual([m['body'] for m in last['messages']],['100'])
        self.assertFalse(last['has_more'])
        for invalid in ['-1','wrong',str(2**64)]:
            self.assertEqual(self.feed(item['id'],after=invalid)[0],400)

    def test_rate_limit_blocks_new_messages_but_allows_safe_retries(self):
        _,item = self.conversation()
        first=self.send(item['id'])[1]['message']
        client_id=self.request('client','GET','/api/client/me')[1]['client']['id']
        with server.connection() as db:
            db.executemany('INSERT INTO appointment_messages(appointment_id,sender_role,sender_id,body,request_key,created_at) VALUES(?,?,?,?,?,?)',
                           [(item['id'],'client',client_id,'rate fixture',f'rate-test-request-{n}',booking.now()) for n in range(29)])
        self.assertEqual(self.send(item['id'],key='new-rate-limited-key')[0],429)
        self.assertEqual(self.send(item['id'])[1]['message']['id'],first['id'])

    def test_support_is_separate_and_only_owner_or_admin_has_access(self):
        _,item = self.conversation()
        ticket=self.ticket()[1]['ticket']
        self.assertEqual(self.support_send(ticket['id'])[0],200)
        messages=self.support_feed(ticket['id'])[1]['messages']
        self.assertEqual([m['sender_role'] for m in messages],['client','admin'])
        self.assertEqual(self.feed(item['id'])[1]['messages'],[])
        self.assertEqual(self.support_feed(ticket['id'],'psy','psychologist')[0],409)
        self.client('stranger')
        self.assertEqual(self.support_feed(ticket['id'],'stranger')[0],409)
        self.assertEqual(self.support_send(ticket['id'],'stranger','client')[0],409)
        self.assertEqual(self.request('stranger','GET','/api/client/support/tickets')[1]['tickets'],[])
        self.assertEqual(len(self.request('admin','GET','/api/admin/support/tickets')[1]['tickets']),1)

    def test_support_close_reopen_and_retry_after_close(self):
        self.client()
        item=self.ticket()[1]['ticket']
        original=self.support_send(item['id'])[1]['message']
        item=self.support_feed(item['id'])[1]['ticket']
        close={'ticket_id':item['id'],'status':'closed','revision':item['revision']}
        self.assertEqual(self.request('admin','POST','/api/admin/support/status',close)[0],200)
        self.assertEqual(self.support_send(item['id'])[1]['message']['id'],original['id'])
        self.assertEqual(self.support_send(item['id'],key='new-message-closed')[0],409)
        self.assertEqual(self.request('client','POST','/api/client/support/status',{'ticket_id':item['id'],'status':'open','revision':item['revision']})[0],409)
        item=self.support_feed(item['id'])[1]['ticket']
        self.assertEqual(self.request('client','POST','/api/client/support/status',{'ticket_id':item['id'],'status':'open','revision':item['revision']})[0],200)
        self.assertEqual(self.support_send(item['id'],key='new-message-reopen')[0],200)

    def test_psychologist_can_contact_support_before_profile_approval(self):
        self.psy()
        code,data=self.ticket('psy','psychologist')
        self.assertEqual(code,200,data)
        self.assertEqual(self.support_feed(data['ticket']['id'],'psy','psychologist')[0],200)
        self.assertEqual(data['ticket']['owner_role'],'psychologist')

    def test_older_support_tickets_remain_accessible_with_stable_pagination(self):
        self.client()
        actor=self.request('client','GET','/api/client/me')[1]['client']['id']
        with server.connection() as db:
            db.executemany('INSERT INTO support_tickets VALUES(?,?,?,?,?,?,?,?,?)',
                           [(f'{n:024d}','client',actor,f'Ticket {n}','open',0,booking.now(),booking.now(),f'ticket-page-request-{n}') for n in range(101)])
        first=self.request('client','GET','/api/client/support/tickets')[1]
        self.assertEqual(len(first['tickets']),100)
        self.assertIsNotNone(first['next_before'])
        self.ticket(key='new-pagination-ticket')
        from urllib.parse import quote
        second=self.request('client','GET','/api/client/support/tickets?before='+quote(first['next_before']))[1]
        self.assertEqual(len(second['tickets']),1)
        self.assertIsNone(second['next_before'])
        self.assertFalse({t['id'] for t in first['tickets']} & {t['id'] for t in second['tickets']})

    def test_ticket_creation_is_atomic_and_idempotent(self):
        self.client()
        with patch.object(communications,'public_ticket',side_effect=RuntimeError('simulated failure')),patch.object(server.logging,'exception'):
            self.assertEqual(self.ticket()[0],500)
        self.assertEqual(self.request('client','GET','/api/client/support/tickets')[1]['tickets'],[])
        with ThreadPoolExecutor(2) as pool:
            responses=list(pool.map(lambda _:self.ticket(),range(2)))
        self.assertEqual([code for code,_ in responses],[200,200])
        ident=responses[0][1]['ticket']['id']
        self.assertEqual(ident,responses[1][1]['ticket']['id'])
        self.assertEqual(len(self.support_feed(ident)[1]['messages']),1)
        self.assertEqual(self.ticket(body='Другой текст')[0],409)

    def test_messages_survive_backup_restore_and_cancelled_appointments(self):
        p,item=self.conversation()
        self.send(item['id'])
        ticket=self.ticket()[1]['ticket']
        self.request('client','POST','/api/client/appointments/cancel',{'id':item['id'],'revision':item['revision']})
        self.assertEqual(self.send(item['id'],'psy','psychologist',key='after-cancel-message')[0],200)
        original=server.DB
        copy=Path(self.temp.name)/'restored-messages.sqlite3'
        with server.connection() as source,closing(sqlite3.connect(copy)) as destination:
            source.backup(destination)
        server.DB=copy
        self.assertEqual(len(self.feed(item['id'])[1]['messages']),2)
        self.assertEqual(self.support_feed(ticket['id'])[1]['messages'][0]['body'],'Нужна помощь')
        self.assertEqual(self.feed(item['id'],'admin','admin')[0],403)
        server.DB=original

    def test_upgrade_from_v2_preserves_appointments_and_creates_empty_conversations(self):
        _,item=self.conversation()
        with server.connection() as db:
            for trigger in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'notify_%'").fetchall():
                db.execute(f'DROP TRIGGER {trigger[0]}')
            for table in ('notifications','support_messages','support_tickets','appointment_messages'):
                db.execute(f'DROP TABLE {table}')
            db.execute('DROP TABLE client_details')
            db.execute('DROP TABLE appointment_video')
            db.execute('PRAGMA user_version=2')
        self.assertEqual(self.feed(item['id'])[1]['messages'],[])
        self.assertEqual(self.appointments()[0]['id'],item['id'])
        with server.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],booking.SCHEMA_VERSION)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
