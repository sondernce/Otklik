"""Run browser tests against an isolated server and disposable SQLite database."""
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


class BrowserHandler(server.Handler):
    # This subclass exists only on the disposable loopback test server.
    def visitor(self):
        marker=self.headers.get('X-Otklik-Test-IP','')
        if re.fullmatch('[0-9a-f-]{36}',marker):self.client_address=('browser-'+marker,self.client_address[1])
    def do_GET(self):
        self.visitor();return super().do_GET()
    def do_POST(self):
        self.visitor();return super().do_POST()


def main():
    os.environ['OTKLIK_MAIL_TRANSPORT']='local'
    with tempfile.TemporaryDirectory(prefix='otklik-browser-') as folder:
        server.DB = Path(folder) / 'test.sqlite3'
        server.connection().close()
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), BrowserHandler)
        server.PORT = http.server_port
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        env = os.environ | {
            'OTKLIK_TEST_ORIGIN': f'http://127.0.0.1:{server.PORT}',
            'OTKLIK_BROWSER_TEST': 'isolated-server',
            'OTKLIK_TEST_PYTHON':sys.executable,
            'OTKLIK_TEST_DATABASE':str(server.DB),
        }
        try:
            result = subprocess.run(['node', '--test', *sys.argv[1:], str(ROOT/'tests/browser.test.cjs')],
                                    cwd=ROOT, env=env, timeout=300)
            return result.returncode
        except FileNotFoundError:
            print('Для браузерных проверок требуется Node.js.', file=sys.stderr)
            return 1
        finally:
            http.shutdown()
            http.server_close()
            thread.join()


if __name__ == '__main__':
    raise SystemExit(main())
