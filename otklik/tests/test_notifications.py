from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

import booking
import communications
import server
import notifications
import test_changes
import test_communications


class NotificationsTest(unittest.TestCase):
    for _name in ('setUp','tearDown','request','psy','saved','submit','review','slot','public','ready','client','book','appointments',
                  'conversation','send','feed','ticket','support_send','support_feed'):
        locals()[_name] = getattr(test_communications.CommunicationsTest, _name)

    def notices(self, who='client', role='client', before=None):
        return self.request(who,'GET',f'/api/{role}/notifications'+(f'?before={before}' if before is not None else ''))

    def read(self, data, who='client', role='client'):
        return self.request(who,'POST',f'/api/{role}/notifications/read',data)

    def test_booking_and_decision_retries_notify_each_participant_once(self):
        p=self.ready();self.client()
        with ThreadPoolExecutor(2) as pool:
            replies=list(pool.map(lambda _:self.book(p),range(2)))
        self.assertEqual([code for code,_ in replies],[200,200])
        ident=replies[0][1]['appointment']['id']
        self.assertEqual(len(self.notices()[1]['notifications']),1)
        self.assertEqual(len(self.notices('psy','psychologist')[1]['notifications']),1)
        for _ in range(2):
            self.assertEqual(self.request('psy','POST','/api/psychologist/appointments/decision',{'id':ident,'action':'confirm'})[0],200)
        for who,role in [('client','client'),('psy','psychologist')]:
            data=self.notices(who,role)[1]
            self.assertEqual(data['unread_count'],2)
            self.assertEqual(data['notifications'][0]['title'],'Встреча подтверждена')
        self.assertEqual(self.notices('admin','admin')[1]['notifications'],[])

    def test_automatic_confirmation_does_not_emit_pending_notification(self):
        p=self.ready();self.client()
        self.request('psy','POST','/api/psychologist/booking-settings',{'auto_approve':True})
        self.book(p)
        items=self.notices()[1]['notifications']
        self.assertEqual([item['title'] for item in items],['Встреча подтверждена'])

    def test_private_message_has_no_body_or_sender_details_and_retries_do_not_duplicate(self):
        _,item=self.conversation()
        body='Private health details <img src=x>'
        with ThreadPoolExecutor(2) as pool:
            replies=list(pool.map(lambda _:self.send(item['id'],body=body),range(2)))
        self.assertEqual([code for code,_ in replies],[200,200])
        own=self.notices()[1]
        peer=self.notices('psy','psychologist')[1]
        self.assertNotIn('appointment_message',own['unread_by_kind'])
        self.assertEqual(peer['unread_by_kind']['appointment_message'],1)
        self.assertNotIn(body,str(peer))
        self.assertNotIn('sender_id',str(peer))
        self.assertEqual(peer['notifications'][0]['target_id'],item['id'])
        self.assertEqual(self.notices('admin','admin')[1]['notifications'],[])

    def test_scope_applies_to_listing_ids_and_bulk_read_even_with_forged_identity(self):
        _,item=self.conversation();self.client('stranger')
        ident=self.notices()[1]['notifications'][0]['id']
        self.assertEqual(self.notices('anonymous')[0],401)
        self.assertEqual(self.notices('client','psychologist')[0],401)
        self.assertEqual(self.notices('stranger')[1]['notifications'],[])
        self.assertEqual(self.read({'ids':[ident]},'stranger')[0],200)
        self.assertEqual(self.read({'through_id':2**63-1},'admin','admin')[0],200)
        self.assertEqual(self.notices()[1]['unread_count'],1)
        self.assertEqual(self.read({'ids':[ident],'recipient_id':'forged'})[0],400)

    def test_read_all_uses_snapshot_boundary_and_keeps_later_events_unread(self):
        _,item=self.conversation();snapshot=self.notices()[1]
        self.send(item['id'],'psy','psychologist',key='new-after-snapshot')
        for _ in range(2):
            response=self.read({'through_id':snapshot['through_id']})
            self.assertEqual(response[0],200)
            self.assertEqual(response[1]['unread_count'],1)
        latest=self.notices()[1]['notifications'][0]
        self.assertEqual(latest['kind'],'appointment_message')
        self.assertIsNone(latest['read_at'])
        self.read({'ids':[latest['id']]})
        server.SESSIONS.clear();server.PSY_SESSIONS.clear()
        self.assertEqual(self.notices()[1]['unread_count'],0)
        self.assertTrue(all(item['read_at'] for item in self.notices()[1]['notifications']))

    def test_read_thread_stops_at_last_delivered_message_without_reading_booking_events(self):
        _,item=self.conversation()
        first=self.send(item['id'],'psy','psychologist',key='first-incoming-message')[1]['message']
        self.send(item['id'],'psy','psychologist',key='later-incoming-message')
        response=self.read({'target_kind':'appointment','target_id':item['id'],'through_source_id':first['id']})
        self.assertEqual(response[0],200)
        self.assertEqual(response[1]['unread_by_kind'],{'appointment':1,'appointment_message':1})

    def test_support_notifies_admin_and_owner_but_never_other_users(self):
        self.psy();self.client();self.client('stranger')
        ticket=self.ticket()[1]['ticket']
        self.ticket()
        self.assertEqual(self.notices('admin','admin')[1]['unread_by_kind'],{'support_message':1})
        self.assertEqual(self.notices()[1]['unread_count'],0)
        self.support_send(ticket['id']);self.support_send(ticket['id'])
        own=self.notices()[1]
        self.assertEqual(own['unread_by_kind'],{'support_message':1})
        self.assertEqual(own['notifications'][0]['target_id'],ticket['id'])
        self.assertNotIn('Нужна помощь',str(own))
        self.assertEqual(self.notices('stranger')[1]['notifications'],[])
        self.assertEqual(self.notices('psy','psychologist')[1]['notifications'],[])
        current=self.support_feed(ticket['id'])[1]['ticket']
        self.request('admin','POST','/api/admin/support/status',{'ticket_id':ticket['id'],'status':'closed','revision':current['revision']})
        self.assertEqual(self.notices()[1]['notifications'][0]['title'],'Обращение закрыто')

    def test_invalid_cursors_and_read_requests_leave_unread_state_unchanged(self):
        self.conversation()
        for bad in ['wrong','-1',str(2**64)]:
            self.assertEqual(self.notices(before=bad)[0],400)
        for payload in ({},{'ids':[]},{'ids':[True]},{'ids':[-1]},{'ids':[1]*101},{'through_id':None},{'through_id':1.2},
                        {'target_kind':'support','target_id':[],'through_source_id':1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.read(payload)[0],400)
        self.assertEqual(self.notices()[1]['unread_count'],1)

    def test_pagination_has_no_gaps_when_new_notification_arrives(self):
        _,item=self.conversation()
        with server.connection() as db:
            owner=db.execute("SELECT json_extract(data,'$.owner_id') FROM profiles WHERE id=?",(item['profile_id'],)).fetchone()[0]
            db.executemany('INSERT INTO appointment_messages(appointment_id,sender_role,sender_id,body,request_key,created_at) VALUES(?,?,?,?,?,?)',
                           [(item['id'],'psychologist',owner,'body',f'page-message-key-{n}',booking.now()) for n in range(105)])
        first=self.notices()[1]
        self.assertEqual(len(first['notifications']),100)
        self.send(item['id'],'psy','psychologist',key='insert-between-pages')
        second=self.notices(before=first['next_before'])[1]
        ids=[row['id'] for row in first['notifications']+second['notifications']]
        self.assertEqual(len(ids),106)
        self.assertEqual(len(set(ids)),106)
        self.assertIsNone(second['next_before'])

    def test_notification_failure_rolls_back_message_and_retry_creates_once(self):
        _,item=self.conversation()
        actor=self.request('client','GET','/api/client/me')[1]['client']['id']
        with server.connection() as db:
            db.execute("CREATE TRIGGER reject_notification BEFORE INSERT ON notifications BEGIN SELECT RAISE(ABORT,'test storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            with server.connection() as db:
                communications.send_appointment(db,'client',actor,{'appointment_id':item['id'],'body':'Retry body','request_key':'rollback-message-key'})
        self.assertEqual(self.feed(item['id'])[1]['messages'],[])
        with server.connection() as db:
            db.execute('DROP TRIGGER reject_notification')
        self.assertEqual(self.send(item['id'],body='Retry body',key='rollback-message-key')[0],200)
        self.assertEqual(self.notices('psy','psychologist')[1]['unread_by_kind']['appointment_message'],1)

    def test_cancel_and_expiration_emit_once(self):
        _,item=self.conversation()
        with server.connection() as db:
            db.execute("UPDATE appointments SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(item['id'],))
        for _ in range(2):
            items=self.notices()[1]['notifications']
            self.assertEqual([row['title'] for row in items].count('Срок подтверждения заявки истёк'),1)
        self.assertEqual(self.notices('psy','psychologist')[1]['notifications'][0]['title'],'Срок подтверждения заявки истёк')
        second=self.book({'id':item['profile_id']},key='new-after-expiration')[1]['appointment']
        for _ in range(2):
            self.assertEqual(self.request('client','POST','/api/client/appointments/cancel',{'id':second['id'],'revision':second['revision']})[0],200)
        self.assertEqual([row['title'] for row in self.notices()[1]['notifications']].count('Встреча отменена'),1)

    def test_v3_migration_preserves_existing_messages_and_backup(self):
        _,item=self.conversation();self.send(item['id'])
        with server.connection() as db:
            for trigger in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'notify_%'").fetchall():
                db.execute(f'DROP TRIGGER {trigger[0]}')
            db.execute('DROP TABLE client_details')
            db.execute('DROP TABLE appointment_video')
            db.execute('DROP TABLE notifications');db.execute('PRAGMA user_version=3')
        previous=set((server.DB.parent/'backups').glob('*'))
        self.assertEqual(self.notices()[1]['notifications'],[])
        self.assertEqual(len(self.feed(item['id'])[1]['messages']),1)
        with server.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],booking.SCHEMA_VERSION)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        backups=set((server.DB.parent/'backups').glob('*'))-previous
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(next(iter(backups)))) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM appointment_messages').fetchone()[0],1)
        self.send(item['id'],'psy','psychologist',key='post-migration-message')
        self.assertEqual(self.notices()[1]['unread_by_kind'],{'appointment_message':1})

    def test_transfer_proposal_decline_withdraw_expiry_and_acceptance_notify_once(self):
        for name in ('meeting','action','propose','current'):
            setattr(self,name,getattr(test_changes.ChangesTest,name).__get__(self))
        _,item=self.meeting()
        for key,action in [('first-transfer-key','decline'),('second-transfer-key','withdraw'),('third-transfer-key','expire'),('fourth-transfer-key','accept')]:
            self.assertEqual(self.propose('client',self.current(),key=key)[0],200)
            current=self.current();change=current['change']
            if action=='expire':
                with server.connection() as db:
                    db.execute("UPDATE appointment_changes SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(change['id'],))
                self.notices();self.notices()
            else:
                who,role=('client','client') if action=='withdraw' else ('psy','psychologist')
                for _ in range(2):
                    self.assertEqual(self.action(who,current,'reschedule/respond',role,change_id=change['id'],action=action)[0],200)
        titles=[row['title'] for row in self.notices()[1]['notifications']]
        self.assertEqual(titles.count('Предложено другое время встречи'),4)
        for title in ('Перенос отклонён','Предложение переноса отозвано','Срок ответа на перенос истёк','Встреча перенесена'):
            self.assertEqual(titles.count(title),1)
        self.assertEqual(self.current()['start_at'],self.times[1])

    def test_failed_v4_migration_rolls_back_and_next_attempt_preserves_data(self):
        _,item=self.conversation()
        with server.connection() as db:
            for trigger in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'notify_%'").fetchall():
                db.execute(f'DROP TRIGGER {trigger[0]}')
            db.execute('DROP TABLE client_details')
            db.execute('DROP TABLE appointment_video')
            db.execute('DROP TABLE notifications');db.execute('PRAGMA user_version=3')
        original=notifications.install
        def fail_after_install(db):
            original(db)
            raise RuntimeError('simulated migration failure')
        with patch.object(notifications,'install',side_effect=fail_after_install):
            with self.assertRaises(RuntimeError):
                server.connection()
        with closing(sqlite3.connect(server.DB)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='notifications'").fetchone())
            self.assertEqual(db.execute('SELECT id FROM appointments').fetchone()[0],item['id'])
        self.assertEqual(self.notices()[0],200)
        self.assertEqual(self.appointments()[0]['id'],item['id'])
