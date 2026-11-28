"""Single-use links, account isolation, reset sessions, safe retries and migration."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit

import test_booking
import server
import database
import access
import email_access
import mail_queue
import mail_worker
import backup


class EmailTest(unittest.TestCase):
    setUp=test_booking.BookingTest.setUp
    tearDown=test_booking.BookingTest.tearDown
    request=test_booking.BookingTest.request
    def legacy_email(self,role,item):
        # Existing accounts created before email verification remain supported.
        with server.connection() as db:
            db.execute('UPDATE email_status SET verified_at=NULL WHERE role=? AND account_id=?',(role,item['id'] if role=='client' else item['owner_id']))
            email_access.issue(db,role,{'id':item['id'] if role=='client' else item['owner_id'],'email':item['email']},'verify',f'http://127.0.0.1:{server.PORT}')
        return item

    def client(self,who='client'):
        return self.legacy_email('client',test_booking.BookingTest.client(self,who))

    def psy(self,who='psy'):
        return self.legacy_email('psychologist',test_booking.BookingTest.psy(self,who))

    def link(self,role='client',purpose='verify'):
        with server.connection() as db:
            row=db.execute('SELECT q.body,t.* FROM email_tokens t JOIN mail_outbox q ON q.id=t.mail_id WHERE t.role=? AND t.purpose=? ORDER BY t.created_at DESC LIMIT 1',(role,purpose)).fetchone()
        self.assertIsNotNone(row)
        link=re.search(r'http[^\s]+/account-access#[^\s]+',row['body']).group()
        return parse_qs(urlsplit(link).fragment)['token'][0],dict(row)

    def consume(self,token,role='client',purpose='verify',**data):
        return self.request('anonymous','POST',f'/api/{role}/'+('email/confirm' if purpose=='verify' else 'password/reset'),{'token':token,**data})

    def reset_request(self,role='client',email='client@test.invalid'):
        return self.request('anonymous','POST',f'/api/{role}/password/request-reset',{'email':email})

    def test_existing_accounts_can_confirm_email_for_both_roles(self):
        self.client();self.psy()
        for role in ('client','psychologist'):
            token,row=self.link(role)
            self.assertEqual(len(token),43);self.assertEqual(row['token_hash'],access.digest(token))
            self.assertNotEqual(row['token_hash'],token)
            self.assertAlmostEqual(row['expires_at']-row['created_at'],24*3600,delta=1)
            who='client' if role=='client' else 'psy'
            code,body=self.request(who,'GET',f'/api/{role}/me');self.assertEqual(code,200)
            self.assertNotIn(token,json.dumps(body));self.assertNotIn('token_hash',json.dumps(body))
            self.assertFalse(body['client']['email_verified'] if role=='client' else body['email_verified'])
            self.assertEqual(self.consume(token,role)[0],200)
            self.assertTrue(self.request(who,'GET',f'/api/{role}/email/status')[1]['email_verified'])
            self.assertEqual(self.consume(token,role)[0],400)

    def test_verification_get_does_not_consume_and_link_uses_fragment(self):
        self.client();token,row=self.link()
        self.assertEqual(self.request('anonymous','GET','/account-access')[0],200)
        self.assertEqual(self.request('anonymous','GET','/api/client/email/confirm?token='+token)[0],404)
        with server.connection() as db:self.assertIsNone(db.execute('SELECT used_at FROM email_tokens').fetchone()[0])
        self.assertEqual(self.consume(token)[0],200)

    def test_wrong_role_purpose_modified_token_and_expired_link_are_rejected(self):
        self.client();token,row=self.link()
        self.assertEqual(self.consume(token,'psychologist')[0],400)
        self.assertEqual(self.consume(token,purpose='reset',password='new-password')[0],400)
        for value in ('',[],token+'x','A'*43):self.assertEqual(self.consume(value)[0],400)
        with server.connection() as db:db.execute('UPDATE email_tokens SET expires_at=0')
        self.assertEqual(self.consume(token)[0],400)

    def test_verify_requires_ownership_only_through_token_not_current_session(self):
        first=self.client();token,row=self.link();self.client('other')
        self.assertEqual(self.request('anonymous','POST','/api/client/email/request-verification',{})[0],401)
        self.assertEqual(self.consume(token)[0],200)
        self.assertTrue(self.request('client','GET','/api/client/email/status')[1]['email_verified'])
        self.assertFalse(self.request('other','GET','/api/client/email/status')[1]['email_verified'])

    def test_reset_unknown_and_known_addresses_have_identical_public_response(self):
        self.client()
        known=self.reset_request();unknown=self.reset_request(email='unknown@test.invalid')
        self.assertEqual(known,unknown);self.assertNotIn('token',json.dumps(known))
        with server.connection() as db:self.assertEqual(db.execute("SELECT count(*) FROM email_tokens WHERE purpose='reset'").fetchone()[0],1)
        self.assertEqual(self.reset_request(),known)
        with server.connection() as db:self.assertEqual(db.execute("SELECT count(*) FROM email_tokens WHERE purpose='reset'").fetchone()[0],1)

    def test_reset_changes_password_revokes_all_sessions_and_confirms_email(self):
        client=self.client();self.client('other')
        self.request('device','POST','/api/client/login',{'email':'client@test.invalid','password':'client-password'})
        self.reset_request();token,row=self.link(purpose='reset')
        self.assertAlmostEqual(row['expires_at']-row['created_at'],30*60,delta=1)
        self.assertEqual(self.consume(token,purpose='reset',password='new-secure-password')[0],200)
        self.assertEqual(self.request('client','GET','/api/client/me')[0],401)
        self.assertEqual(self.request('device','GET','/api/client/me')[0],401)
        self.assertEqual(self.request('other','GET','/api/client/me')[0],200)
        self.assertEqual(self.request('new','POST','/api/client/login',{'email':'client@test.invalid','password':'client-password'})[0],401)
        self.assertEqual(self.request('new','POST','/api/client/login',{'email':'client@test.invalid','password':'new-secure-password'})[0],200)
        self.assertTrue(self.request('new','GET','/api/client/me')[1]['client']['email_verified'])
        self.assertEqual(self.consume(token,purpose='reset',password='another-password')[0],400)

    def test_psychologist_reset_isolated_from_client_and_admin(self):
        self.client();self.psy();self.reset_request('psychologist','psy@test.invalid')
        token,_=self.link('psychologist','reset')
        self.assertEqual(self.consume(token,'psychologist','reset',password='new-psychologist-password')[0],200)
        self.assertEqual(self.request('psy','GET','/api/psychologist/me')[0],401)
        self.assertEqual(self.request('client','GET','/api/client/me')[0],200)
        self.assertEqual(self.request('admin','GET','/api/status')[1]['authenticated'],True)
        self.assertEqual(self.request('new','POST','/api/psychologist/login',{'email':'psy@test.invalid','password':'new-psychologist-password'})[0],200)

    def test_invalid_password_does_not_consume_token(self):
        self.client();self.reset_request();token,_=self.link(purpose='reset')
        for password in ('short',None,True,'a'*201):self.assertEqual(self.consume(token,purpose='reset',password=password)[0],400)
        self.assertEqual(self.consume(token,purpose='reset',password='valid-new-password')[0],200)

    def test_resend_cooldown_then_replacement_revokes_old_link(self):
        self.client();old,_=self.link()
        self.request('client','POST','/api/client/email/request-verification',{})
        with server.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM email_tokens').fetchone()[0],1)
            db.execute('UPDATE email_tokens SET created_at=created_at-61');db.execute('UPDATE email_requests SET requested_at=requested_at-61')
        self.assertEqual(self.request('client','POST','/api/client/email/request-verification',{})[0],200)
        new,_=self.link();self.assertNotEqual(old,new)
        self.assertEqual(self.consume(old)[0],400);self.assertEqual(self.consume(new)[0],200)

    def test_parallel_token_consumption_has_one_success(self):
        self.client();token,_=self.link()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.consume(token)[0],range(2)))
        self.assertEqual(sorted(results),[200,400])
        with server.connection() as db:self.assertEqual(db.execute("SELECT count(*) FROM admin_events WHERE kind='email_verified'").fetchone()[0],1)

    def test_parallel_reset_requests_enqueue_once(self):
        self.client()
        with ThreadPoolExecutor(3) as pool:results=list(pool.map(lambda _:self.reset_request()[0],range(3)))
        self.assertEqual(results,[200]*3)
        with server.connection() as db:self.assertEqual(db.execute("SELECT count(*) FROM email_tokens WHERE purpose='reset'").fetchone()[0],1)

    def test_registration_and_reset_failures_roll_back_account_queue_and_token(self):
        with patch.object(mail_queue,'enqueue',side_effect=RuntimeError('PRIVATE-TEXT')):
            self.assertEqual(self.request('client','POST','/api/client/register',{'name':'Клиент','email':'client@test.invalid','password':'client-password'})[0],500)
        with server.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM email_tokens').fetchone()[0],0)
        self.client();self.reset_request();token,_=self.link(purpose='reset')
        with server.connection() as db:db.execute("CREATE TRIGGER deny_audit BEFORE INSERT ON admin_events BEGIN SELECT RAISE(ABORT,'error'); END")
        self.assertEqual(self.consume(token,purpose='reset',password='new-password')[0],503)
        self.assertEqual(self.request('client','GET','/api/client/me')[0],200)
        with server.connection() as db:db.execute('DROP TRIGGER deny_audit')
        self.assertEqual(self.consume(token,purpose='reset',password='new-password')[0],200)

    def test_restore_revokes_old_bearer_links_and_cancels_mail(self):
        self.client();token,_=self.link();self.reset_request();reset,_=self.link(purpose='reset')
        restored=server.DB.parent/'restored.sqlite3';backup.snapshot(server.DB,restored,restore=True)
        with database.connect(restored) as db:
            with self.assertRaises(ValueError):email_access.consume(db,'client','verify',token)
        with database.connect(restored) as db:
            with self.assertRaises(ValueError):email_access.consume(db,'client','reset',reset,'new-password')
        self.assertEqual(self.consume(token)[0],200)

    def test_preview_link_and_admin_api_do_not_expose_secrets(self):
        self.client();token,item=self.link()
        mail_worker.run_once(server.DB)
        self.assertTrue((server.DB.parent/'mail-preview'/(item['mail_id']+'.eml')).exists())
        for path in ('/api/admin/events','/api/admin/overview','/api/client/me'):
            code,body=self.request('client' if path.endswith('/me') else 'admin','GET',path);self.assertEqual(code,200)
            self.assertNotIn(token,json.dumps(body));self.assertNotIn('token_hash',json.dumps(body))
        for path in ('/email_access.py','/mail-preview/'+item['mail_id']+'.eml'):
            self.assertEqual(self.request('anonymous','GET',path)[0],404)

    def test_changed_email_and_revoked_links_are_invalid(self):
        self.client();token,_=self.link()
        with server.connection() as db:db.execute("UPDATE clients SET email='different@test.invalid'")
        self.assertEqual(self.consume(token)[0],400)

    def test_old_accounts_remain_unverified_without_blocking_existing_access(self):
        self.client()
        with server.connection() as db:
            db.execute('DELETE FROM email_tokens');db.execute('DELETE FROM email_status')
        self.assertFalse(self.request('client','GET','/api/client/me')[1]['client']['email_verified'])
        self.assertEqual(self.request('client','GET','/api/client/appointments')[0],200)

    def test_request_origin_and_token_body_validation(self):
        self.client();token,_=self.link()
        self.assertEqual(self.request('anonymous','POST','/api/client/email/confirm',{'token':token},origin=False)[0],403)
        self.assertEqual(self.consume(token)[0],200)

    def test_v8_migration_and_failed_upgrade_preserve_existing_accounts(self):
        user=self.client()
        with server.connection() as db:
            for table in ('email_tokens','email_status','email_requests'):db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=8')
        install=email_access.install
        def fail(db):install(db);raise RuntimeError('failure')
        with patch.object(email_access,'install',side_effect=fail):
            with self.assertRaises(RuntimeError):database.connect(server.DB)
        with closing(sqlite3.connect(server.DB)) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
        with server.connection() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],database.SCHEMA_VERSION)
        self.assertEqual(self.request('client','GET','/api/client/me')[1]['client']['id'],user['id'])
        self.assertTrue(list((server.DB.parent/'backups').glob('*')))

    def test_limits_do_not_reveal_account_and_valid_tokens_have_independent_buckets(self):
        self.client()
        with server.connection() as db:db.execute('DELETE FROM email_requests')
        for index in range(13):
            # Different bearer values must not share a global account limiter.
            self.assertEqual(self.consume((str(index).zfill(2)+'a'*41))[0],400)
        for _ in range(30):self.assertEqual(self.reset_request(email='unknown@test.invalid')[0],200)
        self.assertEqual(self.reset_request(email='unknown@test.invalid')[0],429)

    def test_reset_revokes_previous_verification_and_other_reset_links(self):
        self.client();verify,_=self.link();self.reset_request();old,_=self.link(purpose='reset')
        with server.connection() as db:db.execute('UPDATE email_requests SET requested_at=requested_at-61')
        self.reset_request();new,_=self.link(purpose='reset');self.assertNotEqual(old,new)
        self.assertEqual(self.consume(old,purpose='reset',password='new-password')[0],400)
        self.assertEqual(self.consume(new,purpose='reset',password='new-password')[0],200)
        self.assertEqual(self.consume(verify)[0],400)

    def test_mail_links_use_configured_origin_not_untrusted_host_and_settings_fail_uniformly(self):
        self.client();token,row=self.link()
        self.assertIn(f'http://127.0.0.1:{server.PORT}/account-access#',row['body'])
        self.assertNotIn('?token=',row['body'])
        with patch.dict('os.environ',{'OTKLIK_MAIL_TRANSPORT':'smtp','OTKLIK_PUBLIC_URL':'https://example.test/unsafe?redirect=evil'}):
            self.assertEqual(self.reset_request()[0],400)
            self.assertEqual(self.reset_request(email='missing@test.invalid')[0],400)

    def test_localhost_registration_link_keeps_the_same_browser_origin(self):
        import http.client
        conn=http.client.HTTPConnection('localhost',server.PORT)
        origin=f'http://localhost:{server.PORT}'
        conn.request('POST','/api/client/register',json.dumps({'name':'Клиент','email':'host-alias@test.invalid','password':'client-password'}),{'Origin':origin,'Content-Type':'application/json'})
        reply=conn.getresponse();body=reply.read();conn.close();self.assertEqual(reply.status,200,body)
        with server.connection() as db:
            item=db.execute('SELECT body FROM mail_outbox ORDER BY created_at DESC LIMIT 1').fetchone()
        self.assertIn('Код подтверждения:',item['body'])
        self.assertNotIn('http://',item['body'])
