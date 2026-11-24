"""Participant reports and explicit administrative decisions about completed meetings."""
import json

import booking

VALUES={'held','client_absent','psychologist_absent'}


def install(db):
    db.execute("""CREATE TABLE IF NOT EXISTS appointment_outcomes (
        appointment_id TEXT PRIMARY KEY REFERENCES appointments(id),
        client_report TEXT CHECK(client_report IN ('held','client_absent','psychologist_absent')),psychologist_report TEXT CHECK(psychologist_report IN ('held','client_absent','psychologist_absent')),final_value TEXT CHECK(final_value IN ('held','client_absent','psychologist_absent')),
        state TEXT NOT NULL DEFAULT 'unreported' CHECK(state IN ('unreported','reported','agreed','disputed','resolved')),revision INTEGER NOT NULL DEFAULT 0,
        decided_by TEXT,updated_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS outcome_requests (
        appointment_id TEXT NOT NULL REFERENCES appointments(id),role TEXT NOT NULL,
        request_key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(appointment_id,role,request_key))""")


def summary(db,ident):
    row=db.execute('SELECT * FROM appointment_outcomes WHERE appointment_id=?',(ident,)).fetchone()
    return dict(row) if row else {'state':'unreported','revision':0,'client_report':None,'psychologist_report':None,'final_value':None,'decided_by':None}


def eligible(item):
    if item['status']!='confirmed' or item['cancelled'] or item['end_at']>booking.now():
        raise booking.Conflict('Результат доступен после окончания подтверждённой встречи.')


def report(db,role,actor,data):
    value=data.get('value');key=data.get('request_key')
    if value not in VALUES or not isinstance(key,str) or not 16<=len(key)<=100:
        raise ValueError('Выберите результат и обновите форму.')
    db.execute('BEGIN IMMEDIATE')
    if role=='admin':
        item=db.execute('SELECT * FROM appointments WHERE id=?',(data.get('id'),)).fetchone()
        if not item:raise booking.Conflict('Встреча не найдена.')
    else:item=booking.owned_appointment(db,role,actor,data.get('id'))
    eligible(item)
    current=summary(db,item['id'])
    previous=db.execute('SELECT value FROM outcome_requests WHERE appointment_id=? AND role=? AND request_key=?',(item['id'],role,key)).fetchone()
    if previous:
        if previous[0]!=value:raise booking.Conflict('Этот запрос использован для другого результата.')
        return {'outcome':current}
    if type(data.get('revision')) is not int or data['revision']!=current['revision']:
        raise booking.Conflict('Результат изменился. Обновите встречу и повторите действие.')
    if role!='admin' and current['state'] in ('agreed','resolved'):
        raise booking.Conflict('Итог уже подтверждён. Для исправления обратитесь в поддержку.')
    if role=='admin':
        current.update(final_value=value,state='resolved',decided_by='admin')
    else:
        current[role+'_report']=value
        a,b=current['client_report'],current['psychologist_report']
        current.update(state='agreed' if a and a==b else 'disputed' if a and b else 'reported',final_value=a if a and a==b else None,decided_by='participants' if a and a==b else None)
    revision=current['revision']+1;stamp=booking.now()
    db.execute('INSERT INTO appointment_outcomes VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(appointment_id) DO UPDATE SET client_report=excluded.client_report,psychologist_report=excluded.psychologist_report,final_value=excluded.final_value,state=excluded.state,revision=excluded.revision,decided_by=excluded.decided_by,updated_at=excluded.updated_at',
        (item['id'],current['client_report'],current['psychologist_report'],current['final_value'],current['state'],revision,current['decided_by'],stamp))
    db.execute('INSERT INTO outcome_requests VALUES(?,?,?,?)',(item['id'],role,key,value))
    db.execute('INSERT INTO admin_events(created_at,category,kind,actor,target_kind,target_id,result,details) VALUES(?,?,?,?,?,?,?,?)',
        (stamp,'admin' if role=='admin' else 'platform','outcome_resolved' if role=='admin' else 'outcome_reported',role,'appointment',item['id'],'success',json.dumps({'value':value,'state':current['state']})))
    owner=db.execute("SELECT json_extract(data,'$.owner_id') FROM profiles WHERE id=?",(item['profile_id'],)).fetchone()[0]
    recipients=[('client',item['client_id']),('psychologist',owner)]
    if current['state']=='disputed':recipients.append(('admin','administrator'))
    title='Результат встречи требует разбора' if current['state']=='disputed' else 'Результат встречи обновлён'
    for recipient,ident in recipients:
        if recipient==role:continue
        db.execute("INSERT OR IGNORE INTO notifications(recipient_role,recipient_id,event_key,kind,target_kind,target_id,title,created_at) VALUES(?,?,?,'appointment','appointment',?,?,?)",(recipient,ident,'outcome:'+item['id']+':'+str(revision),item['id'],title,stamp))
    return {'outcome':summary(db,item['id'])}
