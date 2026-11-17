"""Authentication regression tests against disposable databases and HTTP servers."""
import hashlib
import http.client
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import server
import access
import test_booking
import test_server


class AccessTest(unittest.TestCase):
    for _name in ('setUp','tearDown','request','psy'):
        locals()[_name] = getattr(test_server.WorkflowTest,_name)

    def test_admin_and_psychologist_sessions_survive_another_process(self):
        self.psy()
        code = '''import sys,server
server.DB=sys.argv[1]
h=server.Handler.__new__(server.Handler)
h.headers={'Cookie':sys.argv[2]}
assert h.authorized() if sys.argv[3]=='admin' else h.psychologist()
'''
        for who in ('admin','psy'):
            result=subprocess.run([sys.executable,'-B','-c',code,str(server.DB),self.cookies[who],who],cwd=server.ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_login_rate_limit_survives_clearing_process_memory(self):
        for _ in range(7):
            self.assertEqual(self.request('anonymous','POST','/api/login',{'password':'incorrect-password'})[0],401)
        code = '''import sys,server,access
server.DB=sys.argv[1]
try:
    with server.connection() as db:
        access.throttle(db,'admin','127.0.0.1')
except access.Limited:
    raise SystemExit(0)
raise SystemExit(1)
'''
        result=subprocess.run([sys.executable,'-B','-c',code,str(server.DB)],cwd=server.ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.request('anonymous','POST','/api/login',{'password':'incorrect-password'})[0],429)

    def raw(self, method, path, data=None, headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.PORT)
        supplied={'Origin':f'http://127.0.0.1:{server.PORT}','Cookie':self.cookies.get('admin','')}
        if data is not None:
            supplied['Content-Type']='application/json'
        supplied.update(headers or {})
        connection.request(method,path,None if data is None else json.dumps(data),supplied)
        response=connection.getresponse();status=response.status;response.read();connection.close()
        return status

    def test_sessions_store_only_hashes_and_expire_for_every_role(self):
        self.psy();test_booking.BookingTest.client(self)
        routes={'admin':'/api/admin/profiles','psychologist':'/api/psychologist/me','client':'/api/client/me'}
        for role,who in [('admin','admin'),('psychologist','psy'),('client','client')]:
            value=self.cookies[who].split('=',1)[1]
            with server.connection() as db:
                row=db.execute(f'SELECT * FROM {access.TABLES[role][0]}').fetchone()
                self.assertEqual(row['token_hash'],hashlib.sha256(value.encode()).hexdigest())
                self.assertNotIn(value,str(dict(row)))
                db.execute(f'UPDATE {access.TABLES[role][0]} SET expires=0')
            self.assertEqual(self.request(who,'GET',routes[role])[0],401)

    def test_logout_is_idempotent_and_old_cookie_cannot_be_replayed(self):
        self.psy();test_booking.BookingTest.client(self)
        for who,logout,endpoint in [('admin','/api/logout','/api/admin/profiles'),('psy','/api/psychologist/logout','/api/psychologist/me'),('client','/api/client/logout','/api/client/me')]:
            saved=self.cookies[who]
            self.assertEqual(self.request(who,'POST',logout,{})[0],200)
            self.cookies[who]=saved
            self.assertEqual(self.request(who,'GET',endpoint)[0],401)
            self.assertEqual(self.request(who,'POST',logout,{})[0],200)

    def test_login_rotates_current_session_but_keeps_other_device(self):
        self.psy()
        original=self.cookies['psy']
        login={'email':'psy@test.invalid','password':'test-psychologist-password'}
        self.assertEqual(self.request('other','POST','/api/psychologist/login',login)[0],200)
        self.assertEqual(self.request('psy','POST','/api/psychologist/login',login)[0],200)
        self.assertNotEqual(original,self.cookies['psy'])
        self.cookies['old']=original
        self.assertEqual(self.request('old','GET','/api/psychologist/me')[0],401)
        self.assertEqual(self.request('other','GET','/api/psychologist/me')[0],200)
        self.assertEqual(self.request('psy','GET','/api/psychologist/me')[0],200)

    def test_tokens_cannot_cross_roles_and_malformed_cookies_are_denied(self):
        self.psy()
        value=self.cookies['admin'].split('=',1)[1]
        self.cookies['forged']='otklik_psy='+value
        self.assertEqual(self.request('forged','GET','/api/psychologist/me')[0],401)
        for value in ('x'*1000,'broken','"unterminated'):
            self.assertEqual(self.raw('GET','/api/admin/profiles',headers={'Cookie':'otklik_admin='+value}),401)

    def test_host_origin_and_fetch_metadata_rejection_precede_mutation(self):
        payload={'password':'replacement-password'}
        for headers in ({'Host':'attacker.invalid'}, {'Origin':'https://attacker.invalid'}, {'Origin':'null'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.raw('POST','/api/setup',payload,headers),403)
        self.assertEqual(self.raw('GET','/api/admin/profiles',headers={'Host':'attacker.invalid'}),403)
        self.assertEqual(self.request('admin','POST','/api/logout',{},origin=False)[0],403)
        self.assertEqual(self.request('admin','GET','/api/admin/profiles')[0],200)

    def test_account_limit_is_case_normalized_and_does_not_block_other_account(self):
        self.psy()
        for _ in range(12):
            self.assertEqual(self.request('bad','POST','/api/psychologist/login',{'email':' PSY@Test.Invalid ','password':'wrong-password'})[0],401)
        self.assertEqual(self.request('bad','POST','/api/psychologist/login',{'email':'psy@test.invalid','password':'test-psychologist-password'})[0],429)
        self.assertEqual(self.request('bad','POST','/api/psychologist/login',{'email':'other@test.invalid','password':'wrong-password'})[0],401)
        with server.connection() as db:
            stored=str([tuple(row) for row in db.execute('SELECT * FROM login_attempts')])
            self.assertNotIn('psy@test.invalid',stored)
            self.assertNotIn('127.0.0.1',stored)

    def test_parallel_attempts_cannot_exceed_limit_and_expired_attempts_are_cleaned(self):
        def reserve(_):
            try:
                with server.connection() as db:
                    access.throttle(db,'psychologist','test-address','parallel@test.invalid')
                return True
            except access.Limited:
                return False
        with ThreadPoolExecutor(6) as pool:
            results=list(pool.map(reserve,range(16)))
        self.assertEqual(sum(results),12)
        with server.connection() as db:
            db.execute('UPDATE login_attempts SET attempted_at=0')
        self.assertTrue(reserve(0))
        with server.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM login_attempts').fetchone()[0],2)

    def test_v5_migration_keeps_client_sessions_and_creates_private_session_tables(self):
        client=test_booking.BookingTest.client(self)
        with server.connection() as db:
            for table in ('admin_sessions','psychologist_sessions','login_attempts'):
                db.execute(f'DROP TABLE {table}')
            db.execute('DROP INDEX client_session_expiry')
            db.execute('PRAGMA user_version=5')
        self.assertEqual(self.request('client','GET','/api/client/me')[1]['client']['id'],client['id'])
        with server.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],server.database.SCHEMA_VERSION)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0],0)
        self.assertEqual(self.request('admin','GET','/api/admin/profiles')[0],401)
        self.assertEqual(self.request('admin','POST','/api/login',{'password':'test-admin-password'})[0],200)

    def test_failed_session_write_rolls_back_registration(self):
        with patch.object(server.diagnostics,'record'), patch.object(access,'issue',side_effect=RuntimeError('simulated storage failure')):
            self.assertEqual(self.request('new','POST','/api/psychologist/register',{'email':'new@test.invalid','password':'test-psychologist-password'})[0],500)
        with server.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM psychologists').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM profiles').fetchone()[0],0)
        self.assertEqual(self.request('new','POST','/api/psychologist/register',{'email':'new@test.invalid','password':'test-psychologist-password'})[0],200)
