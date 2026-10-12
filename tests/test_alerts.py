from datetime import datetime, timezone
import unittest
from alerts import Alerts, RECIPIENTS
import test_bling


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_bling.BlingTests()
        self.fixture.setUp()
        self.crm = self.fixture.crm
        self.sent = []
        self.alerts = Alerts(self.crm, {'EMAIL_ENABLED': 'true'}, lambda *args: self.sent.append(args))

    def tearDown(self):
        self.fixture.tearDown()

    def test_daily_slots_local_time_and_restart_dedup(self):
        self.alerts.tick(datetime(2026, 10, 11, 14, 59, tzinfo=timezone.utc))
        self.assertEqual(len(self.sent), 0)
        now = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
        self.alerts.tick(now)
        self.alerts.tick(now)
        restarted = Alerts(self.crm, {'EMAIL_ENABLED': 'true'}, lambda *args: self.sent.append(args))
        restarted.tick(now)
        self.assertEqual([s[0] for s in self.sent], list(RECIPIENTS))
        restarted.tick(datetime(2026, 10, 11, 19, 30, tzinfo=timezone.utc))
        self.assertEqual(len(self.sent), 6)
        restarted.tick(datetime(2026, 10, 12, 15, 15, tzinfo=timezone.utc))
        self.assertEqual(len(self.sent), 6)

    def test_failed_or_uncertain_test_not_repeated_and_other_recipients_attempted(self):
        def send(recipient, *args):
            self.sent.append(recipient)
            if recipient == RECIPIENTS[0]:
                raise RuntimeError('secret must not be stored')
        self.alerts.sender = send
        self.alerts.settings = {'EMAIL_TEST_ID': 'first', 'EMAIL_ENABLED': 'false'}
        now = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
        self.alerts.tick(now)
        self.alerts.tick(now)
        self.assertEqual(self.sent, list(RECIPIENTS))
        label, rows = self.alerts.status()
        self.assertIn('desativados', label)
        self.assertTrue(any('incerto' in r for r in rows))
        self.assertFalse(any('secret' in r for r in rows))

    def test_summary_marks_old_data_and_omits_test_orders_and_personal_data(self):
        row = self.crm.normalize(self.fixture.order(), {'id': 1, 'nome': 'PRIVATE NAME', 'numeroDocumento': '12345678909'})
        import json
        with self.crm.store.connect() as db:
            db.execute('INSERT INTO crm_orders VALUES (?,?)', (row['key'], json.dumps(row)))
            self.crm.put(db, 'tokens', 'connected')
            self.crm.put(db, 'last_sync', '2026-10-11T14:00:00+00:00')
        now = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
        body = self.alerts.summary(now)
        self.assertIn('desatualizada', body)
        self.assertIn('Pedido Bling 1', body)
        self.assertNotIn('PRIVATE NAME', body)
        self.assertNotIn('12345678909', body)
        self.crm.rules['excluded_bling_numbers'] = ['1']
        self.assertNotIn('Pedido Bling 1', self.alerts.summary(now))

    def test_delivered_omitted_but_stale_failed_and_open_shipments_remain(self):
        from unittest.mock import Mock
        import json
        now = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
        row = self.crm.normalize(self.fixture.order(), {'id': 1})
        with self.crm.store.connect() as db:
            db.execute('INSERT INTO crm_orders VALUES (?,?)', (row['key'], json.dumps(row)))
        self.crm.meli = Mock()
        self.crm.meli.action.return_value = 'API status'
        self.crm.meli.lookup.return_value = {'status': 'delivered', 'checked_at': now.isoformat()}
        self.assertIn('ENTREGA CONFIRMADA', self.alerts.summary(now))
        self.alerts.tick(now)
        self.assertTrue(all('ENTREGA CONFIRMADA' in mail[2] for mail in self.sent))
        for recipient in RECIPIENTS:
            self.assertNotIn('Pedido Bling 1', self.alerts.summary(now, recipient))
            self.assertIn('Nenhum pedido pendente', self.alerts.summary(now, recipient))
        self.assertEqual(len(self.crm.snapshot()[0]), 1)
        for data in ({'status': 'delivered', 'checked_at': '2026-10-11T14:00:00+00:00'},
                     {'status': 'delivered', 'checked_at': now.isoformat(), 'error': 'API failed'},
                     {'status': 'pending', 'checked_at': now.isoformat()},
                     {'status': 'delivered', 'checked_at': 'invalid'}):
            self.crm.meli.lookup.return_value = data
            self.assertIn('Pedido Bling 1', self.alerts.summary(now))

    def test_completion_recorded_only_for_successful_recipient_and_survives_restart(self):
        from unittest.mock import Mock
        import json
        now = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
        row = self.crm.normalize(self.fixture.order(), {'id': 1})
        with self.crm.store.connect() as db:
            db.execute('INSERT INTO crm_orders VALUES (?,?)', (row['key'], json.dumps(row)))
        self.crm.meli = Mock()
        self.crm.meli.lookup.return_value = {'status': 'delivered', 'checked_at': now.isoformat()}
        def send(recipient, *args):
            if recipient == RECIPIENTS[0]:
                raise RuntimeError('failed')
        self.alerts.sender = send
        self.alerts.tick(now)
        restarted = Alerts(self.crm, {'EMAIL_ENABLED': 'true'}, lambda *args: None)
        self.assertIn('ENTREGA CONFIRMADA', restarted.summary(now, RECIPIENTS[0]))
        self.assertNotIn('Pedido Bling 1', restarted.summary(now, RECIPIENTS[1]))
