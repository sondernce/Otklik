"""Private operational overview and transactional event history."""
import base64
from datetime import datetime, timedelta, timezone, time as day_time
import json
from pathlib import Path
import shutil
import time
from zoneinfo import ZoneInfo

import diagnostics
import maintenance

VERSION = '0.7.0'
STARTED = time.monotonic()
STAMP = "strftime('%Y-%m-%dT%H:%M:%f','now')||'000+00:00'"
PROFILE_FIELDS = ('name','gender','price','experience','style','approach','bio','education','topics','photo','contact','status','hidden','deleted','slots')


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS account_registry (role TEXT NOT NULL, account_id TEXT NOT NULL, registered_at TEXT, PRIMARY KEY(role,account_id))')
    for role, table in [('client','clients'),('psychologist','psychologists')]:
        db.execute(f"INSERT OR IGNORE INTO account_registry SELECT '{role}',id,NULL FROM {table}")
    db.execute('CREATE INDEX IF NOT EXISTS account_registration_time ON account_registry(registered_at,role)')
    db.execute('CREATE TABLE IF NOT EXISTS admin_events (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, category TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL, target_kind TEXT NOT NULL, target_id TEXT NOT NULL, result TEXT NOT NULL, details TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS admin_event_feed ON admin_events(created_at,id)')
    db.execute('CREATE INDEX IF NOT EXISTS admin_event_kind ON admin_events(category,kind,created_at,id)')

    def insert(kind, actor, target, ident, details="'{}'", category="'platform'"):
        return f"INSERT INTO admin_events(created_at,category,kind,actor,target_kind,target_id,result,details) VALUES({STAMP},{category},{kind},{actor},'{target}',{ident},'success',{details});"
    for role, table in [('client','clients'),('psychologist','psychologists')]:
        extra = insert("'registered'",f"'{role}'",'client','NEW.id') if role=='client' else ''
        db.execute(f"CREATE TRIGGER IF NOT EXISTS monitor_register_{role} AFTER INSERT ON {table} BEGIN INSERT INTO account_registry VALUES('{role}',NEW.id,{STAMP});{extra} END")
    db.execute("CREATE TRIGGER IF NOT EXISTS monitor_profile_created AFTER INSERT ON profiles BEGIN " + insert("'registered'","'psychologist'",'profile','NEW.id')+' END')
    actor = "CASE json_extract(NEW.data,'$.history[#-1].actor') WHEN 'Администратор' THEN 'admin' ELSE 'psychologist' END"
    # Text edits log field names only, never the values or the review note.
    changed = "json_array("+','.join(f"CASE WHEN json_extract(OLD.data,'$.{field}') IS NOT json_extract(NEW.data,'$.{field}') THEN '{field}' END" for field in PROFILE_FIELDS)+")"
    details = f"json_object('before',json_object('status',json_extract(OLD.data,'$.status'),'hidden',json_extract(OLD.data,'$.hidden'),'deleted',json_extract(OLD.data,'$.deleted'),'price',json_extract(OLD.data,'$.price')),'after',json_object('status',json_extract(NEW.data,'$.status'),'hidden',json_extract(NEW.data,'$.hidden'),'deleted',json_extract(NEW.data,'$.deleted'),'price',json_extract(NEW.data,'$.price')),'fields',{changed})"
    db.execute("CREATE TRIGGER IF NOT EXISTS monitor_profile_updated AFTER UPDATE OF data ON profiles WHEN OLD.data<>NEW.data BEGIN "+insert("CASE WHEN json_extract(NEW.data,'$.deleted')=1 AND NOT COALESCE(json_extract(OLD.data,'$.deleted'),0) THEN 'profile_archived' WHEN json_extract(OLD.data,'$.deleted')=1 AND NOT COALESCE(json_extract(NEW.data,'$.deleted'),0) THEN 'profile_restored' WHEN json_extract(OLD.data,'$.hidden') IS NOT json_extract(NEW.data,'$.hidden') THEN CASE WHEN json_extract(NEW.data,'$.hidden')=1 THEN 'profile_hidden' ELSE 'profile_restored' END WHEN json_extract(OLD.data,'$.status') IS NOT json_extract(NEW.data,'$.status') THEN 'profile_'||json_extract(NEW.data,'$.status') ELSE 'profile_changed' END",actor,'profile','NEW.id',details,f"CASE WHEN ({actor})='admin' THEN 'admin' ELSE 'platform' END")+' END')
    db.execute("CREATE TRIGGER IF NOT EXISTS monitor_appointment_created AFTER INSERT ON appointments BEGIN "+insert("'booking_created'","'client'",'appointment','NEW.id',"json_object('status',NEW.status)")+' END')
    db.execute("CREATE TRIGGER IF NOT EXISTS monitor_appointment_event AFTER INSERT ON appointment_events BEGIN "+insert('NEW.action','NEW.actor','appointment','NEW.appointment_id',"json_object('from_start',NEW.from_start,'to_start',NEW.to_start)")+' END')
    db.execute("CREATE TRIGGER IF NOT EXISTS monitor_booking_decision AFTER UPDATE OF status ON appointments WHEN OLD.status<>NEW.status AND NEW.status IN ('confirmed','declined') BEGIN "+insert("'booking_'||NEW.status","'psychologist'",'appointment','NEW.id')+' END')
    db.execute("CREATE TRIGGER IF NOT EXISTS monitor_booking_expired AFTER UPDATE OF status ON appointments WHEN OLD.status<>NEW.status AND NEW.status='expired' BEGIN "+insert("'booking_expired'","'system'",'appointment','NEW.id')+' END')
    db.execute("CREATE TRIGGER IF NOT EXISTS monitor_change_expired AFTER UPDATE OF status ON appointment_changes WHEN OLD.status='pending' AND NEW.status='expired' BEGIN "+insert("'change_expired'","'system'",'appointment','NEW.appointment_id')+' END')
    db.execute("CREATE TRIGGER IF NOT EXISTS monitor_support_message AFTER INSERT ON support_messages BEGIN "+insert("'support_message'",'NEW.sender_role','support','NEW.ticket_id',category="CASE WHEN NEW.sender_role='admin' THEN 'admin' ELSE 'platform' END")+' END')


