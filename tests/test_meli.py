import base64
from datetime import datetime, timezone, timedelta
import io
import json
import unittest
from urllib.parse import parse_qs, urlparse
from meli import Meli
from bling import BlingError
from test_bling import BlingTests


class MeliTests(BlingTests):
    # Inherit database fixture and existing regression tests.
    def setUp(self):
        super().setUp()
        self.integration = Meli(self.crm, '1234', 'secret', self.api)
        self.counts = []

    def api(self, method, url, headers, body=None):
        self.counts.append((method, url))
        if method == 'POST':
            return {'access_token': 'ml-access', 'refresh_token': 'ml-refresh', 'user_id': 88, 'expires_in': 3600}
        path = urlparse(url).path
        if path == '/packs/101':
            return {'orders': [{'id': 102}], 'shipment': {'id': 103}}
        if path == '/orders/102':
            return {'seller': {'id': 88}, 'status': 'paid'}
        if path == '/shipments/103':
            return {'origin': {'sender_id': 88}, 'status': 'delivered', 'substatus': None}
        if path == '/shipments/103/sla':
            return {'expected_date': '2026-10-13T18:00:00-03:00'}
        raise BlingError('Mercado Livre HTTP 404')

    def seed_token(self, expired=False):
        token = {'access_token': 'old', 'refresh_token': 'old-refresh', 'user_id': 88, 'expires_at': 0 if expired else 9999999999}
        with self.store.connect() as db:
            self.crm.put(db, 'meli_tokens', self.crm.cipher.encrypt(json.dumps(token).encode()).decode())

    def call(self, path, method='GET', query='', cookie='', body=None, auth=True):
        raw = json.dumps(body or {}).encode()
        env = {'PATH_INFO': path, 'REQUEST_METHOD': method, 'QUERY_STRING': query, 'HTTP_COOKIE': cookie, 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(('admin:' + self.settings['CRM_ADMIN_PASSWORD']).encode()).decode()
        result = []
        data = b''.join(self.integration.handle(env, lambda s,h: result.append((s,dict(h)))))
        return result[0], data

    def test_meli_oauth_binding_encryption_and_rotation(self):
        self.assertEqual(self.call('/meli/connect', auth=False)[0][0], '401 Unauthorized')
        (status, headers), _ = self.call('/meli/connect')
        state = parse_qs(urlparse(headers['Location']).query)['state'][0]
        with self.assertRaises(BlingError):
            self.integration.authorize('code', state, 'wrong')
        self.integration.authorize('code', state, state)
        with self.assertRaises(BlingError):
            self.integration.authorize('code', state, state)
        self.seed_token(expired=True)
        self.assertEqual(self.integration.token()['access_token'], 'ml-access')
        with self.store.connect() as db:
            self.assertNotIn('ml-refresh', self.crm.get(db, 'meli_tokens'))

    def test_canonical_pack_shipping_and_sla(self):
        self.seed_token()
        result = self.integration.resolve('101', 88)
        self.assertEqual(result['status'], 'delivered')
        self.assertEqual(result['dispatch_deadline'], '2026-10-13T18:00:00-03:00')
        with self.assertRaises(BlingError):
            self.integration.resolve('101', 99)
        with self.assertRaises(BlingError):
            self.integration.read('https://evil.test/')

    def test_shipment_origin_must_match_authorized_seller(self):
        self.seed_token()
        original = self.integration.transport
        for origin in ({'sender_id': 99}, {}):
            def response(method, url, headers, body=None):
                if urlparse(url).path == '/shipments/103':
                    return {'origin': origin, 'sender_id': 88, 'status': 'delivered'}
                return original(method, url, headers, body)
            self.integration.transport = response
            with self.assertRaisesRegex(BlingError, 'Envio de outra conta'):
                self.integration.resolve('101', 88)

    def test_notifications_are_only_bounded_signals(self):
        self.seed_token()
        event = {'application_id': 1234, 'user_id': 88, 'topic': 'shipments', 'resource': '/shipments/103', 'status': 'cancelled'}
        self.assertEqual(self.call('/meli/notifications', 'POST', body=event, auth=False)[0][0], '200 OK')
        self.assertFalse(self.counts)
        with self.store.connect() as db:
            self.assertEqual(self.crm.get(db, 'meli_dirty'), '1')
        event['resource'] = 'https://evil.test/shipments/103'
        self.assertEqual(self.call('/meli/notifications', 'POST', body=event)[0][0], '400 Bad Request')
        event['resource'] = '/shipments/103'
        event['user_id'] = 99
        self.assertEqual(self.call('/meli/notifications', 'POST', body=event)[0][0], '403 Forbidden')

    def test_api_delivery_overrides_manual_and_cancelled_not_valid(self):
        row = self.crm.normalize(self.order(external='101'), {'id': 1})
        data = {'status': 'delivered', 'order_statuses': ['paid'], 'checked_at': datetime.now(timezone.utc).isoformat()}
        with self.store.connect() as db:
            db.execute('INSERT INTO meli_shipments VALUES (?,?)', (row['key'], json.dumps(data)))
        self.assertTrue(self.integration.valid(row))
        self.assertIn('Entregue', self.integration.action(row))
        data['order_statuses'] = ['cancelled']
        data['status'] = 'cancelled'
        with self.store.connect() as db:
            db.execute('UPDATE meli_shipments SET payload=?', (json.dumps(data),))
        self.assertFalse(self.integration.valid(row))
        data['checked_at'] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.store.connect() as db:
            db.execute('UPDATE meli_shipments SET payload=?', (json.dumps(data),))
        self.assertIsNone(self.integration.valid(row))
        self.assertIn('desatualizados', self.integration.action(row))


if __name__ == '__main__':
    unittest.main()
