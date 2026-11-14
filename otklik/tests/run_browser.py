"""Run browser tests against an isolated server and disposable SQLite database."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


def main():
    with tempfile.TemporaryDirectory(prefix='otklik-browser-') as folder:
        server.DB = Path(folder) / 'test.sqlite3'
        server.connection().close()
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        server.PORT = http.server_port
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        env = os.environ | {
            'OTKLIK_TEST_ORIGIN': f'http://127.0.0.1:{server.PORT}',
            'OTKLIK_BROWSER_TEST': 'isolated-server',
        }
        try:
            result = subprocess.run(['node', '--test', *sys.argv[1:], str(ROOT/'tests/browser.test.cjs')],
                                    cwd=ROOT, env=env, timeout=180)
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
