"""Account isolation, confirmation races, erasure and retained conversations."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import re
import sqlite3
import time
import unittest
from unittest.mock import patch

import test_booking
import server
import accounts
import access
import backup
import database
import email_access


class AccountTest(unittest.TestCase):
    for name in ('setUp','tearDown','request','psy','saved','submit','review','slot','public','ready','client','book','appointments'):
        locals()[name]=getattr(test_booking.BookingTest,name)

    def account(self,role='client'):
        return self.request('admin','GET','/api/admin/accounts?role='+role)[1]['accounts'][0]

    def block(self,item,status='blocked',**extra):
        return self.request('admin','POST','/api/admin/accounts/status',{'role':item['role'],'account_id':item['id'],'revision':item['revision'],'status':status}|extra)

    def change(self,who='client',role='client',email='new@test.invalid',key='change-email-request'):
        return self.request(who,'POST',f'/api/{role}/account/email-change',{'email':email,'password':'client-password' if role=='client' else 'test-psychologist-password','request_key':key})

    def token(self,role='client'):
        with server.connection() as db:
            row=db.execute('SELECT q.body,c.* FROM email_changes c JOIN mail_outbox q ON q.id=c.mail_id WHERE c.role=? ORDER BY created_at DESC LIMIT 1',(role,)).fetchone()
        return re.search('token=([A-Za-z0-9_-]{43})',row['body'])[1],dict(row)

    def erase(self,who='client',role='client',**values):
        return self.request(who,'POST',f'/api/{role}/account/delete',{'password':'client-password' if role=='client' else 'test-psychologist-password','confirmation':'УДАЛИТЬ'}|values)

    def test_admin_accounts_are_private_paginated_and_safe(self):
        self.client();self.psy()
        for who in ('anonymous','client','psy'):
            self.assertEqual(self.request(who,'GET','/api/admin/accounts')[0],401)
            self.assertEqual(self.request(who,'POST','/api/admin/accounts/status',{})[0],401)
        result=self.request('admin','GET','/api/admin/accounts')[1]
        self.assertEqual(len(result['accounts']),2)
        self.assertEqual(len(self.request('admin','GET','/api/admin/accounts?account_id='+result['accounts'][0]['id'])[1]['accounts']),1)
        self.assertNotRegex(json.dumps(result),'salt|hash|password|body')
        self.assertEqual(self.request('admin','GET','/api/admin/accounts?search=nothing')[1]['accounts'],[])
        self.assertEqual(self.request('admin','GET','/api/admin/accounts?after=invalid')[0],400)
        with server.connection() as db:
            db.executemany('INSERT INTO clients VALUES(?,?,?,?,?)',[(f'generated{i:03}',f'{i}@test.invalid','Имя','00','hash') for i in range(105)])
        first=self.request('admin','GET','/api/admin/accounts?role=client')[1]
        second=self.request('admin','GET','/api/admin/accounts?role=client&after='+first['next_after'])[1]
        self.assertEqual(len(first['accounts']),100)
        self.assertEqual(len(second['accounts']),6)
        self.assertFalse({x['id'] for x in first['accounts']}&{x['id'] for x in second['accounts']})

    def test_block_revokes_sessions_and_links_and_unblock_never_revives_them(self):
        client=self.client();self.client('other');self.change();token,_=self.token()
        item=next(x for x in self.request('admin','GET','/api/admin/accounts?role=client')[1]['accounts'] if x['id']==client['id']);old=self.cookies['client']
        with server.connection() as db:access.issue(db,'client',client['id'])
        self.assertEqual(self.block(item)[0],200)
        self.assertEqual(self.request('client','GET','/api/client/me')[0],401)
        self.assertEqual(self.request('other','GET','/api/client/me')[0],200)
        self.assertEqual(self.request('client','POST','/api/client/login',{'email':client['email'],'password':'client-password'})[0],400)
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':token})[0],400)
        self.assertEqual(self.block(item)[0],200)
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM client_sessions WHERE client_id=?',(client['id'],)).fetchone()[0],0)
        item=next(x for x in self.request('admin','GET','/api/admin/accounts?role=client')[1]['accounts'] if x['id']==client['id']);self.assertEqual(self.block(item,'active')[0],200)
        self.cookies['client']=old
        self.assertEqual(self.request('client','GET','/api/client/me')[0],401)
        self.assertEqual(self.request('client','POST','/api/client/login',{'email':client['email'],'password':'client-password'})[0],200)

    def test_block_psychologist_hides_catalogue_and_preserves_meetings(self):
        p=self.ready();self.client();self.book(p)
        item=self.account('psychologist');self.assertEqual(self.block(item)[0],200)
        self.assertEqual(self.public(),[])
        self.assertEqual(self.request('psy','GET','/api/psychologist/me')[0],401)
        self.assertEqual(len(self.appointments()),1)
        self.assertEqual(self.request('client','POST','/api/client/appointments',{'psychologist_id':p['id'],'start_at':self.slot(),'request_key':'second-booking-key'})[0],409)
        with server.connection() as db:
            with self.assertRaises(ValueError):server.booking.decide(db,p['owner_id'],self.appointments()[0]['id'],'confirm')

    def test_block_conflict_and_rollback(self):
        self.client();item=self.account()
        self.assertEqual(self.block(item,revision=9)[0],409)
        with patch.object(accounts,'audit',side_effect=sqlite3.OperationalError('private failure')):
            self.assertEqual(self.block(item)[0],503)
        self.assertEqual(self.account()['status'],'active')
        self.assertEqual(self.request('client','GET','/api/client/me')[0],200)

    def test_change_requires_password_and_keeps_old_address_until_post_confirmation(self):
        client=self.client()
        self.assertEqual(self.request('anonymous','POST','/api/client/account/email-change',{})[0],401)
        self.assertEqual(self.request('client','POST','/api/client/account/email-change',{'email':'new@test.invalid','password':'wrong-password','request_key':'change-email-request'})[0],400)
        self.assertEqual(self.change()[0],200);token,row=self.token()
        self.assertNotIn(token,row['token_hash']);self.assertIn('#role=client',row['body'])
        self.assertEqual(self.request('client','GET','/api/client/me')[1]['client']['email'],client['email'])
        self.assertEqual(self.request('anonymous','GET','/api/client/account/confirm-email?token='+token)[0],404)
        self.assertEqual(self.request('anonymous','POST','/api/psychologist/account/confirm-email',{'token':token})[0],400)
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':token})[0],200)
        self.assertEqual(self.request('client','GET','/api/client/me')[0],401)
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':token})[0],400)
        self.assertEqual(self.request('client','POST','/api/client/login',{'email':'new@test.invalid','password':'client-password'})[0],200)
        self.assertTrue(self.request('client','GET','/api/client/me')[1]['client']['email_verified'])
        with server.connection() as db:self.assertFalse(db.execute('SELECT 1 FROM mail_outbox WHERE id=? AND body<>?',(row['mail_id'],'')).fetchone())

    def test_change_idempotency_and_no_address_enumeration(self):
        self.client();self.client('occupied')
        self.assertEqual(self.change(email='occupied@test.invalid')[1],self.change(email='unused@test.invalid',key='different-email-key')[1])
        self.assertEqual(self.change(email='unused@test.invalid',key='different-email-key')[0],200)
        self.assertEqual(self.change(email='other@test.invalid',key='different-email-key')[0],409)
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM email_changes').fetchone()[0],1)

    def test_change_expiry_resend_and_reset_revoke_pending_change(self):
        client=self.client();self.change();first,_=self.token()
        with server.connection() as db:
            db.execute('UPDATE email_changes SET created_at=created_at-120,expires_at=?',(time.time()-1,))
            db.execute('DELETE FROM email_requests')
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':first})[0],400)
        self.change(email='newer@test.invalid',key='change-email-again');second,_=self.token()
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':first})[0],400)
        self.request('anonymous','POST','/api/client/password/request-reset',{'email':client['email']})
        with server.connection() as db:
            body=db.execute("SELECT body FROM mail_outbox WHERE kind='password_reset'").fetchone()[0]
        reset=re.search('token=([A-Za-z0-9_-]{43})',body)[1]
        self.assertEqual(self.request('anonymous','POST','/api/client/password/reset',{'token':reset,'password':'new-password'})[0],200)
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':second})[0],400)

    def test_parallel_confirm_changes_address_once(self):
        self.client();self.change();token,_=self.token()
        with ThreadPoolExecutor(2) as pool:
            values=list(pool.map(lambda _:self.request('anonymous','POST','/api/client/account/confirm-email',{'token':token})[0],range(2)))
        self.assertEqual(sorted(values),[200,400])

    def test_occupied_address_race_and_change_rollback(self):
        self.client();self.change();token,_=self.token()
        self.client('new')
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':token})[0],409)
        with server.connection() as db:db.execute("UPDATE clients SET email='free@test.invalid' WHERE email='new@test.invalid'")
        with patch.object(accounts,'audit',side_effect=sqlite3.OperationalError('private')):
            self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':token})[0],503)
        self.assertEqual(self.request('client','GET','/api/client/me')[1]['client']['email'],'client@test.invalid')
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':token})[0],200)

    def test_psychologist_email_changes_private_profile_only(self):
        p=self.ready();self.change('psy','psychologist');token,_=self.token('psychologist')
        self.assertEqual(self.request('anonymous','POST','/api/psychologist/account/confirm-email',{'token':token})[0],200)
        self.assertEqual(self.request('psy','POST','/api/psychologist/login',{'email':'new@test.invalid','password':'test-psychologist-password'})[0],200)
        self.assertEqual(self.request('psy','GET','/api/psychologist/me')[1]['profile']['email'],'new@test.invalid')
        self.assertNotIn('email',self.public()[0])

    def test_erasure_requires_confirmation_and_no_future_or_ongoing_meeting(self):
        p=self.ready();self.client();item=self.book(p)[1]['appointment']
        self.assertEqual(self.erase(confirmation='yes')[0],400)
        self.assertEqual(self.erase(password='wrong-password')[0],400)
        self.assertEqual(self.erase()[0],409)
        self.request('psy','POST','/api/psychologist/appointments/decision',{'id':item['id'],'action':'confirm'})
        with server.connection() as db:db.execute("UPDATE appointments SET start_at='2000-01-01T00:00:00+00:00' WHERE id=?",(item['id'],))
        self.assertEqual(self.erase()[0],409)
        with server.connection() as db:db.execute("UPDATE appointments SET end_at='2000-01-01T01:00:00+00:00' WHERE id=?",(item['id'],))
        self.assertEqual(self.erase()[0],200)

    def test_client_erasure_keeps_conversations_history_and_other_role(self):
        p=self.ready();client=self.client();item=self.book(p)[1]['appointment']
        self.request('client','POST','/api/client/messages',{'appointment_id':item['id'],'body':'Переписка сохраняется','request_key':'send-message-before-delete'})
        self.request('client','POST','/api/client/appointments/cancel',{'id':item['id'],'revision':item['revision']})
        self.change();self.assertEqual(self.erase()[0],200)
        self.assertEqual(self.request('client','GET','/api/client/me')[0],401)
        self.assertEqual(self.request('psy','GET','/api/psychologist/me')[0],200)
        self.assertEqual(self.request('psy','GET','/api/psychologist/messages?appointment_id='+item['id'])[1]['messages'][0]['body'],'Переписка сохраняется')
        with server.connection() as db:
            row=db.execute('SELECT * FROM clients WHERE id=?',(client['id'],)).fetchone()
            self.assertEqual(row['name'],'Удалённый пользователь');self.assertTrue(row['email'].endswith('@deleted.invalid'))
            self.assertFalse(db.execute('SELECT 1 FROM email_tokens WHERE account_id=?',(client['id'],)).fetchone())
            self.assertFalse(db.execute('SELECT 1 FROM email_changes WHERE account_id=?',(client['id'],)).fetchone())
        deleted=self.account();self.assertEqual(deleted['status'],'deleted');self.assertEqual(self.block(deleted,'active')[0],409)
        self.assertEqual(self.request('client','POST','/api/client/login',{'email':client['email'],'password':'client-password'})[0],401)

    def test_psychologist_erasure_clears_publication_and_cannot_restore_profile(self):
        p=self.ready();self.assertEqual(self.erase('psy','psychologist')[0],200)
        self.assertEqual(self.public(),[])
        profile=self.request('admin','GET','/api/admin/profiles')[1]['profiles'][0]
        self.assertEqual(profile['name'],'Удалённый психолог')
        for key in ('email','contact','bio','education','photo'):self.assertFalse(profile[key])
        self.assertEqual(self.review(profile,'restore')[0],409)
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM profile_publications').fetchone()[0],0)

    def test_erasure_rollback_preserves_credentials_and_session(self):
        client=self.client()
        with patch.object(accounts,'audit',side_effect=sqlite3.OperationalError('private')):
            self.assertEqual(self.erase()[0],503)
        self.assertEqual(self.request('client','GET','/api/client/me')[1]['client']['email'],client['email'])

    def test_restore_revokes_change_links_and_v9_upgrade_is_atomic(self):
        self.client();self.change();token,_=self.token()
        output=server.DB.parent/'restored.sqlite3';backup.snapshot(server.DB,output,restore=True)
        with database.connect(output) as db:
            with self.assertRaises(ValueError):accounts.confirm_change(db,'client',token)
        with closing(sqlite3.connect(server.DB)) as db:
            for table in ('outcome_requests','appointment_outcomes','email_changes','account_states'):db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=9');db.commit()
        with patch('outcomes.install',side_effect=sqlite3.OperationalError('migration failure')):
            with self.assertRaises(sqlite3.OperationalError):server.connection()
        with closing(sqlite3.connect(server.DB)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='account_states'").fetchone())
        with server.connection() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],database.SCHEMA_VERSION)
        self.assertTrue(list((server.DB.parent/'backups').glob('*')))

    def test_block_and_confirmation_race_never_leaves_a_valid_session(self):
        self.client();self.change();token,_=self.token();item=self.account()
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(self.block,item);b=pool.submit(self.request,'anonymous','POST','/api/client/account/confirm-email',{'token':token})
            self.assertEqual(a.result()[0],200);self.assertIn(b.result()[0],(200,400))
        self.assertEqual(self.account()['status'],'blocked')
        self.assertEqual(self.request('client','GET','/api/client/me')[0],401)


    def test_confirmation_does_not_sign_out_a_different_logged_in_client(self):
        self.client();self.change();token,_=self.token();other=self.client('other')
        self.assertEqual(self.request('other','POST','/api/client/account/confirm-email',{'token':token})[0],200)
        self.assertEqual(self.request('other','GET','/api/client/me')[0],200)

    def test_blocked_participant_cannot_gain_new_confirmations_or_transfers(self):
        p=self.ready();self.client();item=self.book(p)[1]['appointment']
        self.block(self.account())
        self.assertEqual(self.request('psy','POST','/api/psychologist/appointments/decision',{'id':item['id'],'action':'confirm'})[0],400)
        self.assertEqual(self.request('psy','POST','/api/psychologist/appointments/decision',{'id':item['id'],'action':'decline'})[0],200)

    def test_deleted_client_does_not_close_same_email_psychologist(self):
        self.client()
        self.assertEqual(self.request('same','POST','/api/psychologist/register',{'email':'client@test.invalid','password':'test-psychologist-password'})[0],200)
        import test_server
        test_server.complete_registration(self,'same','psychologist','client@test.invalid')
        self.assertEqual(self.erase()[0],200)
        self.assertEqual(self.request('same','GET','/api/psychologist/me')[0],200)

    def test_change_respects_legacy_unicode_domain_addresses(self):
        self.client();other=self.client('legacy')
        with server.connection() as db:db.execute('UPDATE clients SET email=? WHERE id=?',('legacy@пример.рф',other['id']))
        self.assertEqual(self.change(email='legacy@пример.рф')[0],200)
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM email_changes').fetchone()[0],0)

    def test_change_rechecks_legacy_unicode_address_before_confirmation(self):
        self.client();self.change(email='legacy@пример.рф');token,_=self.token();other=self.client('legacy')
        with server.connection() as db:db.execute('UPDATE clients SET email=? WHERE id=?',('legacy@пример.рф',other['id']))
        self.assertEqual(self.request('anonymous','POST','/api/client/account/confirm-email',{'token':token})[0],409)
