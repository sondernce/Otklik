"""Bounded local JSON logs. Never serialize requests or exception messages."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sqlite3
import sys
import traceback

LOGGER = logging.getLogger('otklik')
LOGGER.propagate = False
LOGGER.addHandler(logging.NullHandler())
EVENTS = {'startup_ready','startup_failed','startup_stopped','http_database_error',
          'http_unexpected_error','connection_lost','server_thread_error'}
FILES = {'server.py','database.py','access.py','booking.py','communications.py','notifications.py',
         'client_profiles.py','video_links.py','publications.py','timestamps.py','diagnostics.py'}
ERRORS = {'RuntimeError','ValueError','TypeError','OSError','PermissionError','FileNotFoundError',
          'NotADirectoryError','IsADirectoryError','OperationalError','DatabaseError','IntegrityError',
          'UnsupportedSchema','BrokenPipeError','ConnectionResetError','TimeoutError'}


class SafeHandler(RotatingFileHandler):
    def handleError(self, record):
        # logging's default handler can print a raw exception and its path to stderr.
        if not getattr(self,'reported_failure',False):
            self.reported_failure=True
            print('Не удалось записать журнал ошибок. Проверьте место на диске и права доступа.',file=sys.stderr)

    def _open(self):
        descriptor=os.open(self.baseFilename,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
        return os.fdopen(descriptor,self.mode,encoding=self.encoding,errors=self.errors)


def configure(directory):
    directory=Path(directory)
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=directory/'otklik.log'
    if path.is_symlink():
        raise ValueError('Журнал не должен быть символической ссылкой.')
    handler=SafeHandler(path,maxBytes=1024*1024,backupCount=2,encoding='utf-8')
    try:
        os.chmod(path,0o600)
    except OSError:
        handler.close()
        raise
    handler.setFormatter(logging.Formatter('%(message)s'))
    for previous in list(LOGGER.handlers):
        LOGGER.removeHandler(previous);previous.close()
    LOGGER.addHandler(handler);LOGGER.setLevel(logging.INFO)
    return path


def close():
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler);handler.close()
    LOGGER.addHandler(logging.NullHandler())


def record(event,error=None,request_id=None,method=None):
    if event not in EVENTS:
        raise ValueError('Unknown diagnostic event')
    data={'at':datetime.now(timezone.utc).isoformat(),'event':event}
    if isinstance(request_id,str) and re.fullmatch('[0-9a-f]{32}',request_id):
        data['request_id']=request_id
    if method in {'GET','POST'}:
        data['method']=method
    if error is not None:
        name=type(error).__name__
        data['error_type']=name if name in ERRORS else 'Exception'
        if isinstance(error,sqlite3.Error):
            code=getattr(error,'sqlite_errorcode',None)
            if type(code) is int:
                data['sqlite_code']=code
        data['frames']=[{'file':Path(frame.f_code.co_filename).name if Path(frame.f_code.co_filename).name in FILES else 'external',
                         'line':line} for frame,line in traceback.walk_tb(error.__traceback__)][-8:]
    level=logging.INFO if event in {'startup_ready','startup_stopped'} else logging.ERROR
    LOGGER.log(level,json.dumps(data,ensure_ascii=False))
