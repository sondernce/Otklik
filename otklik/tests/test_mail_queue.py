"""Outbox, concurrent workers, ambiguous SMTP results and restore safety."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import smtplib
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import test_server
import database
import backup
import mail_queue
import mail_worker


class MailTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'test.sqlite3'
        with database.connect(self.path):pass

    def enqueue(self,key='event-1',mode='local',**kwargs):
        with database.connect(self.path) as db:
            return mail_queue.enqueue(db,event_key=key,kind='verification',recipient='recipient@example.test',subject='Подтвердите адрес',body='PRIVATE-LINK https://example.test/verify#SECRET',transport=mode,**kwargs)

    def row(self,ident):
        with database.connect(self.path) as db:return dict(db.execute('SELECT * FROM mail_outbox WHERE id=?',(ident,)).fetchone())

    def config(self):
        return {'OTKLIK_MAIL_FROM':'sender@example.test','OTKLIK_SMTP_HOST':'smtp.example.test','OTKLIK_SMTP_USER':'sender','OTKLIK_SMTP_PASSWORD':'PRIVATE-PASSWORD'}

    def test_same_event_is_idempotent_before_and_after_preview(self):
        first=self.enqueue();self.assertEqual(self.enqueue(),first)
        self.assertEqual(mail_worker.run_once(self.path),{'preview':1})
        second=self.enqueue();self.assertEqual(first['id'],second['id']);self.assertEqual(second['status'],'preview')
        self.assertEqual(mail_worker.run_once(self.path),{})
        row=self.row(first['id']);self.assertEqual(row['body'],'');self.assertEqual(row['recipient'],'')
        files=list((self.path.parent/'mail-preview').glob('*.eml'));self.assertEqual(len(files),1)
        self.assertEqual(files[0].stat().st_mode&0o777,0o600)

    def test_changed_event_payload_is_rejected_without_overwrite(self):
        first=self.enqueue()
        with self.assertRaises(ValueError),database.connect(self.path) as db:
            mail_queue.enqueue(db,event_key='event-1',kind='verification',recipient='other@example.test',subject='Тема',body='Текст')
        self.assertEqual(self.row(first['id'])['recipient'],'recipient@example.test')

    def test_queue_insert_rolls_back_with_business_operation(self):
        with self.assertRaises(RuntimeError),database.connect(self.path) as db:
            db.execute("INSERT INTO settings VALUES('test-event','created')")
            mail_queue.enqueue(db,event_key='atomic',kind='appointment',recipient='x@example.test',subject='Встреча',body='Организационное уведомление')
            raise RuntimeError('rollback')
        with database.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mail_outbox').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM settings').fetchone()[0],0)

    def test_concurrent_enqueue_and_workers_create_one_preview(self):
        with ThreadPoolExecutor(4) as pool:items=list(pool.map(lambda _:self.enqueue(),range(8)))
        self.assertEqual(len({i['id'] for i in items}),1)
        with ThreadPoolExecutor(4) as pool:results=list(pool.map(lambda _:mail_worker.run_once(self.path),range(4)))
        self.assertEqual(sum(r.get('preview',0) for r in results),1)
        self.assertEqual(len(list((self.path.parent/'mail-preview').glob('*.eml'))),1)

    def test_modes_are_isolated_and_local_never_opens_smtp(self):
        local=self.enqueue();remote=self.enqueue('smtp-event','smtp')
        with patch.object(smtplib,'SMTP_SSL',side_effect=AssertionError('Must not connect')):
            self.assertEqual(mail_worker.run_once(self.path),{'preview':1})
        self.assertEqual(self.row(local['id'])['status'],'preview');self.assertEqual(self.row(remote['id'])['status'],'pending')

    def test_expired_messages_are_cancelled_and_private_content_is_removed(self):
        item=self.enqueue(expires_at=time.time()-1)
        with database.connect(self.path) as db:self.assertIsNone(mail_queue.claim(db,'local'))
        row=self.row(item['id']);self.assertEqual(row['status'],'cancelled');self.assertEqual(row['body'],'')

    def test_preview_publish_can_be_recovered_after_worker_crash(self):
        queued=self.enqueue()
        with database.connect(self.path) as db:item=mail_queue.claim(db,'local')
        mail_worker.preview(item,self.path.parent/'mail-preview')
        with database.connect(self.path) as db:db.execute('UPDATE mail_outbox SET lease_until=0')
        self.assertEqual(mail_worker.run_once(self.path),{'preview':1})
        self.assertEqual(self.row(queued['id'])['attempts'],2)
        self.assertEqual(len(list((self.path.parent/'mail-preview').glob('*.eml'))),1)

    def test_abandoned_smtp_lease_is_unknown_and_not_sent_again(self):
        item=self.enqueue(mode='smtp')
        with database.connect(self.path) as db:claimed=mail_queue.claim(db,'smtp')
        with database.connect(self.path) as db:db.execute('UPDATE mail_outbox SET lease_until=0')
        with patch.object(mail_worker,'submit_smtp',side_effect=AssertionError('Must not resend')):
            self.assertEqual(mail_worker.run_once(self.path,'smtp',env=self.config()),{})
        self.assertEqual(self.row(item['id'])['status'],'unknown')
        with database.connect(self.path) as db:self.assertFalse(mail_queue.finish(db,claimed,'sent'))

    def test_safe_retry_backoff_limit_and_lease_ownership(self):
        queued=self.enqueue(mode='smtp')
        for attempt in range(6):
            with database.connect(self.path) as db:
                item=mail_queue.claim(db,'smtp');self.assertIsNotNone(item)
            with database.connect(self.path) as db:
                self.assertFalse(mail_queue.finish(db,item|{'lease':'wrong'},'sent'))
                self.assertTrue(mail_queue.finish(db,item,'retry','connection_failed'))
            if attempt<5:
                self.assertEqual(self.row(queued['id'])['status'],'pending')
                with database.connect(self.path) as db:
                    self.assertIsNone(mail_queue.claim(db,'smtp'));db.execute('UPDATE mail_outbox SET next_attempt=0')
        row=self.row(queued['id']);self.assertEqual(row['status'],'failed');self.assertEqual(row['attempts'],6);self.assertEqual(row['body'],'')

    def test_invalid_config_does_not_claim_mail_or_expose_password(self):
        queued=self.enqueue(mode='smtp')
        with self.assertRaises(ValueError):mail_worker.run_once(self.path,'smtp',env={})
        self.assertEqual(self.row(queued['id'])['status'],'pending')
        with self.assertRaises(ValueError):mail_worker.smtp_config(self.config()|{'OTKLIK_SMTP_SECURITY':'plain'})

    def test_header_injection_and_invalid_input_are_rejected(self):
        values=[{'recipient':'x@example.test\r\nBcc: victim@example.test'}, {'subject':'Hi\nBCC'}, {'transport':'bad'}, {'kind':'private'}, {'body':''}, {'expires_at':True}]
        for changed in values:
            data=dict(event_key='invalid',kind='verification',recipient='x@example.test',subject='Тема',body='Текст')|changed
            with self.assertRaises(ValueError),database.connect(self.path) as db:mail_queue.enqueue(db,**data)

    def test_smtp_delivery_outcomes_and_safe_error_storage(self):
        for outcome,status in [('sent','sent'),('retry','pending'),('unknown','unknown'),('failed','failed')]:
            queued=self.enqueue('test-'+outcome,'smtp')
            effect=None if outcome=='sent' else mail_worker.DeliveryError(outcome,'delivery_unknown' if outcome=='unknown' else 'smtp_rejected' if outcome=='failed' else 'connection_failed')
            with patch.object(mail_worker,'submit_smtp',side_effect=effect):mail_worker.run_once(self.path,'smtp',env=self.config())
            self.assertEqual(self.row(queued['id'])['status'],status)
        with database.connect(self.path) as db:raw=json.dumps(mail_queue.summary(db))
        for secret in ('PRIVATE-','recipient@example.test','SECRET','Подтвердите'):self.assertNotIn(secret,raw)

    def test_smtp_protocol_classifies_uncertain_data_and_negative_ack(self):
        class SMTP:
            fail=''
            def __init__(self,*a,**k):self.closed=False
            def login(self,*a):
                if self.fail=='login':raise OSError('PRIVATE-PASSWORD')
            def mail(self,*a):return (250,b'ok')
            def rcpt(self,*a):return (250,b'ok')
            def data(self,*a):
                if self.fail=='lost':raise smtplib.SMTPServerDisconnected('PRIVATE-DETAIL')
                if self.fail=='temporary':raise smtplib.SMTPDataError(451,b'PRIVATE-DETAIL')
                if self.fail=='reject':return (550,b'PRIVATE-DETAIL')
                return (250,b'ok')
            def close(self):self.closed=True
        queued=self.enqueue(mode='smtp');item=self.row(queued['id']);config=mail_worker.smtp_config(self.config())
        with patch.object(smtplib,'SMTP_SSL',SMTP):
            mail_worker.submit_smtp(item,config)
            for failure,expected in [('login','retry'),('lost','unknown'),('temporary','retry'),('reject','failed')]:
                SMTP.fail=failure
                with self.assertRaises(mail_worker.DeliveryError) as caught:mail_worker.submit_smtp(item,config)
                self.assertEqual(caught.exception.outcome,expected)
                self.assertNotIn('PRIVATE-',str(caught.exception))

    def test_preview_conflict_is_not_overwritten(self):
        queued=self.enqueue();folder=self.path.parent/'mail-preview';folder.mkdir()
        target=folder/(queued['id']+'.eml');target.write_text('keep')
        self.assertEqual(mail_worker.run_once(self.path),{'retry':1});self.assertEqual(target.read_text(),'keep')
        self.assertEqual(list(folder.glob('.mail-*')),[])

    def test_backup_restore_cancels_queued_mail_and_keeps_original(self):
        item=self.enqueue(mode='smtp');restored=self.path.parent/'restored.sqlite3'
        backup.snapshot(self.path,restored,restore=True)
        self.assertEqual(self.row(item['id'])['status'],'pending')
        with database.connect(restored) as db:
            row=db.execute('SELECT * FROM mail_outbox').fetchone();self.assertEqual(row['status'],'cancelled');self.assertEqual(row['body'],'')
        with patch.object(mail_worker,'submit_smtp',side_effect=AssertionError('Must not send restored mail')):
            self.assertEqual(mail_worker.run_once(restored,'smtp',env=self.config()),{})

    def test_migration_from_v7_preserves_data_and_makes_backup(self):
        with database.connect(self.path) as db:
            db.execute('DROP TABLE mail_outbox');db.execute('PRAGMA user_version=7')
            db.execute("INSERT INTO settings VALUES('preserved','value')")
        with database.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='preserved'").fetchone()[0],'value')
        copies=list((self.path.parent/'backups').glob('*'));self.assertTrue(copies)
        with closing(sqlite3.connect(copies[0])) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)

    def test_migration_failure_rolls_back_queue_and_version(self):
        with database.connect(self.path) as db:db.execute('DROP TABLE mail_outbox');db.execute('PRAGMA user_version=7')
        install=mail_queue.install
        def fail(db):install(db);raise RuntimeError('failure')
        with patch.object(mail_queue,'install',side_effect=fail):
            with self.assertRaises(RuntimeError):database.connect(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_outbox'").fetchone())
        with database.connect(self.path) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)

    def test_repeated_event_keeps_identity_after_original_expiry(self):
        stamp=time.time();expiry=stamp+3600
        first=self.enqueue(expires_at=expiry)
        with patch.object(mail_queue.time,'time',return_value=stamp+10*86400):
            self.assertEqual(self.enqueue(expires_at=expiry)['id'],first['id'])
