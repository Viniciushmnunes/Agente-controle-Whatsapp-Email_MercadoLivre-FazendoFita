from contextlib import closing
import base64
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from cryptography.fernet import Fernet
from app import Store
from bling import Bling, BlingError


class BlingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(local_path=os.path.join(self.tmp.name, 'crm.db'))
        self.settings = dict(BLING_CLIENT_ID='client', BLING_CLIENT_SECRET='secret', BLING_ENCRYPTION_KEY=Fernet.generate_key().decode(), CRM_ADMIN_PASSWORD='a-private-password-for-tests', BLING_STORE_ID='10', BLING_START_DATE='2026-10-01', BLING_VALID_STATUS_IDS='9', BLING_EXCLUDED_STATUS_IDS='12', BLING_PENDING_STATUS_IDS='6')
        self.calls = []
        self.crm = Bling(self.store, self.settings, self.transport)

    def tearDown(self):
        self.tmp.cleanup()

    def transport(self, method, url, headers, body=None):
        self.calls.append((method, url))
        self.assertEqual(headers.get('enable-jwt'), '1')
        return dict(access_token='access', refresh_token='refresh', expires_in=3600)

    def request(self, path='/crm', auth=True, query='', cookie=''):
        env = dict(PATH_INFO=path, REQUEST_METHOD='GET', QUERY_STRING=query, HTTP_COOKIE=cookie, **{'wsgi.input': io.BytesIO()})
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(('admin:' + self.settings['CRM_ADMIN_PASSWORD']).encode()).decode()
        result = []
        body = b''.join(self.crm.handle(env, lambda s, h: result.append((s, dict(h)))))
        return result[0], body

    def order(self, oid=1, contact=1, status=9, external='sale-1'):
        return dict(id=oid, numero=oid, numeroLoja=external, data='2026-10-09', loja=dict(id=10), contato=dict(id=contact), situacao=dict(id=status), total=19.94, itens=[])

    def test_private_panel_and_html_escape(self):
        self.assertEqual(self.request(auth=False)[0][0], '401 Unauthorized')
        with self.store.connect() as db:
            row = self.crm.normalize(self.order(), dict(id=1, nome='<script>x</script>', numeroDocumento='12345678909'))
            db.execute('INSERT INTO crm_orders VALUES (?,?)', (row['key'], json.dumps(row)))
        (_, headers), body = self.request()
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn(b'&lt;script&gt;', body)
        self.assertNotIn(b'12345678909', body)

    def test_oauth_state_cookie_replay_and_encryption(self):
        (status, headers), _ = self.request('/bling/connect')
        self.assertEqual(status, '302 Found')
        state = headers['Set-Cookie'].split(';')[0].split('=', 1)[1]
        with self.assertRaises(BlingError):
            self.crm.authorize('code', state, 'other-browser')
        self.crm.authorize('code', state, state)
        self.assertEqual(self.crm.access_token(), 'access')
        with self.store.connect() as db:
            saved = self.crm.get(db, 'tokens')
        self.assertNotIn('refresh', saved)
        with self.assertRaises(BlingError):
            self.crm.authorize('code', state, state)

    def test_expired_state_and_callback_without_session(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO bling_states VALUES (?,?)', ('not-valid', time.time() - 1))
        self.assertEqual(self.request('/bling/callback', auth=False, query='code=test&state=x')[0][0], '400 Bad Request')
        self.assertFalse(self.calls)

    def test_refresh_rotation_persisted(self):
        token = dict(access_token='old', refresh_token='old-refresh', expires_at=0)
        with self.store.connect() as db:
            self.crm.put(db, 'tokens', self.crm.cipher.encrypt(json.dumps(token).encode()).decode())
        self.assertEqual(self.crm.access_token(), 'access')
        self.assertEqual(self.crm.access_token(), 'access')
        self.assertEqual(len(self.calls), 1)

    def test_store_discovery_is_private_and_missing_store_does_not_import(self):
        self.assertEqual(self.request('/bling/stores', auth=False)[0][0], '401 Unauthorized')
        self.crm.shop = ''
        with self.assertRaises(BlingError):
            self.crm.sync()
        self.assertFalse(self.calls)

    def test_document_identity_and_fallback(self):
        self.assertEqual(self.crm.identity('123.456.789-09', 1), self.crm.identity('12345678909', 2))
        self.assertNotEqual(self.crm.identity('', 1)[0], self.crm.identity('', 2)[0])
        self.assertNotEqual(self.crm.identity('00000000000', 1)[0], self.crm.identity('00000000000', 2)[0])

    def test_sync_duplicate_recurrence_cancellation_and_failure(self):
        statuses = {1: 9, 2: 9}
        def read(path, query=None):
            if path == '/pedidos/vendas':
                return [dict(id=1), dict(id=1), dict(id=2)] if query['pagina'] == 1 else []
            if path.startswith('/contatos/'):
                return dict(id=int(path.split('/')[-1]), nome='Example', numeroDocumento='12345678909')
            oid = int(path.split('/')[-1])
            return self.order(oid, oid, statuses[oid], 'sale-' + str(oid))
        self.crm.read = read
        self.assertEqual(self.crm.sync(), 2)
        self.assertIn('Recorrente', self.crm.page())
        statuses[2] = 12
        self.crm.sync()
        self.assertIn('Compra única', self.crm.page())
        self.assertNotIn('Recorrente', self.crm.page())
        def fail(path, query=None):
            raise BlingError('Bling HTTP 429')
        self.crm.read = fail
        with self.assertRaises(BlingError):
            self.crm.sync()
        self.assertEqual(len(self.crm.snapshot()[0]), 2)
        with closing(sqlite3.connect(self.store.local_path)) as db:
            data = db.execute('SELECT payload FROM crm_orders').fetchall()
        self.assertNotIn('12345678909', str(data))

    def test_unknown_status_and_store_isolation(self):
        self.crm.read = lambda path, query=None: [dict(id=1)] if path == '/pedidos/vendas' and query['pagina'] == 1 else [] if path == '/pedidos/vendas' else dict(self.order(), loja=dict(id=99))
        self.assertEqual(self.crm.sync(), 0)
        row = self.crm.normalize(self.order(status=123), dict(id=1, nome='Example'))
        with self.store.connect() as db:
            db.execute('INSERT INTO crm_orders VALUES (?,?)', (row['key'], json.dumps(row)))
        self.assertIn('A conferir', self.crm.page())
        self.assertNotIn('Compra única', self.crm.page())


    def test_confirmed_orders_exclude_test_and_separate_money(self):
        self.settings['BLING_STORE_ID'] = '206307158'
        self.crm = Bling(self.store, self.settings, self.transport)
        for oid, external in [(1, ''), (2, '2000015410851473'), (3, '2000015447855219')]:
            order = self.order(oid, oid, status=6, external=external)
            order['loja'] = {'id': 206307158}
            order['totalProdutos'] = 19.94
            order['total'] = 30.93 if oid == 3 else 19.94
            order['transporte'] = {'frete': 10.99 if oid == 3 else 0}
            row = self.crm.normalize(order, {'id': oid, 'nome': 'Test' if oid == 1 else 'Buyer ' + str(oid)})
            with self.store.connect() as db:
                db.execute('INSERT INTO crm_orders VALUES (?,?)', (row['key'], json.dumps(row)))
        rows, customers, *_ = self.crm.snapshot()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(customers), 2)
        self.assertTrue(all(self.crm.purchase_valid(r) for r in rows))
        panel = self.crm.page()
        self.assertEqual(panel.count('Compra única'), 2)
        self.assertNotIn('<td>Test</td>', panel)
        self.assertIn('R$ 19,94', panel)
        self.assertIn('R$ 10,99', panel)
        self.assertIn('R$ 30,93', panel)
        self.assertIn('Entregue — confirmado pelo responsável', panel)
        self.assertIn('13/10/2026', panel)
        self.assertIn('ainda não está conectado', panel)
        self.crm.excluded = {'6'}
        self.assertFalse(any(self.crm.purchase_valid(r) for r in rows))
        self.assertTrue(all(self.crm.delivery_action(r) == 'Excluído da recorrência' for r in rows))

    def test_missing_values_not_inferred_and_unknown_order_not_confirmed(self):
        row = self.crm.normalize(self.order(status=123), {'id': 1})
        self.assertIsNone(row['products_total'])
        self.assertIsNone(row['freight'])
        self.assertEqual(self.crm.products_sum([row]), 'Aguardando atualização')
        self.assertFalse(self.crm.purchase_valid(row))


if __name__ == '__main__':
    unittest.main()
