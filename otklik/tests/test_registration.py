"""No real account or authenticated session exists before a correct email code."""
import json
import re
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import test_server
import server
import access

class RegistrationTest(unittest.TestCase):
    setUp=test_server.WorkflowTest.setUp
    tearDown=test_server.WorkflowTest.tearDown
    def request(self,who,method,path,data=None,origin=True):
        from http.cookies import SimpleCookie
        old=self.cookies.get(who,'')
        result=test_server.WorkflowTest.request(self,who,method,path,data,origin)
        jar=SimpleCookie();jar.load(old);jar.load(self.cookies.get(who,''))
        self.cookies[who]='; '.join(key+'='+item.value for key,item in jar.items())
        return result

    def start(self,role='client',who='new',**extra):
        return self.request(who,'POST',f'/api/{role}/register',dict(email='new@test.invalid',password='new-password',name='Новый клиент',age=25,request_key='registration-request-key-12345678')|extra)

    def code(self,role='client'):
        with server.connection() as db:
            row=db.execute('SELECT q.body FROM pending_registrations p JOIN mail_outbox q ON q.id=p.mail_id WHERE role=? ORDER BY p.created_at DESC LIMIT 1',(role,)).fetchone()
        return re.search(r'Код подтверждения: (\d{6})',row['body'])[1]

    def confirm(self,code,role='client',who='new'):
        return self.request(who,'POST',f'/api/{role}/registration/confirm',{'code':code})

    def test_no_account_session_or_profile_before_code_for_both_roles(self):
        for role,table in [('client','clients'),('psychologist','psychologists')]:
            code,result=self.start(role);self.assertEqual(code,200,result);self.assertTrue(result['verification_required'])
            self.assertEqual(self.request('new','GET',f'/api/{role}/me')[0],401)
            code,status=self.request('new','GET',f'/api/{role}/registration/status');self.assertEqual(code,200)
            self.assertEqual(status['email'],'new@test.invalid');self.assertNotRegex(json.dumps(status),'code_hash|password|salt')
            self.assertEqual(self.request('other','GET',f'/api/{role}/registration/status')[0],401)
            with server.connection() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM profiles').fetchone()[0],0)
            self.assertEqual(self.request('other','POST',f'/api/{role}/login',{'email':'new@test.invalid','password':'new-password'})[0],401)
            secret=self.code(role);self.assertEqual(self.confirm(secret,role)[0],200)
            self.assertEqual(self.request('new','GET',f'/api/{role}/me')[0],200)
            self.assertTrue(self.request('new','GET',f'/api/{role}/email/status')[1]['email_verified'])
            self.assertEqual(self.confirm(secret,role)[0],409)

    def test_wrong_code_attempts_persist_and_session_cannot_be_guessed(self):
        self.start();secret=self.code();wrong='000000' if secret!='000000' else '111111'
        for _ in range(5):self.assertEqual(self.confirm(wrong)[0],400)
        self.assertEqual(self.confirm(secret)[0],429)
        self.assertEqual(self.confirm(secret,who='other')[0],401)
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],0)

    def test_retry_start_does_not_send_duplicate_and_changed_payload_is_rejected(self):
        self.start();secret=self.code();self.assertEqual(self.start()[0],200)
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM mail_outbox').fetchone()[0],1)
        self.assertEqual(self.start(password='different-password')[0],409)
        self.assertEqual(self.code(),secret)

    def test_change_email_resend_cooldown_and_old_code_rejected(self):
        self.start();old=self.code()
        self.assertEqual(self.request('new','POST','/api/client/registration/resend',{})[0],429)
        with patch('registration.time.time',return_value=time.time()+61):
            code,result=self.request('new','POST','/api/client/registration/change-email',{'email':'correct@test.invalid'})
        self.assertEqual(code,200,result);self.assertEqual(result['email'],'correct@test.invalid')
        self.assertEqual(self.confirm(old)[0],400)
        self.assertEqual(self.confirm(self.code())[0],200)
        self.assertEqual(self.request('new','GET','/api/client/me')[1]['client']['email'],'correct@test.invalid')

    def test_expired_code_and_wrong_role_cannot_create_account(self):
        self.start();secret=self.code()
        self.assertEqual(self.confirm(secret,'psychologist')[0],401)
        with server.connection() as db:db.execute('UPDATE pending_registrations SET code_expires_at=0')
        self.assertEqual(self.confirm(secret)[0],400)

    def test_confirmation_rolls_back_when_session_write_fails(self):
        self.start();secret=self.code()
        with patch.object(access,'issue',side_effect=RuntimeError('synthetic failure')),patch.object(server.diagnostics,'record'):
            self.assertEqual(self.confirm(secret)[0],500)
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],0)
        self.assertEqual(self.confirm(secret)[0],200)

    def test_concurrent_confirmation_creates_only_one_account(self):
        self.start();secret=self.code()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.confirm(secret),range(2)))
        self.assertEqual(sorted(x[0] for x in results),[200,409])
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],1)

    def test_code_does_not_leak_and_get_does_not_confirm(self):
        self.start();secret=self.code()
        code,body=self.request('new','GET','/api/client/registration/status')
        self.assertEqual(code,200);self.assertNotIn(secret,json.dumps(body))
        self.assertEqual(self.request('new','GET','/api/client/registration/confirm?code='+secret)[0],404)
        with server.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM account_registry WHERE role='client'").fetchone()[0],0)
            row=db.execute('SELECT * FROM pending_registrations').fetchone()
            self.assertNotIn('new-password',str(dict(row)));self.assertNotEqual(row['code_hash'],secret)

    def test_restore_cancels_pending_codes_without_creating_users(self):
        import backup,database
        self.start();secret=self.code();output=server.DB.parent/'restored.sqlite3'
        backup.snapshot(server.DB,output,restore=True)
        with database.connect(output) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pending_registrations').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT body FROM mail_outbox').fetchone()[0],'')
        self.assertEqual(self.confirm(secret)[0],200)

    def test_upgrade_from_ten_preserves_accounts_and_rolls_back_on_failure(self):
        import sqlite3,registration,database
        from contextlib import closing
        self.start();self.confirm(self.code())
        with closing(sqlite3.connect(server.DB)) as db:
            db.execute('DROP TABLE pending_registrations');db.execute('PRAGMA user_version=10');db.commit()
        with patch.object(registration,'install',side_effect=sqlite3.OperationalError('synthetic migration failure')):
            with self.assertRaises(sqlite3.OperationalError):server.connection()
        with closing(sqlite3.connect(server.DB)) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
        with server.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],database.SCHEMA_VERSION)
            self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],1)
        self.assertTrue(list((server.DB.parent/'backups').glob('*')))

    def test_expired_pending_registration_is_scrubbed_and_old_sessions_still_work(self):
        self.start()
        with server.connection() as db:db.execute('UPDATE pending_registrations SET expires_at=0')
        self.assertEqual(self.request('new','GET','/api/client/registration/status')[0],401)
        with server.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pending_registrations').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT body FROM mail_outbox').fetchone()[0],'')

if __name__=='__main__':unittest.main()
