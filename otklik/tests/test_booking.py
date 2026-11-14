import concurrent.futures
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import test_server
import server
import booking


class BookingTest(unittest.TestCase):
    setUp = test_server.WorkflowTest.setUp
    tearDown = test_server.WorkflowTest.tearDown
    request = test_server.WorkflowTest.request
    psy = test_server.WorkflowTest.psy
    saved = test_server.WorkflowTest.saved
    submit = test_server.WorkflowTest.submit
    review = test_server.WorkflowTest.review
    slot = test_server.WorkflowTest.slot
    public = test_server.WorkflowTest.public

    def ready(self):
        p = self.submit(self.saved(self.psy()))
        p = self.review(p, 'approve')[1]['profile']
        return self.request('psy', 'POST', '/api/psychologist/schedule', {'revision': p['revision'], 'slots': [self.slot()]})[1]['profile']

    def client(self, who='client'):
        code, body = self.request(who, 'POST', '/api/client/register', {'email': who+'@test.invalid', 'name': 'Клиент '+who, 'password': 'client-password'})
        self.assertEqual(code, 200, body)
        return self.request(who, 'GET', '/api/client/me')[1]['client']

    def book(self, p, who='client', key='one-booking-request'):
        return self.request(who, 'POST', '/api/client/appointments', {'psychologist_id': p['id'], 'start_at': self.slot(), 'request_key': key, 'price': 1, 'status': 'confirmed'})

    def appointments(self, who='client', role='client'):
        return self.request(who, 'GET', f'/api/{role}/appointments')[1]['appointments']

    def test_manual_confirmation_and_idempotency(self):
        p = self.ready()
        self.client()
        code, body = self.book(p)
        self.assertEqual(code, 200, body)
        appointment = body['appointment']
        self.assertEqual(appointment['status'], 'pending')
        self.assertEqual(appointment['price'], 3000)
        self.assertAlmostEqual((datetime.fromisoformat(appointment['expires_at'])-datetime.now(timezone.utc)).total_seconds(), 12*3600, delta=10)
        self.assertEqual(self.public(), [])
        self.assertEqual(self.book(p)[1]['appointment']['id'], appointment['id'])
        self.assertEqual(len(self.appointments()), 1)
        self.assertEqual(self.appointments('psy', 'psychologist')[0]['client_name'], 'Клиент client')
        self.assertEqual(len(self.appointments('admin', 'admin')), 1)
        self.assertEqual(self.request('psy', 'POST', '/api/psychologist/appointments/decision', {'id': appointment['id'], 'action': 'confirm'})[0], 200)
        self.assertEqual(self.appointments()[0]['status'], 'confirmed')
        self.assertEqual(self.request('psy', 'POST', '/api/psychologist/schedule', {'revision': p['revision'], 'slots': []})[0], 409)

    def test_automatic_approval_and_restart(self):
        p = self.ready()
        self.client()
        self.assertEqual(self.request('psy', 'POST', '/api/psychologist/booking-settings', {'auto_approve': True})[0], 200)
        self.assertEqual(self.book(p)[1]['appointment']['status'], 'confirmed')
        # Client sessions, appointments and preferences are stored in SQLite.
        server.SESSIONS.clear()
        server.PSY_SESSIONS.clear()
        self.assertEqual(self.request('client', 'GET', '/api/client/me')[0], 200)
        self.assertEqual(self.appointments()[0]['status'], 'confirmed')
        self.assertEqual(self.public(), [])

    def test_parallel_booking_only_one_wins(self):
        p = self.ready()
        self.client('first')
        self.client('second')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda who: self.book(p, who), ['first', 'second']))
        self.assertEqual(sorted(code for code, _ in results), [200, 409])
        self.assertEqual(len(self.appointments('admin', 'admin')), 1)

    def test_expiration_decline_and_setting_affects_only_new_requests(self):
        p = self.ready()
        self.client()
        first = self.book(p)[1]['appointment']
        self.request('psy', 'POST', '/api/psychologist/booking-settings', {'auto_approve': True})
        self.assertEqual(self.appointments()[0]['status'], 'pending')
        with server.connection() as db:
            db.execute('UPDATE appointments SET expires_at=? WHERE id=?', ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), first['id']))
        self.assertEqual(len(self.public()), 1)
        self.assertEqual(self.appointments()[0]['status'], 'expired')
        self.assertEqual(self.request('psy', 'POST', '/api/psychologist/appointments/decision', {'id': first['id'], 'action': 'confirm'})[0], 409)
        second = self.book(p, key='second-booking-request')[1]['appointment']
        self.assertEqual(second['status'], 'confirmed')

    def test_decline_releases_slot_and_roles_are_isolated(self):
        p = self.ready()
        self.client()
        self.client('otherclient')
        other = self.psy('otherpsy')
        appointment = self.book(p)[1]['appointment']
        self.assertEqual(self.appointments('otherclient'), [])
        self.assertEqual(self.appointments('otherpsy', 'psychologist'), [])
        self.assertEqual(self.request('otherpsy', 'POST', '/api/psychologist/appointments/decision', {'id': appointment['id'], 'action': 'confirm'})[0], 409)
        self.assertEqual(self.request('client', 'GET', '/api/admin/appointments')[0], 401)
        self.assertEqual(self.request('psy', 'POST', '/api/client/appointments', {})[0], 401)
        self.assertEqual(self.request('client', 'POST', '/api/psychologist/booking-settings', {'auto_approve': True})[0], 401)
        self.assertEqual(self.request('psy', 'POST', '/api/psychologist/appointments/decision', {'id': appointment['id'], 'action': 'decline'})[0], 200)
        self.assertEqual(len(self.public()), 1)
        self.assertNotIn('client_name', self.public()[0])
        self.assertEqual(self.request('client', 'POST', '/api/client/logout', {})[0], 200)
        self.assertEqual(self.request('client', 'GET', '/api/client/me')[0], 401)
        self.assertEqual(self.request('client', 'POST', '/api/client/login', {'email': 'CLIENT@test.invalid', 'password': 'client-password'})[0], 200)

    def test_client_overlap_across_psychologists_and_hidden_profiles(self):
        p = self.ready()
        self.client()
        self.book(p)
        other = self.psy('otherpsy')
        other = self.saved(other, 'otherpsy')
        other = self.request('otherpsy', 'POST', '/api/psychologist/submit', {'revision': other['revision']})[1]['profile']
        other = self.review(other, 'approve')[1]['profile']
        other = self.request('otherpsy', 'POST', '/api/psychologist/schedule', {'revision': other['revision'], 'slots': [self.slot()]})[1]['profile']
        self.assertEqual(self.book(other, key='overlapping-booking')[0], 409)
        self.review(other, 'hide')
        self.client('otherclient')
        self.assertEqual(self.book(other, 'otherclient')[0], 409)

    def test_repeated_parallel_request_and_archive_preserve_appointment(self):
        p = self.ready()
        self.client()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.book(p), range(2)))
        self.assertEqual([code for code, _ in results], [200, 200])
        self.assertEqual(results[0][1]['appointment']['id'], results[1][1]['appointment']['id'])
        self.review(p, 'delete')
        self.assertEqual(len(self.appointments()), 1)
        self.assertEqual(self.appointments()[0]['status'], 'pending')
        self.assertEqual(self.public(), [])

    def test_manual_deadline_is_capped_by_start(self):
        p = self.ready()
        start = (datetime.now(timezone.utc)+timedelta(minutes=30)).replace(second=0, microsecond=0).isoformat()
        code, body = self.request('psy', 'POST', '/api/psychologist/schedule', {'revision': p['revision'], 'slots': [start]})
        self.assertEqual(code, 200, body)
        self.client()
        code, body = self.request('client', 'POST', '/api/client/appointments', {'psychologist_id': p['id'], 'start_at': start, 'request_key': 'near-start-booking'})
        self.assertEqual(code, 200, body)
        self.assertEqual(body['appointment']['expires_at'], start)


