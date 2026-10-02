from contextlib import closing
import hashlib
import hmac
import io
import json
import os
import sqlite3
import tempfile
import unittest
from app import Store, Webhook, MAX_BODY


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'events.db')
        self.store = Store(local_path=self.path)
        self.app = Webhook('test-token', 'test-secret', self.store)
        self.body = json.dumps({'object': 'whatsapp_business_account', 'entry': [{'changes': [{'field': 'messages', 'value': {'messages': [{'id': 'wamid.test', 'text': {'body': 'teste'}}]}}]}]}).encode()

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method='POST', body=None, signature=None, query='', path='/webhook', length=None):
        body = self.body if body is None else body
        if signature is None:
            signature = 'sha256=' + hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()
        environ = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query, 'CONTENT_LENGTH': str(len(body) if length is None else length), 'wsgi.input': io.BytesIO(body), 'HTTP_X_HUB_SIGNATURE_256': signature}
        result = []
        response = b''.join(self.app(environ, lambda status, headers: result.append(status)))
        return result[0], response

    def count(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT count(*) FROM webhook_events').fetchone()[0]

    def test_verification(self):
        self.assertEqual(self.request('GET', query='hub.mode=subscribe&hub.verify_token=test-token&hub.challenge=123'), ('200 OK', b'123'))
        self.assertEqual(self.request('GET', query='hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=123')[0], '403 Forbidden')

    def test_signed_event_and_duplicate(self):
        self.assertEqual(self.request()[0], '200 OK')
        self.assertEqual(self.request()[0], '200 OK')
        self.assertEqual(self.count(), 1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM webhook_events').fetchone()[0]), json.loads(self.body))

    def test_signature_rejected(self):
        self.assertEqual(self.request(signature='')[0], '403 Forbidden')
        self.assertEqual(self.request(signature='sha256=wrong')[0], '403 Forbidden')
        self.assertEqual(self.count(), 0)

    def test_invalid_payload(self):
        for body in [b'{', b'[]', b'{"object":"wrong","entry":[]}']:
            self.assertEqual(self.request(body=body)[0], '400 Bad Request')
        self.assertEqual(self.count(), 0)

    def test_limits(self):
        self.assertEqual(self.request(length=MAX_BODY + 1)[0], '413 Payload Too Large')
        self.assertEqual(self.request(body=b'')[0], '400 Bad Request')
        self.assertEqual(self.request(length=len(self.body) + 1)[0], '400 Bad Request')

    def test_storage_failure(self):
        def fail(raw):
            raise OSError('test')
        self.store.save = fail
        self.assertEqual(self.request()[0], '503 Service Unavailable')

    def test_health_and_routes(self):
        self.assertEqual(self.request('GET', path='/healthz')[0], '200 OK')
        self.assertEqual(self.request('GET', path='/missing')[0], '404 Not Found')
        self.assertEqual(self.request('DELETE')[0], '405 Method Not Allowed')

    def test_secrets_required(self):
        with self.assertRaises(ValueError):
            Webhook('', '', self.store)


if __name__ == '__main__':
    unittest.main()
