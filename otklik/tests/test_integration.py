"""Regression checks for imported client settings, video links and web layout."""
import unittest
import re

import test_server
import test_changes


class IntegrationTest(unittest.TestCase):
    setUp = test_changes.ChangesTest.setUp
    tearDown = test_changes.ChangesTest.tearDown
    request = test_changes.ChangesTest.request
    psy = test_changes.ChangesTest.psy
    saved = test_changes.ChangesTest.saved
    submit = test_changes.ChangesTest.submit
    review = test_changes.ChangesTest.review
    slot = test_changes.ChangesTest.slot
    public = test_changes.ChangesTest.public
    ready = test_changes.ChangesTest.ready
    client = test_changes.ChangesTest.client
    book = test_changes.ChangesTest.book
    appointments = test_changes.ChangesTest.appointments
    meeting = test_changes.ChangesTest.meeting
    action = test_changes.ChangesTest.action

    def test_pages_load_their_public_dependencies_and_keep_sources_private(self):
        for page in ('/', '/client', '/psychologist', '/admin', '/documents.html'):
            code, html = self.request('anonymous', 'GET', page)
            self.assertEqual(code, 200)
            for asset in re.findall(rb'(?:href|src)="(/static/[^\"]+)"', html):
                self.assertEqual(self.request('anonymous', 'GET', asset.decode())[0], 200, asset)
        for path in ('/database.py', '/video_links.py', '/diagnostics.py', '/logs/otklik.log', '/.local/logs/otklik.log', '/templates/client.html', '/static/../server.py', '/static/js/../../server.py'):
            self.assertEqual(self.request('anonymous', 'GET', path)[0], 404)

    def test_video_link_rejects_stale_editor(self):
        _, item = self.meeting()
        self.assertEqual(self.action('psy', item, 'video-link', 'psychologist', url='https://example.test/first')[0], 200)
        self.assertEqual(self.action('psy', item, 'video-link', 'psychologist', url='https://example.test/stale')[0], 409)
        self.assertEqual(self.appointments()[0]['video_url'], 'https://example.test/first')

    def test_video_link_access_validation_and_removal(self):
        _, item = self.meeting()
        self.psy('stranger')
        self.assertEqual(self.action('stranger', item, 'video-link', 'psychologist', url='https://example.test/private')[0], 409)
        self.assertEqual(self.action('client', item, 'video-link', url='https://example.test/private')[0], 404)
        for value in ('javascript:alert(1)', 'http://example.test', 'https://user:pass@example.test', 'https://example.test/\nroom'):
            self.assertEqual(self.action('psy', item, 'video-link', 'psychologist', url=value)[0], 400)
        self.assertEqual(self.action('psy', item, 'video-link', 'psychologist', url='https://example.test/private')[0], 200)
        self.assertNotIn('video_url', self.appointments('admin', 'admin')[0])
        current = self.appointments()[0]
        self.assertEqual(self.action('psy', current, 'video-link', 'psychologist', url='')[0], 200)
        self.assertNotIn('video_url', self.appointments()[0])

    def test_client_settings_validation_and_isolation(self):
        self.client()
        self.client('other')
        data = {'name': 'Новое имя', 'age': 30}
        self.assertEqual(self.request('anonymous', 'POST', '/api/client/settings', data)[0], 401)
        for age in (True, 0, 121, '30'):
            self.assertEqual(self.request('client', 'POST', '/api/client/settings', data | {'age': age})[0], 400)
        self.assertEqual(self.request('client', 'POST', '/api/client/settings', data)[0], 200)
        user = self.request('client', 'GET', '/api/client/me')[1]['client']
        self.assertEqual((user['name'], user['age']), ('Новое имя', 30))
        self.assertIsNone(self.request('other', 'GET', '/api/client/me')[1]['client']['age'])


if __name__ == '__main__':
    unittest.main()
