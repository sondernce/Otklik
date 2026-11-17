"""Snapshots, restore safety and restored application data on disposable databases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import backup
import database
import server
import test_communications


class BackupTest(unittest.TestCase):
    for _name in ('setUp','tearDown','request','psy','saved','submit','review','slot','public','ready','client','book','appointments',
                  'conversation','send','feed','ticket','support_send','support_feed'):
        locals()[_name] = getattr(test_communications.CommunicationsTest,_name)

    def output(self, name='snapshot.sqlite3'):
        return server.DB.parent/name

    def test_complete_restore_keeps_appointments_dialogues_support_and_video_but_revokes_sessions(self):
        _,item=self.conversation()
        current=self.request('psy','POST','/api/psychologist/appointments/decision',{'id':item['id'],'action':'confirm'})[1]['appointment']
        self.request('psy','POST','/api/psychologist/appointments/video-link',{'id':item['id'],'revision':current['revision'],'url':'https://example.test/meeting'})
        self.send(item['id']);self.send(item['id'],'psy','psychologist')
        ticket=self.ticket()[1]['ticket'];self.support_send(ticket['id'])
        original=Path(server.DB)
        snapshot=self.output();restored=self.output('restored.sqlite3')
        metadata=backup.snapshot(original,snapshot)
        self.assertEqual(metadata['appointments'],1)
        self.assertEqual(backup.verify(snapshot)['sha256'],metadata['sha256'])
        backup.snapshot(snapshot,restored,restore=True)
        server.DB=restored
        for who,path in [('client','/api/client/me'),('psy','/api/psychologist/me'),('admin','/api/admin/profiles')]:
            self.assertEqual(self.request(who,'GET',path)[0],401)
        self.assertEqual(self.request('client','POST','/api/client/login',{'email':'client@test.invalid','password':'client-password'})[0],200)
        self.assertEqual(self.appointments()[0]['video_url'],'https://example.test/meeting')
        self.assertEqual(len(self.feed(item['id'])[1]['messages']),2)
        self.assertEqual(len(self.support_feed(ticket['id'])[1]['messages']),2)
        self.assertGreater(self.request('client','GET','/api/client/notifications')[1]['unread_count'],0)
        with closing(sqlite3.connect(original)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0],1)
        self.assertEqual(snapshot.stat().st_mode&0o777,0o600)
        self.assertEqual(restored.stat().st_mode&0o777,0o600)
        server.DB=original

    def test_live_wal_snapshot_includes_commits_and_excludes_uncommitted_changes(self):
        with closing(sqlite3.connect(server.DB)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO settings VALUES('snapshot-test','committed')");writer.commit()
            writer.execute("UPDATE settings SET value='uncommitted' WHERE key='snapshot-test'")
            target=self.output()
            backup.snapshot(server.DB,target)
            with closing(backup.readonly(target)) as db:
                self.assertEqual(db.execute("SELECT value FROM settings WHERE key='snapshot-test'").fetchone()[0],'committed')
            writer.rollback()

    def test_existing_destination_and_source_are_never_replaced(self):
        target=self.output();target.write_bytes(b'keep-existing-file')
        for destination in (target,server.DB):
            with self.assertRaises(FileExistsError):
                backup.snapshot(server.DB,destination,restore=True)
        self.assertEqual(target.read_bytes(),b'keep-existing-file')
        self.assertEqual(backup.verify(server.DB)['schema_version'],database.SCHEMA_VERSION)

    def test_missing_corrupt_foreign_key_broken_and_future_sources_are_rejected(self):
        missing=self.output('missing.sqlite3')
        with self.assertRaises(FileNotFoundError):
            backup.snapshot(missing,self.output())
        self.assertFalse(missing.exists())
        corrupt=self.output('corrupt.sqlite3');corrupt.write_bytes(b'not a sqlite database')
        with self.assertRaises(sqlite3.DatabaseError):
            backup.snapshot(corrupt,self.output())
        with closing(sqlite3.connect(server.DB)) as db:
            db.execute('PRAGMA user_version=999');db.commit()
        with self.assertRaises(ValueError):
            backup.snapshot(server.DB,self.output())
        with closing(sqlite3.connect(server.DB)) as db:
            db.execute('PRAGMA user_version=6')
            db.execute("INSERT INTO client_details VALUES('missing-client',20)");db.commit()
        with self.assertRaises(ValueError):
            backup.snapshot(server.DB,self.output())
        self.assertFalse(self.output().exists())
        self.assertEqual(list(server.DB.parent.glob('.otklik-snapshot-*')),[])

    def test_snapshot_failure_does_not_publish_partial_file(self):
        with patch.object(backup,'verify',side_effect=OSError('simulated read failure')):
            with self.assertRaises(OSError):
                backup.snapshot(server.DB,self.output())
        self.assertFalse(self.output().exists())
        self.assertEqual(list(server.DB.parent.glob('.otklik-snapshot-*')),[])
        self.assertEqual(self.request('admin','GET','/api/admin/profiles')[0],200)

    def test_concurrent_snapshots_publish_only_one_complete_destination(self):
        def create(_):
            try:
                backup.snapshot(server.DB,self.output())
                return 'created'
            except FileExistsError:
                return 'exists'
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(create,range(2))),['created','exists'])
        self.assertEqual(backup.verify(self.output())['schema_version'],6)
        self.assertEqual(list(server.DB.parent.glob('.otklik-snapshot-*')),[])

    def test_restore_upgrades_v5_copy_without_modifying_source(self):
        self.client()
        with server.connection() as db:
            for table in ('admin_sessions','psychologist_sessions','login_attempts'):
                db.execute(f'DROP TABLE {table}')
            db.execute('DROP INDEX client_session_expiry')
            db.execute('PRAGMA user_version=5')
        restored=self.output('restored.sqlite3')
        self.assertEqual(backup.snapshot(server.DB,restored,restore=True)['schema_version'],6)
        self.assertEqual(backup.verify(server.DB)['schema_version'],5)
        with closing(backup.readonly(restored)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM client_sessions').fetchone()[0],0)

    def test_cli_create_verify_restore_and_existing_destination_error(self):
        target=self.output('copy with spaces.sqlite3');restored=self.output('restored.sqlite3')
        for args in (['create','--database',str(server.DB),'--output',str(target)],['verify',str(target)],
                     ['restore','--backup',str(target),'--output',str(restored)]):
            result=subprocess.run([sys.executable,'-B',str(server.ROOT/'backup.py'),*args],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('sha256',result.stdout)
        result=subprocess.run([sys.executable,'-B',str(server.ROOT/'backup.py'),'restore','--backup',str(target),'--output',str(restored)],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('уже существует',result.stderr)
