"""Startup failures and privacy checks on disposable files and servers."""
from contextlib import closing
import os
import json
import signal
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import database
import diagnostics
import server
import test_server


class StartupTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.addCleanup(self.folder.cleanup)

    def launch(self, **values):
        env=os.environ|{'OTKLIK_DB':str(self.root/'app.sqlite3'),'OTKLIK_LOG_DIR':str(self.root/'logs'),**values}
        return subprocess.run([sys.executable,'-B',str(server.ROOT/'server.py')],env=env,capture_output=True,text=True,timeout=10)

    def test_invalid_port_has_actionable_message_without_traceback(self):
        result=self.launch(OTKLIK_PORT='invalid-private-setting')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('OTKLIK_PORT',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
        self.assertNotIn('invalid-private-setting',result.stderr)
        self.assertFalse((self.root/'app.sqlite3').exists())

    def test_occupied_port_is_reported_before_database_creation(self):
        with closing(socket.socket()) as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            result=self.launch(OTKLIK_PORT=str(listener.getsockname()[1]))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('уже занят',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
        self.assertFalse((self.root/'app.sqlite3').exists())

    def test_newer_schema_is_rejected_without_modifying_database(self):
        path=self.root/'future.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA user_version=999');db.commit()
        before=path.read_bytes()
        with self.assertRaises(ValueError):
            database.connect(path)
        self.assertEqual(path.read_bytes(),before)

    def free_port(self):
        with closing(socket.socket()) as listener:
            listener.bind(('127.0.0.1',0))
            return str(listener.getsockname()[1])

    def test_corrupt_and_future_database_fail_without_traceback_or_data_changes(self):
        path=self.root/'app.sqlite3'
        path.write_bytes(b'PRIVATE-CORRUPT-DATABASE-CONTENT')
        for future in (False,True):
            if future:
                path.unlink()
                with closing(sqlite3.connect(path)) as db:
                    db.execute('PRAGMA user_version=999');db.commit()
            before=path.read_bytes()
            result=self.launch(OTKLIK_PORT=self.free_port())
            self.assertNotEqual(result.returncode,0)
            self.assertIn('баз',result.stderr)
            self.assertNotIn('Traceback',result.stderr)
            self.assertNotIn('PRIVATE-',result.stderr)
            self.assertEqual(path.read_bytes(),before)
        records=[json.loads(line) for line in (self.root/'logs'/'otklik.log').read_text().splitlines()]
        self.assertEqual([item['event'] for item in records],['startup_failed','startup_failed'])

    def test_unusable_database_parent_and_log_directory_have_clear_errors(self):
        blocker=self.root/'blocked';blocker.write_text('keep')
        result=self.launch(OTKLIK_PORT=self.free_port(),OTKLIK_DB=str(blocker/'app.sqlite3'))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('OTKLIK_DB',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
        result=self.launch(OTKLIK_PORT=self.free_port(),OTKLIK_LOG_DIR=str(blocker))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('OTKLIK_LOG_DIR',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
        self.assertEqual(blocker.read_text(),'keep')

    def test_log_path_cannot_overwrite_database(self):
        directory=self.root/'logs';directory.mkdir()
        target=directory/'otklik.log';target.write_bytes(b'PRIVATE-DATABASE')
        result=self.launch(OTKLIK_PORT=self.free_port(),OTKLIK_DB=str(target))
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(target.read_bytes(),b'PRIVATE-DATABASE')
        self.assertNotIn('PRIVATE-DATABASE',result.stderr)
        alias=self.root/'alias.sqlite3';os.link(target,alias)
        result=self.launch(OTKLIK_PORT=self.free_port(),OTKLIK_DB=str(alias))
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(alias.read_bytes(),b'PRIVATE-DATABASE')

    def test_valid_start_and_keyboard_stop_are_logged_without_tracebacks(self):
        env=os.environ|{'OTKLIK_DB':str(self.root/'app.sqlite3'),'OTKLIK_PORT':self.free_port(),'OTKLIK_LOG_DIR':str(self.root/'logs')}
        process=subprocess.Popen([sys.executable,'-B',str(server.ROOT/'server.py')],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            # Wait for the explicit readiness line without an unbounded readline.
            import selectors
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout,selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=10),'Server did not report readiness')
            self.assertIn('Отклик: http://127.0.0.1:',process.stdout.readline())
            process.send_signal(signal.SIGINT)
            _,errors=process.communicate(timeout=10)
            self.assertEqual(process.returncode,0,errors)
            self.assertNotIn('Traceback',errors)
        finally:
            if process.poll() is None:
                process.kill();process.communicate(timeout=5)
        records=[json.loads(line) for line in (self.root/'logs'/'otklik.log').read_text().splitlines()]
        self.assertEqual([item['event'] for item in records],['startup_ready','startup_stopped'])


class RequestDiagnosticsTest(unittest.TestCase):
    setUp=test_server.WorkflowTest.setUp
    tearDown=test_server.WorkflowTest.tearDown
    request=test_server.WorkflowTest.request

    def test_exception_details_are_not_written_to_log(self):
        with self.assertLogs('otklik',level='ERROR') as captured, patch.object(server,'password_hash',side_effect=RuntimeError('PRIVATE-MESSAGE-AND-TOKEN')):
            code,body=self.request('anonymous','POST','/api/login',{'password':'private-password-value'})
        self.assertEqual(code,500)
        self.assertNotIn('PRIVATE-MESSAGE-AND-TOKEN','\n'.join(captured.output))
        self.assertNotIn('private-password-value',str(body))

    def test_get_failure_returns_reference_and_does_not_log_request_or_cookie(self):
        with self.assertLogs('otklik',level='ERROR') as captured, patch.object(server.Handler,'get_request',side_effect=RuntimeError('PRIVATE-EXCEPTION')):
            code,body=self.request('admin','GET','/api/status?secret=PRIVATE-QUERY')
        self.assertEqual(code,500)
        self.assertRegex(body['request_id'],'^[0-9a-f]{32}$')
        record=json.loads(captured.records[0].getMessage())
        self.assertEqual(record['request_id'],body['request_id'])
        self.assertEqual(record['method'],'GET')
        self.assertEqual(record['error_type'],'RuntimeError')
        self.assertNotIn('PRIVATE-',str(record))
        self.assertNotIn(self.cookies['admin'],str(record))

    def test_sqlite_failure_has_safe_503_and_reference(self):
        with self.assertLogs('otklik',level='ERROR') as captured, patch.object(server,'connection',side_effect=sqlite3.OperationalError('PRIVATE-SQL-AND-BODY')):
            code,body=self.request('admin','GET','/api/status')
        self.assertEqual(code,503)
        self.assertIn('request_id',body)
        self.assertNotIn('PRIVATE-',str(body)+'\n'.join(captured.output))


class LogFileTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(diagnostics.close)

    def test_private_json_log_rotates_and_never_formats_exception_values(self):
        path=diagnostics.configure(self.folder.name)
        handler=diagnostics.LOGGER.handlers[0];handler.maxBytes=500
        for _ in range(25):
            try:
                raise RuntimeError('PRIVATE-HEALTH-DATA-AND-PASSWORD')
            except RuntimeError as error:
                diagnostics.record('http_unexpected_error',error,'a'*32,'POST')
        files=list(Path(self.folder.name).glob('otklik.log*'))
        self.assertEqual(len(files),3)
        for file in files:
            self.assertEqual(file.stat().st_mode&0o777,0o600)
            self.assertLessEqual(file.stat().st_size,500)
            content=file.read_text()
            self.assertNotIn('PRIVATE-',content)
            for line in content.splitlines():
                self.assertEqual(json.loads(line)['error_type'],'RuntimeError')

    def test_log_write_failure_has_safe_console_message_and_does_not_raise(self):
        import io
        from contextlib import redirect_stderr
        diagnostics.configure(self.folder.name)
        handler=diagnostics.LOGGER.handlers[0]
        errors=io.StringIO()
        with patch.object(handler,'shouldRollover',side_effect=OSError('PRIVATE-PATH')),redirect_stderr(errors):
            diagnostics.record('startup_ready')
            diagnostics.record('startup_ready')
        self.assertEqual(errors.getvalue().count('Не удалось записать'),1)
        self.assertNotIn('PRIVATE-',errors.getvalue())

    def test_worker_error_does_not_use_default_traceback_output(self):
        with self.assertLogs('otklik',level='ERROR') as captured:
            try:
                raise RuntimeError('PRIVATE-WORKER-EXCEPTION')
            except RuntimeError:
                server.ThreadingHTTPServer.handle_error(None,None,('private-address',1))
        content='\n'.join(captured.output)
        self.assertIn('server_thread_error',content)
        self.assertNotIn('PRIVATE-',content)
        self.assertNotIn('private-address',content)
