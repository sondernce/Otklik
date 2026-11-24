"""Meeting reports never infer attendance from a date or grant financial authority."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import unittest
from unittest.mock import patch

import test_booking
import server
import outcomes
import backup
import database


class OutcomeTest(unittest.TestCase):
    for name in ('setUp','tearDown','request','psy','saved','submit','review','slot','public','ready','client','book','appointments'):
        locals()[name]=getattr(test_booking.BookingTest,name)

    def meeting(self,past=True):
        p=self.ready();self.client();item=self.book(p)[1]['appointment']
        self.request('psy','POST','/api/psychologist/appointments/decision',{'id':item['id'],'action':'confirm'})
        if past:
            with server.connection() as db:db.execute("UPDATE appointments SET start_at='2000-01-01T12:00:00+00:00',end_at='2000-01-01T12:50:00+00:00' WHERE id=?",(item['id'],))
        return item

    def report(self,item,who='client',role='client',value='held',revision=0,key='outcome-request-key'):
        return self.request(who,'POST',f'/api/{role}/appointments/outcome',{'id':item['id'],'value':value,'revision':revision,'request_key':key})

    def test_dates_do_not_infer_attendance_and_future_pending_cancelled_are_rejected(self):
        item=self.meeting(False);self.assertEqual(self.report(item)[0],409)
        with server.connection() as db:db.execute("UPDATE appointments SET start_at='2000-01-01T12:00:00+00:00',end_at='2000-01-01T12:50:00+00:00' WHERE id=?",(item['id'],))
        self.assertEqual(self.appointments()[0]['outcome']['state'],'unreported')
        for changes in ("status='pending'","status='confirmed',cancelled=1"):
            with server.connection() as db:db.execute('UPDATE appointments SET '+changes+' WHERE id=?',(item['id'],))
            self.assertEqual(self.report(item)[0],409)

    def test_only_participants_and_admin_have_access(self):
        item=self.meeting();self.client('stranger');self.psy('outsider')
        self.assertEqual(self.report(item,'anonymous')[0],401)
        self.assertEqual(self.report(item,'stranger')[0],409)
        self.assertEqual(self.report(item,'outsider','psychologist')[0],409)
        self.assertEqual(self.report(item,'client','admin')[0],401)
        self.assertEqual(self.request('stranger','GET','/api/client/appointments')[1]['appointments'],[])

    def test_matching_reports_confirm_result_and_are_visible_to_both_participants(self):
        item=self.meeting();first=self.report(item)[1]['outcome']
        self.assertEqual(first['state'],'reported');self.assertIsNone(first['final_value'])
        second=self.report(item,'psy','psychologist',revision=1)[1]['outcome']
        self.assertEqual(second['state'],'agreed');self.assertEqual(second['final_value'],'held')
        self.assertEqual(self.appointments()[0]['outcome']['final_value'],'held')
        self.assertEqual(self.report(item,value='client_absent',revision=2,key='changed-outcome-key')[0],409)

    def test_disagreement_notifies_admin_and_admin_can_resolve_and_correct_explicitly(self):
        item=self.meeting();self.report(item,value='client_absent')
        data=self.report(item,'psy','psychologist',value='psychologist_absent',revision=1)[1]['outcome']
        self.assertEqual(data['state'],'disputed');self.assertIsNone(data['final_value'])
        notices=self.request('admin','GET','/api/admin/notifications')[1]['notifications']
        self.assertTrue(any(x['target_id']==item['id'] and x['kind']=='appointment' for x in notices))
        self.assertEqual(self.report(item,'admin','admin',value='client_absent',revision=2)[1]['outcome']['final_value'],'client_absent')
        self.assertEqual(self.report(item,'admin','admin',value='psychologist_absent',revision=3,key='correction-outcome-key')[1]['outcome']['state'],'resolved')
        with server.connection() as db:self.assertEqual(db.execute("SELECT count(*) FROM admin_events WHERE kind='outcome_resolved'").fetchone()[0],2)

    def test_lost_reply_replay_is_idempotent_even_after_another_report(self):
        item=self.meeting();self.assertEqual(self.report(item)[0],200)
        self.report(item,'psy','psychologist',revision=1)
        self.assertEqual(self.report(item)[1]['outcome']['revision'],2)
        self.assertEqual(self.report(item,value='client_absent')[0],409)
        with server.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM outcome_requests').fetchone()[0],2)

    def test_parallel_reports_conflict_safely_then_retry_can_agree(self):
        item=self.meeting()
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(self.report,item);b=pool.submit(self.report,item,'psy','psychologist')
            results=[a.result(),b.result()]
        self.assertEqual(sorted(x[0] for x in results),[200,409])
        who,role=('client','client') if results[0][0]==409 else ('psy','psychologist')
        self.assertEqual(self.report(item,who,role,revision=1)[1]['outcome']['state'],'agreed')

    def test_wrong_payload_and_stale_revision_do_not_mutate_results(self):
        item=self.meeting()
        for data in ({'value':'paid'},{'value':[]},{'revision':True},{'request_key':'short'}):
            body={'id':item['id'],'value':'held','revision':0,'request_key':'outcome-request-key'}|data
            self.assertIn(self.request('client','POST','/api/client/appointments/outcome',body)[0],(400,409))
        self.assertEqual(self.appointments()[0]['outcome']['state'],'unreported')
        self.report(item)
        self.assertEqual(self.report(item,'psy','psychologist',revision=0)[0],409)

    def test_transaction_failure_rolls_back_result_audit_requests_and_notifications(self):
        item=self.meeting()
        with server.connection() as db:
            db.execute("CREATE TRIGGER fail_outcome BEFORE INSERT ON outcome_requests BEGIN SELECT RAISE(ABORT,'private test error'); END")
        self.assertEqual(self.report(item)[0],503)
        self.assertEqual(self.appointments()[0]['outcome']['state'],'unreported')
        with server.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM admin_events WHERE kind='outcome_reported'").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM notifications WHERE event_key LIKE 'outcome:%'").fetchone()[0],0)

    def test_results_survive_backup_restore_and_do_not_change_booking_status(self):
        item=self.meeting();self.report(item,value='psychologist_absent')
        output=server.DB.parent/'result-copy.sqlite3';backup.snapshot(server.DB,output,restore=True)
        with database.connect(output) as db:
            self.assertEqual(outcomes.summary(db,item['id'])['client_report'],'psychologist_absent')
            self.assertEqual(db.execute('SELECT status FROM appointments WHERE id=?',(item['id'],)).fetchone()[0],'confirmed')


    def test_consensus_can_confirm_either_no_show_without_financial_side_effects(self):
        item=self.meeting()
        for value in ('client_absent','psychologist_absent'):
            with self.subTest(value=value):
                with server.connection() as db:
                    db.execute('DELETE FROM outcome_requests');db.execute('DELETE FROM appointment_outcomes')
                self.assertEqual(self.report(item,value=value)[0],200)
                self.assertEqual(self.report(item,'psy','psychologist',value=value,revision=1)[1]['outcome']['final_value'],value)
                self.assertEqual(self.appointments()[0]['price'],3000)
