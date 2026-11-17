import concurrent.futures
from datetime import datetime, timedelta, timezone
import json
import unittest

import booking
import server
import test_booking


class ChangesTest(unittest.TestCase):
    setUp = test_booking.BookingTest.setUp
    tearDown = test_booking.BookingTest.tearDown
    request = test_booking.BookingTest.request
    psy = test_booking.BookingTest.psy
    saved = test_booking.BookingTest.saved
    submit = test_booking.BookingTest.submit
    review = test_booking.BookingTest.review
    slot = test_booking.BookingTest.slot
    public = test_booking.BookingTest.public
    ready = test_booking.BookingTest.ready
    client = test_booking.BookingTest.client
    book = test_booking.BookingTest.book
    appointments = test_booking.BookingTest.appointments

    def meeting(self, auto=False):
        p = self.ready()
        self.times = [(datetime.fromisoformat(self.slot())+timedelta(hours=n)).isoformat() for n in (0, 2, 4)]
        p = self.request('psy', 'POST', '/api/psychologist/schedule', {'revision':p['revision'], 'slots':self.times})[1]['profile']
        self.client()
        if auto:
            self.request('psy', 'POST', '/api/psychologist/booking-settings', {'auto_approve':True})
        item = self.book(p)[1]['appointment']
        if not auto:
            item = self.request('psy','POST','/api/psychologist/appointments/decision',{'id':item['id'],'action':'confirm'})[1]['appointment']
        return p, item

    def action(self, who, item, operation, role='client', **extra):
        return self.request(who, 'POST', f'/api/{role}/appointments/{operation}', {'id':item['id'], 'revision':item['revision'], **extra})

    def propose(self, who, item, role='client', key='reschedule-request-one', target=1):
        return self.action(who, item, 'reschedule/propose', role, start_at=self.times[target], request_key=key)

    def current(self):
        return self.appointments()[0]

    def test_client_and_psychologist_cancellation_release_slots(self):
        p, item = self.meeting()
        self.assertEqual(self.action('client',item,'cancel')[0],200)
        self.assertEqual(self.action('client',item,'cancel')[0],200)
        self.assertEqual(self.current()['status'],'cancelled')
        self.assertEqual(len(self.current()['history']),1)
        self.assertEqual(self.public()[0]['slots'],self.times)
        self.client('secondclient')
        second=self.book(p,'secondclient')[1]['appointment']
        self.assertEqual(self.action('psy',second,'cancel','psychologist')[0],200)
        self.assertEqual(self.appointments('secondclient')[0]['cancelled_by'],'psychologist')

    def test_manual_transfer_keeps_original_until_other_party_accepts(self):
        p,item=self.meeting()
        self.assertEqual(self.propose('client',item)[0],200)
        current=self.current()
        self.assertEqual(current['start_at'],self.times[0])
        self.assertEqual(current['change']['start_at'],self.times[1])
        self.assertEqual(self.public()[0]['slots'],[self.times[2]])
        change=current['change']
        self.assertEqual(self.action('client',current,'reschedule/respond',change_id=change['id'],action='accept')[0],409)
        self.assertEqual(self.action('psy',current,'reschedule/respond','psychologist',change_id=change['id'],action='accept')[0],200)
        current=self.current()
        self.assertEqual(current['start_at'],self.times[1])
        self.assertIsNone(current['change'])
        self.assertEqual(current['price'],3000)
        self.assertEqual(self.public()[0]['slots'],[self.times[0],self.times[2]])
        self.assertEqual(self.action('client',item,'cancel')[0],409)

    def test_psychologist_proposal_requires_client_acceptance(self):
        p,item=self.meeting(auto=True)
        self.assertEqual(self.propose('psy',item,'psychologist')[0],200)
        current=self.current()
        self.assertEqual(current['start_at'],self.times[0])
        self.assertEqual(self.action('client',current,'reschedule/respond',change_id=current['change']['id'],action='accept')[0],200)
        self.assertEqual(self.current()['start_at'],self.times[1])

    def test_auto_approval_and_duplicate_proposal(self):
        p,item=self.meeting(auto=True)
        self.assertEqual(self.propose('client',item)[0],200)
        self.assertEqual(self.propose('client',item)[0],200)
        self.assertEqual(self.current()['start_at'],self.times[1])
        self.assertIsNone(self.current()['change'])
        self.assertEqual(len(self.current()['history']),1)

    def test_original_booking_retry_remains_idempotent_after_multiple_transfers(self):
        p,item=self.meeting(auto=True)
        self.assertEqual(self.propose('client',item)[0],200)
        self.assertEqual(self.propose('client',self.current(),key='second-transfer-retry',target=2)[0],200)
        code,replay=self.book(p)
        self.assertEqual(code,200,replay)
        self.assertEqual(replay['appointment']['id'],item['id'])
        self.assertEqual(replay['appointment']['start_at'],self.times[2])
        wrong=self.request('client','POST','/api/client/appointments',{'psychologist_id':p['id'],'start_at':self.times[2],'request_key':'one-booking-request'})
        self.assertEqual(wrong[0],409)
        self.assertEqual(len(self.appointments()),1)

    def test_expired_declined_and_withdrawn_proposals_preserve_original(self):
        p,item=self.meeting()
        self.propose('client',item)
        current=self.current()
        self.assertEqual(self.action('psy',current,'reschedule/respond','psychologist',change_id=current['change']['id'],action='decline')[0],200)
        current=self.current()
        self.assertEqual(current['start_at'],self.times[0])
        self.propose('client',current,key='second-change-request')
        current=self.current()
        self.assertEqual(self.action('client',current,'reschedule/respond',change_id=current['change']['id'],action='withdraw')[0],200)
        self.propose('client',self.current(),key='third-change-request')
        change=self.current()['change']
        with server.connection() as db:
            db.execute('UPDATE appointment_changes SET expires_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),change['id']))
        self.assertIsNone(self.current()['change'])
        self.assertEqual(self.action('psy',self.current(),'reschedule/respond','psychologist',change_id=change['id'],action='accept')[0],409)
        self.assertEqual(self.current()['start_at'],self.times[0])
        self.assertEqual(self.public()[0]['slots'],self.times[1:])

    def test_cancellation_closes_transfer_and_strangers_cannot_change(self):
        p,item=self.meeting()
        self.propose('client',item)
        current=self.current()
        self.client('stranger')
        self.psy('strangepsy')
        self.assertEqual(self.action('stranger',current,'cancel')[0],409)
        self.assertEqual(self.action('strangepsy',current,'cancel','psychologist')[0],409)
        self.assertEqual(self.action('client',current,'cancel')[0],200)
        self.assertIsNone(self.current()['change'])
        self.assertEqual(self.public()[0]['slots'],self.times)

    def test_occupied_target_and_failed_transfer_preserve_original(self):
        p,item=self.meeting()
        self.client('otherclient')
        other=self.request('otherclient','POST','/api/client/appointments',{'psychologist_id':p['id'],'start_at':self.times[1],'request_key':'other-client-booking'})
        self.assertEqual(other[0],200)
        self.assertEqual(self.propose('client',item)[0],409)
        self.assertEqual(self.current()['start_at'],self.times[0])
        self.propose('client',item,target=2)
        current=self.current()
        with server.connection() as db:
            db.execute('UPDATE slots SET active=0 WHERE profile_id=? AND start_at=?',(p['id'],self.times[2]))
        self.assertEqual(self.action('psy',current,'reschedule/respond','psychologist',change_id=current['change']['id'],action='accept')[0],409)
        self.assertEqual(self.current()['start_at'],self.times[0])

    def test_two_simultaneous_proposals_only_one_reserves_target(self):
        p,first=self.meeting()
        self.client('secondclient')
        second=self.request('secondclient','POST','/api/client/appointments',{'psychologist_id':p['id'],'start_at':self.times[1],'request_key':'second-client-booking'})[1]['appointment']
        second=self.request('psy','POST','/api/psychologist/appointments/decision',{'id':second['id'],'action':'confirm'})[1]['appointment']
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(self.propose,'client',first,target=2),pool.submit(self.propose,'secondclient',second,target=2)]
            self.assertEqual(sorted(f.result()[0] for f in futures),[200,409])
        self.assertEqual(self.current()['start_at'],self.times[0])
        self.assertEqual(self.appointments('secondclient')[0]['start_at'],self.times[1])

    def test_publication_preserves_old_fields_and_price_until_approval(self):
        p,item=self.meeting()
        p=self.request('psy','POST','/api/psychologist/profile',{'revision':p['revision'],'name':'Новое имя','price':4500,'contact':'private-new-contact'})[1]['profile']
        self.assertTrue(p['has_publication'])
        public=self.public()[0]
        self.assertEqual(public['price'],3000)
        self.assertNotEqual(public['name'],'Новое имя')
        self.assertNotIn('contact',public)
        p=self.submit(p)
        self.assertEqual(self.public()[0]['price'],3000)
        self.assertEqual(self.request('psy','POST','/api/psychologist/schedule',{'revision':p['revision'],'slots':self.times})[0],200)
        p=self.request('psy','GET','/api/psychologist/me')[1]['profile']
        p=self.review(p,'reject','Уточните сведения')[1]['profile']
        self.assertEqual(self.public()[0]['price'],3000)
        p=self.submit(p)
        p=self.review(p,'approve')[1]['profile']
        self.assertEqual(self.public()[0]['price'],4500)
        self.assertEqual(self.current()['price'],3000)
        self.assertEqual(self.current()['psychologist_name'],'Тестовый психолог')

    def test_draft_changes_do_not_affect_new_booking_price(self):
        p=self.ready()
        self.request('psy','POST','/api/psychologist/profile',{'revision':p['revision'],'price':25000})
        self.client()
        code,body=self.book(p)
        self.assertEqual(code,200,body)
        self.assertEqual(body['appointment']['price'],3000)
