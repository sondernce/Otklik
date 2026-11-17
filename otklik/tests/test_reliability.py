"""Failure, recovery and concurrency checks using disposable databases only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import booking
import database
import server
import test_changes


class ReliabilityTest(unittest.TestCase):
    # Reuse fixtures, without inheriting and rerunning the other test cases.
    for _name in ('setUp', 'tearDown', 'request', 'psy', 'saved', 'submit', 'review',
                  'slot', 'public', 'ready', 'client', 'book', 'appointments',
                  'meeting', 'action', 'propose', 'current'):
        locals()[_name] = getattr(test_changes.ChangesTest, _name)

    def test_expired_session_cannot_read_or_write_and_login_restores_access(self):
        p, item = self.meeting()
        with server.connection() as db:
            db.execute('UPDATE client_sessions SET expires=0')
        self.assertEqual(self.request('client', 'GET', '/api/client/appointments')[0], 401)
        self.assertEqual(self.action('client', item, 'cancel')[0], 401)
        self.assertEqual(self.request('client', 'POST', '/api/client/login', {
            'email': 'client@test.invalid', 'password': 'client-password'})[0], 200)
        self.assertEqual(self.current()['id'], item['id'])
        self.assertEqual(self.current()['status'], 'confirmed')

    def test_bad_http_requests_have_no_side_effects(self):
        p = self.ready()
        self.client()
        cases = [
            ({'Content-Type': 'application/json'}, '{}', 403),
            ({'Content-Type': 'text/plain', 'Origin': f'http://127.0.0.1:{server.PORT}'}, '{}', 415),
            ({'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.PORT}'}, '[]', 400),
            ({'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.PORT}'}, '{broken', 400),
            ({'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.PORT}', 'Content-Length': '3000001'}, '{}', 413),
        ]
        for headers, body, expected in cases:
            with self.subTest(expected=expected, body=body):
                with closing(http.client.HTTPConnection('127.0.0.1', server.PORT)) as conn:
                    conn.request('POST', '/api/client/appointments', body, headers | {'Cookie': self.cookies['client']})
                    response = conn.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
        self.assertEqual(self.appointments(), [])
        self.assertEqual(len(self.public()[0]['slots']), 1)

    def test_invalid_booking_timestamps_do_not_create_appointments(self):
        p = self.ready()
        self.client()
        for start in ['invalid', '2099-01-01T12:00:00', '2099-01-01T12:00:01Z',
                      '2000-01-01T12:00:00Z', '2099-01-01T12:00:00Z', None]:
            with self.subTest(start=start):
                code, _ = self.request('client', 'POST', '/api/client/appointments', {
                    'psychologist_id': p['id'], 'start_at': start, 'request_key': 'invalid-time-request'})
                self.assertIn(code, {400, 409})
        self.assertEqual(self.appointments(), [])

    def test_cancellation_racing_transfer_acceptance_has_one_consistent_result(self):
        _, item = self.meeting()
        self.propose('client', item)
        current = self.current()
        with ThreadPoolExecutor(2) as pool:
            cancel = pool.submit(self.action, 'client', current, 'cancel')
            accept = pool.submit(self.action, 'psy', current, 'reschedule/respond',
                                 'psychologist', change_id=current['change']['id'], action='accept')
            self.assertEqual(sorted([cancel.result()[0], accept.result()[0]]), [200, 409])
        final = self.current()
        self.assertIsNone(final['change'])
        self.assertIn(final['status'], {'confirmed', 'cancelled'})
        if final['status'] == 'cancelled':
            self.assertEqual(final['start_at'], self.times[0])
            self.assertEqual(self.public()[0]['slots'], self.times)
        else:
            self.assertEqual(final['start_at'], self.times[1])
            self.assertEqual(self.public()[0]['slots'], [self.times[0], self.times[2]])

    def test_database_busy_returns_retryable_error_without_partial_booking(self):
        p = self.ready()
        self.client()
        original = server.connection

        def short_timeout():
            db = original()
            db.execute('PRAGMA busy_timeout=5')
            return db

        with closing(sqlite3.connect(server.DB)) as lock:
            lock.execute('BEGIN IMMEDIATE')
            with patch.object(server, 'connection', short_timeout), patch.object(server.diagnostics, 'record'):
                self.assertEqual(self.book(p)[0], 503)
            lock.rollback()
        self.assertEqual(self.appointments(), [])
        self.assertEqual(self.book(p)[0], 200)
        self.assertEqual(len(self.appointments()), 1)

    def test_exception_halfway_through_transfer_rolls_back_every_write(self):
        _, item = self.meeting()
        self.propose('client', item)
        current = self.current()
        with patch.object(booking, 'audit', side_effect=RuntimeError('simulated storage failure')), patch.object(server.diagnostics, 'record'):
            self.assertEqual(self.action('psy', current, 'reschedule/respond', 'psychologist',
                                        change_id=current['change']['id'], action='accept')[0], 500)
        restored = self.current()
        self.assertEqual(restored['start_at'], self.times[0])
        self.assertEqual(restored['revision'], current['revision'])
        self.assertEqual(restored['change']['id'], current['change']['id'])
        self.assertFalse(any(event['action'] == 'rescheduled' for event in restored['history']))
        self.assertEqual(self.action('psy', restored, 'reschedule/respond', 'psychologist',
                                    change_id=restored['change']['id'], action='accept')[0], 200)
        self.assertEqual(self.current()['start_at'], self.times[1])

    def test_backup_can_restore_sessions_publication_and_pending_transfer(self):
        p, item = self.meeting()
        self.propose('client', item)
        before = self.current()
        original = server.DB
        restored = Path(self.temp.name) / 'restored.sqlite3'
        with server.connection() as source, closing(sqlite3.connect(restored)) as destination:
            source.backup(destination)
        server.DB = restored
        self.assertEqual(self.current(), before)
        self.assertEqual(self.public()[0]['price'], 3000)
        self.assertEqual(self.action('psy', before, 'reschedule/respond', 'psychologist',
                                    change_id=before['change']['id'], action='accept')[0], 200)
        self.assertEqual(self.current()['start_at'], self.times[1])
        with server.connection() as db:
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        server.DB = original
        self.assertEqual(self.current(), before)


class MigrationRecoveryTest(unittest.TestCase):
    def test_failed_migration_rolls_back_and_is_safe_to_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'old.sqlite3'
            start = (datetime.now(timezone.utc)+timedelta(days=2)).replace(second=0, microsecond=0).isoformat()
            profile = {'id': 'old', 'name': 'Прежний профиль', 'status': 'approved', 'slots': [start]}
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE profiles(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
                db.execute('INSERT INTO profiles VALUES(?,?)', ('old', json.dumps(profile)))
                db.commit()
            with patch.object(database, 'publish', side_effect=RuntimeError('simulated migration failure')):
                with self.assertRaises(RuntimeError):
                    booking.connect(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
                self.assertEqual(json.loads(db.execute('SELECT data FROM profiles').fetchone()[0]), profile)
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='appointments'").fetchone())
                self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            self.assertEqual(len(list((path.parent/'backups').iterdir())), 1)
            with booking.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], booking.SCHEMA_VERSION)
                self.assertEqual(booking.available_slots(db, 'old'), [start])
                self.assertEqual(booking.publication(db, 'old')['name'], profile['name'])
