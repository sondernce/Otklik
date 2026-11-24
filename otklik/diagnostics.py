"""Bounded local JSON logs. Never serialize requests or exception messages."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import traceback

LOGGER = logging.getLogger('otklik')
LOGGER.propagate = False
LOGGER.addHandler(logging.NullHandler())
EVENTS = {'startup_ready','startup_failed','startup_stopped','http_database_error',
          'http_unexpected_error','connection_lost','server_thread_error',
          'security_login_failed','security_limited','security_denied'}
OPERATIONS={'login','registration','profiles','appointments','support','notifications','messages','monitoring','other'}
FILES = {'server.py','database.py','access.py','booking.py','communications.py','notifications.py',
         'client_profiles.py','video_links.py','publications.py','timestamps.py','diagnostics.py','admin_monitor.py','maintenance.py','accounts.py','outcomes.py','email_access.py','mail_queue.py','mail_worker.py'}
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


def record(event,error=None,request_id=None,method=None,role=None,operation=None):
    if event not in EVENTS:
        raise ValueError('Unknown diagnostic event')
    data={'at':datetime.now(timezone.utc).isoformat(),'event':event,'event_id':secrets.token_hex(16)}
    if role in {'admin','client','psychologist'}:data['role']=role
    if operation in OPERATIONS:data['operation']=operation
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


def entries(directory):
    """Read only bounded rotated files and return an allowlisted representation."""
    import hashlib
    events=[];available=False;partial=False
    for name in ('otklik.log','otklik.log.1','otklik.log.2'):
        path=Path(directory)/name
        try:
            if path.is_symlink():partial=True;continue
            if not path.exists():continue
            if path.stat().st_size>1024*1024+8192:partial=True;continue
            with path.open('rb') as handle:raw=handle.read(1024*1024+8193)
            if len(raw)>1024*1024+8192:partial=True;continue
            available=True
            for line in raw.splitlines():
                try:
                    item=json.loads(line)
                    if not isinstance(item,dict) or item.get('event') not in EVENTS:raise ValueError()
                    stamp=datetime.fromisoformat(item['at'])
                    if stamp.tzinfo is None:raise ValueError()
                    at=stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')
                    kind=item['event'];security=kind.startswith('security_')
                    category='security' if security else ('platform' if kind in ('startup_ready','startup_stopped') else 'errors')
                    details={}
                    if item.get('error_type') in ERRORS|{'Exception'}:details['error_type']=item['error_type']
                    frames=item.get('frames',[])
                    if isinstance(frames,list):
                        details['frames']=[{'file':f['file'],'line':f['line']} for f in frames[:8] if isinstance(f,dict) and f.get('file') in FILES|{'external'} and type(f.get('line')) is int and 0<f['line']<10**7]
                    if item.get('method') in ('GET','POST'):details['method']=item['method']
                    if item.get('operation') in OPERATIONS:details['operation']=item['operation']
                    ident=item.get('event_id')
                    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):ident=hashlib.sha256(line).hexdigest()[:32]
                    event={'id':'d:'+ident,'created_at':at,'category':category,'kind':kind,'actor':item.get('role') if item.get('role') in ('admin','client','psychologist') else 'system','target_kind':'','target_id':'','result':('limited' if kind=='security_limited' else 'denied') if security else ('success' if category=='platform' else 'failure'),'details':details}
                    if isinstance(item.get('request_id'),str) and re.fullmatch('[0-9a-f]{32}',item['request_id']):event['request_id']=item['request_id']
                    events.append(event)
                except (ValueError,KeyError,TypeError,UnicodeError,OverflowError):partial=True
        except OSError:partial=True
    events.sort(key=lambda e:(e['created_at'],e['id']),reverse=True)
    return {'events':events,'available':available,'partial':partial}
