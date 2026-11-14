import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        server.DB = Path(self.temp.name) / 'test.sqlite3'
        server.SESSIONS.clear()
        server.PSY_SESSIONS.clear()
        server.Handler.attempts.clear()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        server.PORT = self.http.server_port
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.cookies = {}
        self.request('admin', 'POST', '/api/setup', {'password': 'test-admin-password'})

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.temp.cleanup()

    def request(self, who, method, path, data=None, origin=True):
        conn = http.client.HTTPConnection('127.0.0.1', server.PORT)
        headers = {'Cookie': self.cookies.get(who, '')}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if origin:
            headers['Origin'] = f'http://127.0.0.1:{server.PORT}'
        conn.request(method, path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse()
        if response.getheader('Set-Cookie'):
            self.cookies[who] = response.getheader('Set-Cookie').split(';')[0]
        raw = response.read()
        body = json.loads(raw) if response.getheader('Content-Type').startswith('application/json') else raw
        code = response.status
        conn.close()
        return code, body

    def psy(self, who='psy'):
        code, _ = self.request(who, 'POST', '/api/psychologist/register', {'email': f'{who}@test.invalid', 'password': 'test-psychologist-password'})
        self.assertEqual(code, 200)
        return self.request(who, 'GET', '/api/psychologist/me')[1]['profile']

    def saved(self, p, who='psy'):
        fields = dict(name='Тестовый психолог', gender='female', price=3000, experience=5, style='gentle', approach='КПТ', bio='Описание специалиста для локальной проверки процесса.', education='Учебное заведение, программа и год', topics=['anxiety'], photo='', contact='private-contact', revision=p['revision'])
        code, body = self.request(who, 'POST', '/api/psychologist/profile', fields)
        self.assertEqual(code, 200, body)
        return body['profile']

    def submit(self, p):
        code, body = self.request('psy', 'POST', '/api/psychologist/submit', {'revision': p['revision']})
        self.assertEqual(code, 200, body)
        return body['profile']

    def review(self, p, action, note=''):
        return self.request('admin', 'POST', '/api/admin/review', {'id': p['id'], 'revision': p['revision'], 'action': action, 'note': note})

    def slot(self, days=2):
        return (datetime.now(timezone.utc)+timedelta(days=days)).replace(hour=16, minute=30, second=0, microsecond=0).isoformat()

    def public(self):
        return self.request('anonymous', 'GET', '/api/profiles')[1]['profiles']

    def test_complete_approval_schedule_visibility(self):
        p = self.psy()
        self.assertEqual(p['status'], 'draft')
        p = self.submit(self.saved(p))
        self.assertEqual(p['status'], 'pending')
        self.assertEqual(self.public(), [])
        self.assertEqual(self.request('psy','POST','/api/psychologist/schedule', {'revision':p['revision'],'slots':[self.slot()]})[0], 403)
        code, data = self.review(p, 'approve')
        self.assertEqual(code, 200)
        p = data['profile']
        self.assertFalse(p['published'])
        self.assertEqual(self.public(), [])
        code, data = self.request('psy','POST','/api/psychologist/schedule',{'revision':p['revision'],'slots':[self.slot()]})
        self.assertEqual(code, 200, data)
        p = data['profile']
        public = self.public()
        self.assertEqual(len(public), 1)
        self.assertIn(':30:00', public[0]['slots'][0])
        self.assertEqual(public[0]['education'], 'Учебное заведение, программа и год')
        for key in ['email','owner_id','contact','history','review_note']:
            self.assertNotIn(key, public[0])
        code,data=self.review(p,'hide')
        self.assertEqual(code,200)
        self.assertEqual(self.public(),[])
        p=data['profile']
        p=self.review(p,'restore')[1]['profile']
        self.assertTrue(p['published'])
        code,data=self.request('psy','POST','/api/psychologist/schedule',{'revision':p['revision'],'slots':[]})
        self.assertEqual(code,200)
        self.assertEqual(self.public(),[])
        self.assertGreater(len(data['profile']['history']),4)

    def test_rejection_and_resubmission(self):
        p=self.submit(self.saved(self.psy()))
        self.assertEqual(self.review(p,'reject')[0],400)
        code,data=self.review(p,'reject','Уточните образование')
        self.assertEqual(code,200)
        p=data['profile']
        self.assertEqual(p['review_note'],'Уточните образование')
        self.assertEqual(self.request('psy','GET','/api/psychologist/me')[1]['profile']['status'],'rejected')
        p=self.submit(self.saved(p))
        self.assertEqual(p['status'],'pending')

    def test_role_ownership_and_stale_review(self):
        a=self.psy()
        b=self.psy('other')
        self.assertEqual(self.request('other','GET','/api/psychologist/me')[1]['profile']['id'],b['id'])
        self.assertEqual(self.request('psy','GET','/api/admin/profiles')[0],401)
        self.assertEqual(self.request('psy','POST','/api/admin/review',{'id':a['id'],'action':'approve'})[0],401)
        self.assertEqual(self.request('anonymous','GET','/api/psychologist/me')[0],401)
        a=self.submit(self.saved(a))
        self.assertEqual(self.review(a,'approve')[0],200)
        self.assertEqual(self.review(a,'reject','stale')[0],409)
        self.assertEqual(self.request('other','POST','/api/psychologist/schedule',{'id':a['id'],'revision':b['revision'],'slots':[self.slot()]})[0],403)

    def test_archive_restore_and_edit_reapproval(self):
        p=self.submit(self.saved(self.psy()))
        p=self.review(p,'approve')[1]['profile']
        p=self.request('psy','POST','/api/psychologist/schedule',{'revision':p['revision'],'slots':[self.slot()]})[1]['profile']
        self.assertEqual(len(self.public()),1)
        p=self.saved(p)
        self.assertEqual(p['status'],'draft')
        self.assertEqual(len(self.public()),1)
        p=self.submit(p)
        p=self.review(p,'approve')[1]['profile']
        p=self.review(p,'delete')[1]['profile']
        self.assertTrue(p['deleted'])
        self.assertEqual(self.public(),[])
        self.assertEqual(self.request('psy','POST','/api/psychologist/schedule',{'revision':p['revision'],'slots':[]})[0],403)
        p=self.review(p,'restore')[1]['profile']
        self.assertTrue(p['published'])

    def test_draft_validation_expired_slots_and_private_files(self):
        p=self.psy()
        self.assertEqual(self.request('psy','POST','/api/psychologist/submit',{'revision':p['revision']})[0],400)
        code,data=self.request('psy','POST','/api/psychologist/profile',{'revision':p['revision'],'name':'А','bio':'Начало'})
        self.assertEqual(code,200,data)
        p=self.submit(self.saved(data['profile']))
        p=self.review(p,'approve')[1]['profile']
        for slots in [[self.slot(),(datetime.fromisoformat(self.slot())+timedelta(minutes=20)).isoformat()],['2020-01-01T12:00:00+03:00']]:
            self.assertEqual(self.request('psy','POST','/api/psychologist/schedule',{'revision':p['revision'],'slots':slots})[0],400)
        with server.connection() as db:
            p['slots']=['2020-01-01T12:00:00+03:00']
            server.save_record(db,p)
        self.assertEqual(self.public(),[])
        for path in ['/.local/otklik.sqlite3','/server.py','/assets/../server.py']:
            self.assertEqual(self.request('anonymous','GET',path)[0],404)
        self.assertEqual(self.request('psy','POST','/api/psychologist/profile',{},origin=False)[0],403)


if __name__=='__main__':unittest.main()