class MigrationTest(unittest.TestCase):
    def test_v1_appointments_and_accounts_survive_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'version-one.sqlite3'
            start=(datetime.now(timezone.utc)+timedelta(days=2)).replace(second=0,microsecond=0)
            end=start+timedelta(minutes=50)
            p={'id':'psy','owner_id':'owner','status':'approved','name':'Прежнее имя','price':3000,'slots':[start.isoformat()],'contact':'private'}
            with closing(sqlite3.connect(path)) as db:
                db.executescript('''
                    CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE profiles(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                    CREATE TABLE psychologists(id TEXT PRIMARY KEY,email TEXT UNIQUE,salt TEXT,hash TEXT);
                    CREATE TABLE clients(id TEXT PRIMARY KEY,email TEXT UNIQUE,name TEXT,salt TEXT,hash TEXT);
                    CREATE TABLE client_sessions(token_hash TEXT PRIMARY KEY,client_id TEXT,expires REAL);
                    CREATE TABLE booking_settings(profile_id TEXT PRIMARY KEY,auto_approve INTEGER);
                    CREATE TABLE slots(id TEXT PRIMARY KEY,profile_id TEXT,start_at TEXT,end_at TEXT,active INTEGER,UNIQUE(profile_id,start_at));
                    CREATE TABLE appointments(id TEXT PRIMARY KEY,slot_id TEXT,client_id TEXT,profile_id TEXT,start_at TEXT,end_at TEXT,price INTEGER,psychologist_name TEXT,status TEXT,created_at TEXT,expires_at TEXT,request_key TEXT,UNIQUE(client_id,request_key));
                    CREATE UNIQUE INDEX one_active_booking ON appointments(slot_id) WHERE status IN ('pending','confirmed');
                    PRAGMA user_version=1;
                ''')
                db.execute('INSERT INTO profiles VALUES(?,?)',('psy',json.dumps(p)))
                db.execute('INSERT INTO psychologists VALUES(?,?,?,?)',('owner','psy@test.invalid','salt','hash'))
                db.execute('INSERT INTO clients VALUES(?,?,?,?,?)',('client','client@test.invalid','Клиент','salt','hash'))
                db.execute('INSERT INTO client_sessions VALUES(?,?,?)',('session-hash','client',9999999999))
                db.execute('INSERT INTO slots VALUES(?,?,?,?,1)',('slot','psy',start.isoformat(),end.isoformat()))
                db.execute('INSERT INTO appointments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('meeting','slot','client','psy',start.isoformat(),end.isoformat(),3000,'Прежнее имя','confirmed',booking.now(),None,'original-request-key'))
                db.commit()
            with booking.connect(path) as db:
                item=booking.listings(db,'client','client')[0]
                self.assertEqual(item['id'],'meeting')
                self.assertEqual(item['status'],'confirmed')
                self.assertEqual(item['revision'],0)
                self.assertEqual(booking.available_slots(db,'psy'),[])
                self.assertEqual(booking.publication(db,'psy')['price'],3000)
                self.assertNotIn('contact',booking.publication(db,'psy'))
                self.assertEqual(db.execute('SELECT token_hash FROM client_sessions').fetchone()[0],'session-hash')
            backups=list((path.parent/'backups').iterdir())
            self.assertEqual(len(backups),1)
            with closing(sqlite3.connect(backups[0])) as original:
                self.assertEqual(original.execute('PRAGMA user_version').fetchone()[0],1)
                self.assertEqual(original.execute('SELECT count(*) FROM appointments').fetchone()[0],1)

    def test_existing_profile_and_schedule_are_preserved_and_backed_up(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'legacy.sqlite3'
            start = (datetime.now(timezone.utc)+timedelta(days=5)).replace(second=0, microsecond=0).isoformat()
            profile = {'id': 'legacy', 'owner_id': 'oldowner', 'name': 'Существующий психолог', 'education': 'Вуз', 'slots': [start], 'status': 'approved'}
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE profiles(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            db.execute('INSERT INTO profiles VALUES(?,?)', ('legacy', json.dumps(profile)))
            db.commit()
            db.close()
            with booking.connect(path) as db:
                self.assertEqual(json.loads(db.execute('SELECT data FROM profiles').fetchone()[0]), profile)
                self.assertEqual(booking.available_slots(db, 'legacy'), [start])
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], booking.SCHEMA_VERSION)
            self.assertEqual(len(list((path.parent/'backups').iterdir())), 1)
            with booking.connect(path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM slots').fetchone()[0], 1)
            self.assertEqual(len(list((path.parent/'backups').iterdir())), 1)
