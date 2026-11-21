"""Explicit local previews or encrypted SMTP delivery, never invoked by HTTP requests."""
import argparse
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from datetime import datetime
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import tempfile

import database
import mail_queue


class DeliveryError(Exception):
    def __init__(self,outcome,code):
        self.outcome,self.code=outcome,code


def message(item,sender):
    msg=EmailMessage(policy=SMTP)
    msg['From']=mail_queue.address(sender);msg['To']=item['recipient'];msg['Subject']=item['subject']
    msg['Message-ID']=f"<{item['id']}@{sender.rsplit('@',1)[1]}>"
    msg['Date']=format_datetime(datetime.fromisoformat(item['created_at']))
    msg.set_content(item['body'])
    return msg


def smtp_config(env):
    sender=mail_queue.address(env.get('OTKLIK_MAIL_FROM',''))
    host=env.get('OTKLIK_SMTP_HOST','');security=env.get('OTKLIK_SMTP_SECURITY','ssl')
    if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',host) or security not in ('ssl','starttls'):
        raise ValueError('Укажите SMTP-сервер и защищённый режим ssl или starttls.')
    try:port=int(env.get('OTKLIK_SMTP_PORT','465' if security=='ssl' else '587'))
    except ValueError:raise ValueError('Проверьте порт SMTP.') from None
    user,password=env.get('OTKLIK_SMTP_USER',''),env.get('OTKLIK_SMTP_PASSWORD','')
    if not 1<=port<=65535 or not user or not password:
        raise ValueError('Укажите порт, пользователя и пароль SMTP через окружение.')
    return {'sender':sender,'host':host,'port':port,'security':security,'user':user,'password':password}


def submit_smtp(item,config):
    client=None;data_started=False
    try:
        context=ssl.create_default_context()
        if config['security']=='ssl':client=smtplib.SMTP_SSL(config['host'],config['port'],timeout=15,context=context)
        else:
            client=smtplib.SMTP(config['host'],config['port'],timeout=15)
            client.ehlo();client.starttls(context=context);client.ehlo()
        client.login(config['user'],config['password'])
        code,_=client.mail(config['sender'])
        if code!=250:raise smtplib.SMTPResponseException(code,b'')
        code,_=client.rcpt(item['recipient'])
        if code not in (250,251):raise smtplib.SMTPResponseException(code,b'')
        data_started=True
        code,_=client.data(message(item,config['sender']).as_bytes())
        if code!=250:raise smtplib.SMTPResponseException(code,b'')
    except smtplib.SMTPResponseException as error:
        raise DeliveryError('retry' if 400<=error.smtp_code<500 else 'failed','smtp_temporary' if 400<=error.smtp_code<500 else 'smtp_rejected') from None
    except (OSError,smtplib.SMTPException):
        raise DeliveryError('unknown' if data_started else 'retry','delivery_unknown' if data_started else 'connection_failed') from None
    finally:
        if client:
            # A missing QUIT response after acknowledged DATA must not cause a resend.
            try:client.close()
            except (OSError,smtplib.SMTPException):pass


def preview(item,directory):
    directory=Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    content=message(item,'preview@otklik.invalid').as_bytes()
    target=directory/(item['id']+'.eml')
    descriptor,name=tempfile.mkstemp(prefix='.mail-',dir=directory)
    try:
        with os.fdopen(descriptor,'wb') as output:output.write(content);output.flush();os.fsync(output.fileno())
        try:os.link(name,target)
        except FileExistsError:
            if target.is_symlink() or target.stat().st_size!=len(content) or target.read_bytes()!=content:raise OSError('Preview conflict')
    finally:Path(name).unlink(missing_ok=True)


def run_once(path,mode='local',limit=20,env=None):
    if mode not in ('local','smtp') or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('Проверьте режим и размер пакета писем.')
    config=smtp_config(os.environ if env is None else env) if mode=='smtp' else None
    results={}
    for _ in range(limit):
        with database.connect(path) as db:item=mail_queue.claim(db,mode)
        if item is None:break
        try:
            if mode=='local':preview(item,Path(path).parent/'mail-preview');outcome='preview'
            else:submit_smtp(item,config);outcome='sent'
            error=None
        except DeliveryError as failure:outcome,error=failure.outcome,failure.code
        except Exception:
            # Unknown local failures are retryable; unexpected SMTP failures may be ambiguous.
            outcome,error=('retry','preview_failed') if mode=='local' else ('unknown','delivery_unknown')
        with database.connect(path) as db:saved=mail_queue.finish(db,item,outcome,error)
        if not saved:raise RuntimeError('Состояние отправки изменилось; повторная отправка не выполнялась.')
        results[outcome]=results.get(outcome,0)+1
    return results


def main(argv=None):
    parser=argparse.ArgumentParser(description='Обработать очередь почты. По умолчанию только локальные .eml, без отправки.')
    parser.add_argument('--database',default=os.environ.get('OTKLIK_DB',str(Path(__file__).parent/'.local'/'otklik.sqlite3')))
    parser.add_argument('--mode',choices=('local','smtp'),default='local')
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--status',action='store_true',help='Показать безопасную сводку без обработки очереди')
    args=parser.parse_args(argv)
    try:
        path=Path(args.database).resolve(strict=True)
        if args.status:
            with database.connect(path) as db:result=mail_queue.summary(db)
        else:result=run_once(path,args.mode,args.limit)
    except Exception:
        parser.exit(1,'Не удалось обработать очередь почты. Проверьте настройки, базу и доступ к файлам.\n')
    print(json.dumps({'mode':args.mode,'processed':result},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
