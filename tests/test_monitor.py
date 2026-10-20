import base64
from datetime import datetime, timezone
import io
import json
import os
import tempfile
import unittest

from cryptography.fernet import Fernet
from app import Store, Webhook
from monitor import Monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'monitor.db')
        self.store = Store(local_path=self.path)
        self.settings = dict(CRM_ADMIN_PASSWORD='private-test-password', BLING_ENCRYPTION_KEY=Fernet.generate_key().decode(), GPTMAKER_MONITOR_AGENT_ID='agent')
        self.monitor = Monitor(self.store, self.settings)
        self.now = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, **changes):
        event = dict(assistantId='agent', channel='CLOUD_API', messageId='m1', contextId='c1', role='user', date='2026-10-20T16:00:00Z', contactName='<script>Cliente</script>', contactPhone='5511999999999', message='Olá, preciso de ajuda', images=[], audios=[], documents=[], videos=[])
        event.update(changes)
        return event

    def test_dedup_restart_and_encrypted_storage(self):
        event = self.event(images=['https://private.example/file'])
        self.monitor.save(event, self.now)
        self.monitor.save(event, self.now)
        restarted = Monitor(self.store, self.settings)
        self.assertEqual(restarted.latest()[0]['mediaCount'], 1)
        with self.store.connect() as db:
            rows = db.execute('SELECT payload, context_key FROM monitor_messages').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(event['contactPhone'], rows[0][0])
        self.assertNotIn(event['message'], rows[0][0])
        self.assertNotIn('private.example', restarted.cipher.decrypt(rows[0][0].encode()).decode())
        self.assertNotEqual(rows[0][1], 'c1')
        with self.assertRaises(ValueError):
            self.monitor.save(self.event(message='Changed duplicate'), self.now)

    def test_out_of_order_reply_then_new_customer_and_separate_context(self):
        self.monitor.save(self.event(messageId='reply', role='assistant', date='2026-10-20T16:02:00Z'), self.now)
        self.monitor.save(self.event(), self.now)
        self.assertEqual(self.monitor.latest()[0]['role'], 'assistant')
        self.monitor.save(self.event(messageId='next', date='2026-10-20T16:03:00Z'), self.now)
        self.monitor.save(self.event(messageId='other', contextId='c2'), self.now)
        self.assertEqual(len(self.monitor.latest()), 2)
        self.assertEqual(self.monitor.latest()[0]['messageId'], 'next')

    def test_equal_timestamps_prefer_review(self):
        self.monitor.save(self.event(), self.now)
        self.monitor.save(self.event(messageId='reply', role='assistant'), self.now)
        self.assertEqual(self.monitor.latest()[0]['role'], 'user')

    def test_validation(self):
        for changes in [dict(assistantId='other'), dict(role='system'), dict(channel='OTHER'), dict(date='bad'), dict(date='2026-10-20T16:00:00'), dict(date='2027-01-01T00:00:00Z'), dict(date=None), dict(messageId=''), dict(contextId=''), dict(message=123)]:
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                self.monitor.save(self.event(**changes), self.now)
        self.assertEqual(self.monitor.latest(), [])

    def request(self, method='GET', path='/monitor', payload=None, auth=True):
        raw = json.dumps(self.event() if payload is None else payload).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(b'admin:private-test-password').decode()
        app = Webhook('verify', 'secret', self.store)
        app.monitor = self.monitor
        result = []
        body = b''.join(app(env, lambda status, headers: result.append((status, dict(headers)))))
        return result[0], body

    def test_private_routes_and_escaped_panel(self):
        self.assertEqual(self.request(auth=False)[0][0], '401 Unauthorized')
        self.assertEqual(self.request('POST', '/monitor/events', auth=False)[0][0], '401 Unauthorized')
        self.assertEqual(self.request('POST', '/monitor/events')[0][0], '200 OK')
        (status, headers), body = self.request()
        self.assertEqual(status, '200 OK')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn(b'&lt;script&gt;', body)
        self.assertNotIn(b'<script>', body)
        self.assertIn('revisar'.encode(), body)
        self.assertEqual(self.request('GET', '/monitor/events')[0][0], '405 Method Not Allowed')
        self.assertEqual(self.request('POST', '/monitor/events', payload=[])[0][0], '400 Bad Request')

    def test_storage_failure_is_not_acknowledged(self):
        def fail(*args):
            raise OSError('private error')
        self.monitor.save = fail
        (status, _), body = self.request('POST', '/monitor/events')
        self.assertEqual(status, '503 Service Unavailable')
        self.assertNotIn(b'private error', body)
