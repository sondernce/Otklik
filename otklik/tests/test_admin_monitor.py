"""Operational dashboard access, transactional audit, migration and safe diagnostics."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_changes
import server
import database
import admin_monitor
import backup
import diagnostics
import maintenance


class MonitorTest(unittest.TestCase):
    request=test_changes.ChangesTest.request
    psy=test_changes.ChangesTest.psy
    saved=test_changes.ChangesTest.saved
    submit=test_changes.ChangesTest.submit
    review=test_changes.ChangesTest.review
    slot=test_changes.ChangesTest.slot
    public=test_changes.ChangesTest.public
    ready=test_changes.ChangesTest.ready
    client=test_changes.ChangesTest.client
    book=test_changes.ChangesTest.book
    appointments=test_changes.ChangesTest.appointments
    meeting=test_changes.ChangesTest.meeting
    action=test_changes.ChangesTest.action

    def setUp(self):
        test_changes.ChangesTest.setUp(self)
        diagnostics.configure(server.DB.parent/'logs')

    def tearDown(self):
        diagnostics.close()
        test_changes.ChangesTest.tearDown(self)

    def overview(self):
        code,body=self.request('admin','GET','/api/admin/overview')
        self.assertEqual(code,200,body)
        return body

    def events(self,query=''):
        code,body=self.request('admin','GET','/api/admin/events'+query)
        self.assertEqual(code,200,body)
        return body

    def test_private_endpoints_require_admin_and_expired_session_cannot_access(self):
        self.client();self.psy()
        for actor in ('anonymous','client','psy'):
            for path in ('overview','events'):
                self.assertEqual(self.request(actor,'GET','/api/admin/'+path)[0],401)
        with server.connection() as db:db.execute('UPDATE admin_sessions SET expires=0')
        self.assertEqual(self.request('admin','GET','/api/admin/overview')[0],401)
        for path in ('/admin_monitor.py','/maintenance.py','/test.sqlite3.maintenance.json','/logs/otklik.log'):
            self.assertEqual(self.request('anonymous','GET',path)[0],404)

    def test_counts_and_registration_period_do_not_expose_accounts(self):
        self.client();p=self.ready()
        data=self.overview()
        self.assertEqual(data['users']['total'],2)
        self.assertEqual(data['users']['registrations'],{'client':1,'psychologist':1})
        self.assertEqual(data['profiles']['approved'],1)
        self.assertTrue(data['service']['database_available'])
        self.assertEqual(data['service']['schema_version'],7)
        self.assertNotIn('@test.invalid',json.dumps(data))
        code,old=self.request('admin','GET','/api/admin/overview?from=2000-01-01&to=2000-01-02')
        self.assertEqual(code,200);self.assertEqual(old['users']['registrations'],{'client':0,'psychologist':0})
        self.assertEqual(old['users']['total'],2)

    def test_profile_audit_has_actor_fields_and_safe_before_after_only(self):
        p=self.submit(self.saved(self.psy()))
        self.review(p,'approve','PRIVATE-REVIEW-NOTE')
        events=self.events('?category=admin')['events']
        self.assertEqual(len(events),1)
        event=events[0]
        self.assertEqual(event['kind'],'profile_approved')
        self.assertEqual(event['actor'],'admin')
        self.assertEqual(event['target_id'],p['id'])
        self.assertEqual(event['details']['before']['status'],'pending')
        self.assertEqual(event['details']['after']['status'],'approved')
        raw=json.dumps(self.events())
        for secret in ('PRIVATE-REVIEW-NOTE','private-contact','Тестовый психолог','Описание специалиста','test-psychologist-password'):
            self.assertNotIn(secret,raw)
        self.assertNotIn('appointment_messages',raw)

    def test_repeat_cancel_and_booking_do_not_duplicate_audit_and_summary(self):
        p,item=self.meeting()
        self.assertEqual(self.book(p)[0],200)
        self.assertEqual(self.action('client',item,'cancel')[0],200)
        self.assertEqual(self.action('client',item,'cancel')[0],200)
        events=self.events()['events']
        self.assertEqual(sum(e['kind']=='booking_created' for e in events),1)
        self.assertEqual(sum(e['kind']=='cancelled' for e in events),1)
        data=self.overview()
        self.assertEqual(data['meetings']['cancellations'],1)
        self.assertEqual(data['cancellations'][0]['by_client'],1)
        self.assertEqual(data['cancellations'][0]['total_bookings'],1)

    def test_expired_pending_and_decisions_are_reported_without_read_mutation(self):
        p=self.ready();self.client();_,body=self.book(p);item=body['appointment']
        self.assertEqual(self.request('psy','POST','/api/psychologist/appointments/decision',{'id':item['id'],'action':'confirm'})[0],200)
        self.assertEqual(len(self.events('?kind=booking_confirmed')['events']),1)
        with server.connection() as db:
            db.execute("UPDATE appointments SET status='pending',expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(item['id'],))
        self.assertEqual(self.overview()['meetings']['awaiting_expiration'],1)
        with server.connection() as db:self.assertEqual(db.execute('SELECT status FROM appointments').fetchone()[0],'pending')

    def test_audit_failure_rolls_back_admin_operation(self):
        p=self.submit(self.saved(self.psy()))
        with server.connection() as db:
            db.execute("CREATE TRIGGER audit_test_failure BEFORE INSERT ON admin_events BEGIN SELECT RAISE(ABORT,'PRIVATE-ERROR'); END")
        self.assertEqual(self.review(p,'approve')[0],503)
        current=self.request('psy','GET','/api/psychologist/me')[1]['profile']
        self.assertEqual(current['status'],'pending')
        self.assertEqual(self.events('?category=admin')['events'],[])
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM profile_publications').fetchone()[0],0)

    def test_registration_rollback_does_not_leave_registry_or_audit(self):
        with patch.object(server.access,'issue',side_effect=RuntimeError('PRIVATE-ERROR')):
            self.assertEqual(self.request('client','POST','/api/client/register',{'name':'Клиент','email':'x@test.invalid','password':'test-password'})[0],500)
        data=self.overview();self.assertEqual(data['users']['total'],0)
        self.assertEqual(data['users']['registrations']['client'],0)
        self.assertEqual(self.events('?kind=registered')['events'],[])

    def test_pagination_is_stable_with_equal_timestamps_and_new_events(self):
        stamp=datetime.now(timezone.utc).isoformat(timespec='microseconds')
        with server.connection() as db:
            db.executemany("INSERT INTO admin_events(created_at,category,kind,actor,target_kind,target_id,result,details) VALUES(?,'platform','registered','client','client',?,'success','{}')",[(stamp,str(i)) for i in range(115)])
        first=self.events('?category=platform');self.assertEqual(len(first['events']),50)
        self.client('later')
        second=self.events('?category=platform&cursor='+first['next_cursor'])
        third=self.events('?category=platform&cursor='+second['next_cursor'])
        ids=[e['id'] for e in first['events']+second['events']+third['events']]
        self.assertEqual(len(ids),115);self.assertEqual(len(set(ids)),115)
        self.assertIsNone(third['next_cursor'])
        self.assertEqual(self.events('?category=admin')['events'],[])

    def test_invalid_filters_and_duplicate_parameters_are_rejected(self):
        for query in ('days=2','from=bad&to=bad','from=2026-01-02&to=2026-01-01','from=2000-01-01&to=2005-01-01','category=private','result=weird','kind=%27','cursor=bad','days=1&days=7'):
            self.assertEqual(self.request('admin','GET','/api/admin/events?'+query)[0],400,query)

    def test_errors_security_and_corrupt_log_are_sanitized_and_filtered(self):
        try:raise RuntimeError('PRIVATE-TEXT')
        except RuntimeError as error:
            for _ in range(5):diagnostics.record('http_unexpected_error',error,'a'*32,'POST')
        self.request('stranger','POST','/api/login',{'password':'PRIVATE-WRONG-PASSWORD'})
        data=self.overview()
        self.assertEqual(data['errors']['count'],5)
        self.assertEqual(data['errors']['groups'][0]['count'],5)
        self.assertTrue({'errors','repeated_errors'}<={w['code'] for w in data['warnings']})
        failures=self.events('?category=errors&result=failure')['events']
        self.assertEqual(len(failures),5)
        self.assertEqual(failures[0]['request_id'],'a'*32)
        self.assertEqual(self.events('?category=security')['events'][0]['kind'],'security_login_failed')
        log=server.DB.parent/'logs'/'otklik.log'
        with log.open('a') as handle:
            handle.write('INVALID PRIVATE-RAW\n')
            handle.write(json.dumps({'event':'http_unexpected_error','at':datetime.now(timezone.utc).isoformat(),'error_type':'PRIVATE-TYPE','frames':[{'file':'PRIVATE-PATH','line':1}],'body':'PRIVATE-BODY'})+'\n')
        listing=self.events('?category=errors')
        self.assertTrue(listing['logs_partial'])
        self.assertNotIn('PRIVATE-',json.dumps(listing))
        self.assertTrue(self.overview()['errors']['partial'])

    def test_support_admin_actions_are_audited_without_message_content(self):
        self.client()
        code,body=self.request('client','POST','/api/client/support/tickets',{'subject':'PRIVATE-SUBJECT','body':'PRIVATE-MESSAGE','request_key':'monitor-ticket-request'})
        self.assertEqual(code,200,body);ticket=body['ticket']
        self.request('admin','POST','/api/admin/support/messages',{'ticket_id':ticket['id'],'body':'PRIVATE-REPLY','request_key':'monitor-reply-request'})
        ticket=self.request('admin','GET','/api/admin/support/tickets')[1]['tickets'][0]
        self.assertEqual(self.request('admin','POST','/api/admin/support/status',{'ticket_id':ticket['id'],'status':'closed','revision':ticket['revision']})[0],200)
        events=self.events('?category=admin')['events']
        self.assertEqual({e['kind'] for e in events},{'support_message','support_status'})
        self.assertNotIn('PRIVATE-',json.dumps(events))
        self.assertEqual(self.overview()['tasks']['support_open'],0)

    def test_warnings_and_backup_receipts_survive_restart_without_source_changes(self):
        self.client()
        self.request('client','POST','/api/client/support/tickets',{'subject':'Помощь','body':'Вопрос','request_key':'monitor-ticket-request'})
        p=self.submit(self.saved(self.psy()))
        old=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        with server.connection() as db:
            db.execute('UPDATE support_tickets SET updated_at=?',(old,))
            db.execute('UPDATE support_messages SET created_at=?',(old,))
            raw=json.loads(db.execute('SELECT data FROM profiles WHERE id=?',(p['id'],)).fetchone()[0]);raw['updated_at']=old
            for event in raw['history']:
                if event['action']=='Заявка отправлена':event['at']=old
            db.execute('UPDATE profiles SET data=? WHERE id=?',(json.dumps(raw),p['id']))
        with patch.object(admin_monitor.shutil,'disk_usage',return_value=type('Disk',(),{'free':100})()):
            self.assertTrue({'support','profiles','disk_low','backup'}<={w['code'] for w in self.overview()['warnings']})
        original=server.DB.read_bytes();output=server.DB.parent/'copy.sqlite3';restored=server.DB.parent/'restored.sqlite3'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(backup.main(['create','--database',str(server.DB),'--output',str(output)]),0)
            self.assertEqual(backup.main(['verify',str(output),'--record-for',str(server.DB)]),0)
            self.assertEqual(backup.main(['restore','--backup',str(output),'--output',str(restored),'--record-for',str(server.DB)]),0)
        self.assertEqual(server.DB.read_bytes(),original)
        status=self.overview()['maintenance']
        for key in ('backup_at','verified_at','restore_verified_at'):self.assertIsNotNone(status[key])
        self.assertFalse(status['automatic_backups_configured'])
        with closing(backup.readonly(restored)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM admin_events').fetchone()[0],len(self.events('?category=all')['events']))

    def test_concurrent_monitor_reads_and_events_remain_consistent(self):
        self.client()
        with ThreadPoolExecutor(4) as pool:
            replies=list(pool.map(lambda _:self.request('admin','GET','/api/admin/overview'),range(8)))
        self.assertTrue(all(code==200 and body['users']['total']==1 for code,body in replies))
        with server.connection() as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_migration_preserves_legacy_accounts_and_backup_and_rolls_back_failure(self):
        client=self.client()
        with server.connection() as db:
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'monitor_%'").fetchall():db.execute('DROP TRIGGER '+row[0])
            db.execute('DROP TABLE account_registry');db.execute('DROP TABLE admin_events');db.execute('PRAGMA user_version=6')
        original_install=admin_monitor.install
        def broken(db):original_install(db);raise RuntimeError('migration failed')
        with patch.object(admin_monitor,'install',side_effect=broken):
            with self.assertRaises(RuntimeError):database.connect(server.DB)
        with closing(sqlite3.connect(server.DB)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='admin_events'").fetchone())
        with database.connect(server.DB) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
            self.assertEqual(db.execute('SELECT count(*) FROM admin_events').fetchone()[0],0)
        self.assertEqual(self.request('client','GET','/api/client/me')[1]['client']['id'],client['id'])
        self.assertEqual(self.overview()['users']['legacy_unknown_date'],1)
        self.assertEqual(self.overview()['users']['registrations']['client'],0)
        copies=list((server.DB.parent/'backups').glob('*'))
        self.assertTrue(copies)
        with closing(backup.readonly(copies[0])) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)

    def test_moscow_date_range_includes_start_and_excludes_next_day(self):
        first=self.client('first');second=self.client('second')
        with server.connection() as db:
            db.execute("UPDATE account_registry SET registered_at='2026-11-17T21:00:00.000000+00:00' WHERE account_id=?",(first['id'],))
            db.execute("UPDATE account_registry SET registered_at='2026-11-18T21:00:00.000000+00:00' WHERE account_id=?",(second['id'],))
        code,data=self.request('admin','GET','/api/admin/overview?from=2026-11-18&to=2026-11-18')
        self.assertEqual(code,200);self.assertEqual(data['users']['registrations']['client'],1)

    def test_extreme_dates_and_malformed_monitoring_files_do_not_break_dashboard(self):
        self.assertEqual(self.request('admin','GET','/api/admin/overview?from=9999-12-31&to=9999-12-31')[0],400)
        log=server.DB.parent/'logs'/'otklik.log'
        with log.open('a') as handle:
            handle.write(json.dumps({'event':'http_unexpected_error','at':'0001-01-01T00:00:00+14:00'})+'\n')
        Path(str(server.DB)+'.maintenance.json').write_text(json.dumps({'backup_at':'0001-01-01T00:00:00+14:00'}))
        data=self.overview()
        self.assertTrue(data['errors']['partial']);self.assertFalse(data['maintenance']['available'])

    def test_denied_requests_and_rate_limit_are_logged_without_credentials(self):
        self.assertEqual(self.request('anonymous','GET','/api/admin/overview')[0],401)
        for _ in range(9):code,_=self.request('stranger','POST','/api/login',{'password':'PRIVATE-PASSWORD'})
        self.assertEqual(code,429)
        events=self.events('?category=security')['events']
        self.assertTrue({'security_login_failed','security_denied','security_limited'}<={e['kind'] for e in events})
        raw=json.dumps(events)
        self.assertNotIn('PRIVATE-',raw);self.assertNotIn('127.0.0.1',raw)

    def test_missing_oversized_and_symlink_logs_are_reported_as_incomplete(self):
        diagnostics.close();log=server.DB.parent/'logs'/'otklik.log';log.unlink()
        self.assertFalse(self.overview()['errors']['available'])
        target=server.DB.parent/'private.txt';target.write_text('PRIVATE-DATA')
        log.symlink_to(target)
        data=self.overview();self.assertFalse(data['errors']['available']);self.assertTrue(data['errors']['partial'])
        log.unlink();log.write_bytes(b'x'*(1024*1024+8193))
        self.assertTrue(self.overview()['errors']['partial'])
        self.assertNotIn('PRIVATE-',json.dumps(self.events()))

    def test_concurrent_maintenance_receipts_preserve_all_operation_timestamps(self):
        with ThreadPoolExecutor(3) as pool:list(pool.map(lambda op:maintenance.record(server.DB,op),('backup','verify','restore')))
        data=maintenance.status(server.DB)
        self.assertTrue(data['available'])
        for key in ('backup_at','verified_at','restore_verified_at'):self.assertIsNotNone(data[key])
        path=Path(str(server.DB)+'.maintenance.json')
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(list(server.DB.parent.glob('.otklik-maintenance-*')),[])

    def test_failed_backup_does_not_update_receipt_and_unwritable_receipt_preserves_copy(self):
        target=server.DB.parent/'copy.sqlite3';target.write_text('existing')
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):backup.main(['create','--database',str(server.DB),'--output',str(target)])
        self.assertIsNone(maintenance.status(server.DB)['backup_at'])
        output=server.DB.parent/'new.sqlite3';capture=io.StringIO()
        with patch.object(maintenance,'record',side_effect=PermissionError('PRIVATE-PATH')),redirect_stdout(capture):
            self.assertEqual(backup.main(['create','--database',str(server.DB),'--output',str(output)]),0)
        result=json.loads(capture.getvalue());self.assertIn('monitoring_warning',result)
        self.assertNotIn('PRIVATE-',capture.getvalue());self.assertEqual(backup.verify(output)['schema_version'],7)

    def test_followup_messages_do_not_reset_unanswered_support_wait(self):
        self.client()
        ticket=self.request('client','POST','/api/client/support/tickets',{'subject':'Вопрос','body':'Первый вопрос','request_key':'waiting-first-request'})[1]['ticket']
        old=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        with server.connection() as db:db.execute('UPDATE support_messages SET created_at=? WHERE ticket_id=?',(old,ticket['id']))
        self.request('client','POST','/api/client/support/messages',{'ticket_id':ticket['id'],'body':'Уточнение вопроса','request_key':'waiting-followup-request'})
        self.assertEqual(self.overview()['tasks']['support_overdue'],1)
        self.request('admin','POST','/api/admin/support/messages',{'ticket_id':ticket['id'],'body':'Ответ','request_key':'waiting-answer-request'})
        self.assertEqual(self.overview()['tasks']['support_unanswered'],0)

    def test_profile_review_wait_uses_submission_time_not_later_edit(self):
        p=self.submit(self.saved(self.psy()))
        old=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        with server.connection() as db:
            raw=json.loads(db.execute('SELECT data FROM profiles WHERE id=?',(p['id'],)).fetchone()[0])
            for event in raw['history']:
                if event['action']=='Заявка отправлена':event['at']=old
            raw['updated_at']=datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE profiles SET data=? WHERE id=?',(json.dumps(raw),p['id']))
        self.assertEqual(self.overview()['tasks']['profiles_overdue'],1)
