"""Expanded specialist topics and explicitly published optional age."""
import unittest
import server
import test_booking
import catalog_options

class CatalogOptionsTest(unittest.TestCase):
    for name in ('setUp','tearDown','request','psy','saved','submit','review','slot','public'):
        locals()[name]=getattr(test_booking.BookingTest,name)

    def test_topics_and_optional_age_are_validated_before_publication(self):
        p=self.saved(self.psy())
        code,data=self.request('psy','POST','/api/psychologist/profile',{'revision':p['revision'],'age':35,'topics':['anxiety','boundaries','sleep']})
        self.assertEqual(code,200,data);p=data['profile']
        for age in (True,17,101,'35'):
            self.assertEqual(self.request('psy','POST','/api/psychologist/profile',{'revision':p['revision'],'age':age})[0],400)
        self.assertEqual(self.request('psy','POST','/api/psychologist/profile',{'revision':p['revision'],'topics':['not-a-topic']})[0],400)
        p=self.review(self.submit(p),'approve')[1]['profile']
        self.assertEqual(self.request('psy','POST','/api/psychologist/schedule',{'revision':p['revision'],'slots':[self.slot()]})[0],200)
        public=self.public()[0];self.assertEqual(public['age'],35);self.assertEqual(set(public['topics']),{'anxiety','boundaries','sleep'})
        p=self.request('psy','GET','/api/psychologist/me')[1]['profile']
        self.assertEqual(self.request('psy','POST','/api/psychologist/profile',{'revision':p['revision'],'age':50})[0],200)
        self.assertEqual(self.public()[0]['age'],35,'A private draft must not change the approved age')

    def test_shared_dictionary_is_public_but_module_stays_private(self):
        code,data=self.request('anonymous','GET','/static/js/catalog-options.js');self.assertEqual(code,200)
        self.assertIn('Личные границы'.encode(),data);self.assertEqual(len(catalog_options.TOPICS),12)
        self.assertEqual(self.request('anonymous','GET','/catalog_options.py')[0],404)

    def test_erasing_psychologist_also_removes_optional_age(self):
        import json
        p=self.saved(self.psy())
        self.assertEqual(self.request('psy','POST','/api/psychologist/profile',{'revision':p['revision'],'age':35})[0],200)
        code,result=self.request('psy','POST','/api/psychologist/account/delete',{'password':'test-psychologist-password','confirmation':'УДАЛИТЬ'})
        self.assertEqual(code,200,result)
        with server.connection() as db:
            profile=json.loads(db.execute('SELECT data FROM profiles WHERE id=?',(p['id'],)).fetchone()[0])
            self.assertIsNone(profile['age'])

if __name__=='__main__':unittest.main()