def period(params):
    end = datetime.now(timezone.utc)
    if params.get('from') or params.get('to'):
        try:
            zone=ZoneInfo('Europe/Moscow')
            start=datetime.combine(datetime.strptime(params['from'],'%Y-%m-%d').date(),day_time(),zone).astimezone(timezone.utc)
            end=datetime.combine(datetime.strptime(params['to'],'%Y-%m-%d').date()+timedelta(days=1),day_time(),zone).astimezone(timezone.utc)
        except (ValueError,KeyError,OverflowError):
            raise ValueError('Укажите обе даты в формате ГГГГ-ММ-ДД.') from None
        if not start < end or end-start > timedelta(days=366):
            raise ValueError('Период должен быть от одного дня до года.')
    else:
        days=params.get('days','7')
        if days not in ('1','7','30','90'):
            raise ValueError('Выберите период 1, 7, 30 или 90 дней.')
        start=end-timedelta(days=int(days))
    return start.isoformat(),end.isoformat()


def journal(db, params, log_dir):
    start,end=period(params)
    category=params.get('category','all')
    if category not in ('all','admin','platform','errors','security'):
        raise ValueError('Неизвестный раздел журнала.')
    kind=params.get('kind','')
    if len(kind)>80 or (kind and not all(c.isascii() and (c.isalnum() or c=='_') for c in kind)):
        raise ValueError('Неверный тип события.')
    result=params.get('result','all')
    if result not in ('all','success','failure','denied','limited'):
        raise ValueError('Неизвестный результат операции.')
    cursor=None
    if params.get('cursor'):
        try:
            if len(params['cursor'])>256: raise ValueError()
            cursor=json.loads(base64.urlsafe_b64decode(params['cursor']).decode())
            if not isinstance(cursor,list) or len(cursor)!=2 or not all(isinstance(s,str) for s in cursor): raise ValueError()
            datetime.fromisoformat(cursor[0])
        except (ValueError,TypeError,UnicodeError,OverflowError):
            raise ValueError('Неверный указатель журнала.') from None
    rows=[]
    if category in ('all','admin','platform'):
        where=['created_at>=?','created_at<?'];args=[start,end]
        for field,value in [('category',category),('kind',kind),('result',result)]:
            if value and value!='all': where.append(f'{field}=?');args.append(value)
        if cursor:
            where.append("(created_at<? OR (created_at=? AND printf('a:%020d',id)<?))");args.extend([cursor[0],cursor[0],cursor[1]])
        for row in db.execute('SELECT * FROM admin_events WHERE '+' AND '.join(where)+' ORDER BY created_at DESC,id DESC LIMIT 51',args):
            item=dict(row);item['details']=json.loads(item['details']);item['id']=f"a:{item['id']:020d}";rows.append(item)
    logs=diagnostics.entries(log_dir)
    for item in logs['events']:
        if category not in ('all',item['category']) or not start<=item['created_at']<end: continue
        if kind and item['kind']!=kind: continue
        if result!='all' and item['result']!=result: continue
        if cursor and (item['created_at'],item['id'])>=tuple(cursor): continue
        rows.append(item)
    rows.sort(key=lambda r:(r['created_at'],r['id']),reverse=True)
    page=rows[:50]
    next_cursor=base64.urlsafe_b64encode(json.dumps([page[-1]['created_at'],page[-1]['id']]).encode()).decode() if len(rows)>50 else None
    return {'events':page,'next_cursor':next_cursor,'logs_available':logs['available'],'logs_partial':logs['partial'],'log_scope':'Последние три файла журнала, до 3 МиБ; старые записи удаляются при ротации.'}


def overview(db, params, database_path, log_dir):
    start,end=period(params)
    stamp=datetime.now(timezone.utc);now=stamp.isoformat()
    count=lambda sql,args=():db.execute(sql,args).fetchone()[0]
    clients=count('SELECT count(*) FROM clients');psychologists=count('SELECT count(*) FROM psychologists')
    registrations={role:count('SELECT count(*) FROM account_registry WHERE role=? AND registered_at>=? AND registered_at<?',(role,start,end)) for role in ('client','psychologist')}
    profiles={row['status']:row['n'] for row in db.execute("SELECT json_extract(data,'$.status') status,count(*) n FROM profiles WHERE NOT COALESCE(json_extract(data,'$.deleted'),0) GROUP BY status")}
    profiles['hidden']=count("SELECT count(*) FROM profiles WHERE json_extract(data,'$.hidden')=1 AND NOT COALESCE(json_extract(data,'$.deleted'),0)")
    profiles['archived']=count("SELECT count(*) FROM profiles WHERE json_extract(data,'$.deleted')=1")
    pending=count("SELECT count(*) FROM appointments WHERE status='pending' AND cancelled=0 AND expires_at>? AND start_at>?",(now,now))
    overdue=count("SELECT count(*) FROM appointments WHERE status='pending' AND cancelled=0 AND (expires_at<=? OR start_at<=?)",(now,now))
    expired=count("SELECT count(*) FROM appointments WHERE status='expired'")
    meetings={'created':count('SELECT count(*) FROM appointments WHERE created_at>=? AND created_at<?',(start,end)), 'pending':pending,'awaiting_expiration':overdue,'expired':expired,
              'upcoming':count("SELECT count(*) FROM appointments WHERE status='confirmed' AND cancelled=0 AND start_at>?",(now,)),
              'cancellations':count("SELECT count(*) FROM admin_events WHERE kind='cancelled' AND created_at>=? AND created_at<?",(start,end)),
              'reschedules':count("SELECT count(*) FROM admin_events WHERE kind='rescheduled' AND created_at>=? AND created_at<?",(start,end))}
    unanswered_sql="""SELECT id,waiting_since FROM (
        SELECT t.id,(SELECT MIN(m.created_at) FROM support_messages m WHERE m.ticket_id=t.id
          AND m.sender_role<>'admin' AND m.id>COALESCE((SELECT MAX(a.id) FROM support_messages a
          WHERE a.ticket_id=t.id AND a.sender_role='admin'),0)) waiting_since
        FROM support_tickets t WHERE t.status='open') WHERE waiting_since IS NOT NULL"""
    unanswered=db.execute(unanswered_sql).fetchall()
    old_support=sum(row['waiting_since']<(stamp-timedelta(days=1)).isoformat() for row in unanswered)
    old_profiles=count("""SELECT count(*) FROM profiles
        WHERE json_extract(data,'$.status')='pending' AND NOT COALESCE(json_extract(data,'$.deleted'),0)
        AND COALESCE((SELECT MAX(json_extract(h.value,'$.at')) FROM json_each(profiles.data,'$.history') h
        WHERE json_extract(h.value,'$.action')='Заявка отправлена'),json_extract(data,'$.updated_at'))<?""",((stamp-timedelta(days=2)).isoformat(),))
    tasks={'profiles_pending':profiles.get('pending',0),'profiles_overdue':old_profiles,'support_open':count("SELECT count(*) FROM support_tickets WHERE status='open'"),'support_unanswered':len(unanswered),'support_overdue':old_support}
    cancellations=[dict(row) for row in db.execute("""SELECT a.profile_id,COUNT(*) cancelled,
        sum(CASE WHEN e.actor='client' THEN 1 ELSE 0 END) by_client,
        sum(CASE WHEN e.actor='psychologist' THEN 1 ELSE 0 END) by_psychologist
        FROM admin_events e JOIN appointments a ON e.target_id=a.id
        WHERE e.kind='cancelled' AND e.created_at>=? AND e.created_at<? GROUP BY a.profile_id ORDER BY cancelled DESC,a.profile_id LIMIT 50""",(start,end))]
    for item in cancellations:
        item['total_bookings']=count('SELECT count(*) FROM appointments WHERE profile_id=? AND created_at>=? AND created_at<?',(item['profile_id'],start,end))
    logs=diagnostics.entries(log_dir)
    errors=[e for e in logs['events'] if e['category']=='errors' and start<=e['created_at']<end]
    groups={}
    for e in errors:
        signature=(e['kind'],e['details'].get('error_type'),json.dumps(e['details'].get('frames',[]),sort_keys=True))
        group=groups.setdefault(signature,{'kind':e['kind'],'error_type':e['details'].get('error_type'),'count':0,'last_at':e['created_at']})
        group['count']+=1;group['last_at']=max(group['last_at'],e['created_at'])
    hour=(stamp-timedelta(hours=1)).isoformat()
    recent=[e for e in logs['events'] if e['category']=='errors' and e['created_at']>=hour]
    repetitions={}
    for e in recent:
        signature=(e['kind'],e['details'].get('error_type'),json.dumps(e['details'].get('frames',[]),sort_keys=True))
        repetitions[signature]=repetitions.get(signature,0)+1
    maintenance_status=maintenance.status(database_path)
    warnings=[]
    def warn(condition,code,text):
        if condition:warnings.append({'code':code,'text':text})
    warn(len(recent)>=5,'errors','За последний час зарегистрировано не менее пяти ошибок.')
    warn(any(n>=3 for n in repetitions.values()),'repeated_errors','Один и тот же сбой повторился не менее трёх раз за час.')
    warn(old_support>0,'support','Есть обращения без ответа администратора более суток.')
    warn(old_profiles>0,'profiles','Есть анкеты на рассмотрении более двух суток.')
    warn(tasks['profiles_pending']>=10,'profile_queue','В очереди проверки не менее десяти анкет.')
    warn(tasks['support_unanswered']>=10,'support_queue','Без ответа администратора не менее десяти обращений.')
    try:
        free=shutil.disk_usage(Path(database_path).parent).free
    except OSError:free=None
    warn(free is None,'disk_unknown','Не удалось проверить свободное место на диске.')
    warn(free is not None and free<512*1024*1024,'disk_low','Свободно менее 512 МиБ на диске с базой.')
    last=maintenance_status.get('backup_at')
    warn(not last or last<(stamp-timedelta(days=1)).isoformat(),'backup','Нет сведений об успешной резервной копии за последние сутки.')
    warn(not logs['available'],'logs','Журнал ошибок недоступен или ещё не создан.')
    warn(logs['partial'],'logs_partial','Часть журнала повреждена или недоступна; сводка ошибок неполная.')
    warn(overdue>0,'expired_pending','Есть заявки с истёкшим сроком: они уже не удерживают время и будут закрыты при обработке записей.')
    return {'period':{'from':start,'to':end},'users':{'total':clients+psychologists,'clients':clients,'psychologists':psychologists,'registrations':registrations,'legacy_unknown_date':count('SELECT count(*) FROM account_registry WHERE registered_at IS NULL')},
            'profiles':profiles,'meetings':meetings,'tasks':tasks,'cancellations':cancellations,
            'errors':{'count':len(errors),'groups':sorted(groups.values(),key=lambda x:x['count'],reverse=True)[:20],'latest':errors[:10],'available':logs['available'],'partial':logs['partial'],'scope':'Учитываются только сохранившиеся файлы журнала, до 3 МиБ.'},
            'service':{'version':VERSION,'schema_version':db.execute('PRAGMA user_version').fetchone()[0],'uptime_seconds':int(time.monotonic()-STARTED),'database_available':True,'database_bytes':Path(database_path).stat().st_size,'disk_free_bytes':free},
            'maintenance':maintenance_status,'warnings':warnings}
