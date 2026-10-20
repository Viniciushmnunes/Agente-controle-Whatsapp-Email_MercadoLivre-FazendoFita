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

    def test_whatsapp_wait_alert_is_once_per_episode_and_survives_restart(self):
        from monitor import Monitor
        monitor = Monitor(self.crm.store, self.fixture.settings)
        self.crm.monitor = monitor
        now = datetime(2026, 10, 20, 17, tzinfo=timezone.utc)
        event = dict(assistantId=monitor.agent, channel='CLOUD_API', messageId='customer-1', contextId='conversation-1', role='user', date='2026-10-20T16:00:00Z', contactName='PRIVATE NAME', contactPhone='5511999991234', message='PRIVATE MESSAGE')
        monitor.save(event, now)
        self.alerts.tick(datetime(2026, 10, 20, 16, 59, tzinfo=timezone.utc))
        self.assertEqual(self.sent, [])
        self.alerts.tick(now)
        self.assertEqual(len(self.sent), 3)
        self.assertIn('1234', self.sent[0][2])
        self.assertNotIn('PRIVATE NAME', self.sent[0][2])
        self.assertNotIn('PRIVATE MESSAGE', self.sent[0][2])
        self.assertNotIn('5511999991234', self.sent[0][2])
        monitor.save(dict(event, messageId='customer-2', date='2026-10-20T17:00:00Z'), now)
        restarted = Alerts(self.crm, {'EMAIL_ENABLED': 'true'}, lambda *args: self.sent.append(args))
        restarted.tick(now)
        self.assertEqual(len(self.sent), 3)
        monitor.save(dict(event, messageId='late-earlier-customer', date='2026-10-20T15:59:00Z'), now)
        restarted.tick(now)
        self.assertEqual(len(self.sent), 3)
        monitor.save(dict(event, messageId='human-1', role='assistant', date='2026-10-20T17:01:00Z'), now)
        restarted.tick(now)
        self.assertEqual(len(self.sent), 3)
        monitor.save(dict(event, messageId='customer-3', date='2026-10-20T17:02:00Z'), now)
        restarted.tick(datetime(2026, 10, 20, 18, 2, tzinfo=timezone.utc))
        self.assertEqual(len(self.sent), 6)

    def test_whatsapp_alerts_respect_disabled_email_setting(self):
        from unittest.mock import Mock
        self.crm.monitor = Mock()
        self.alerts.settings = {'EMAIL_ENABLED': 'false'}
        self.alerts.tick(datetime(2026, 10, 20, 17, tzinfo=timezone.utc))
        self.crm.monitor.overdue.assert_not_called()

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

    def test_cancelled_announced_once_per_recipient_and_delivery_receipt_does_not_hide_it(self):
        from unittest.mock import Mock
        import json
        now = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
        row = self.crm.normalize(self.fixture.order(), {'id': 1})
        with self.crm.store.connect() as db:
            db.execute('INSERT INTO crm_orders VALUES (?,?)', (row['key'], json.dumps(row)))
            db.execute('INSERT INTO email_completed_orders VALUES (?,?)', (RECIPIENTS[0], row['key']))
        self.crm.meli = Mock()
        self.crm.meli.action.return_value = 'Conferir cancelamento'
        self.crm.meli.lookup.return_value = {'status': 'cancelled', 'order_statuses': ['cancelled'], 'checked_at': now.isoformat()}
        self.assertIn('CANCELAMENTO CONFIRMADO', self.alerts.summary(now, RECIPIENTS[0]))
        def send(recipient, subject, body):
            self.sent.append((recipient, body))
            if recipient == RECIPIENTS[0]:
                raise RuntimeError('failed')
        self.alerts.sender = send
        self.alerts.tick(now)
        self.assertTrue(all('CANCELAMENTO CONFIRMADO' in body for _, body in self.sent))
        restarted = Alerts(self.crm, {'EMAIL_ENABLED': 'true'}, send)
        self.assertIn('CANCELAMENTO CONFIRMADO', restarted.summary(now, RECIPIENTS[0]))
        self.assertNotIn('Pedido Bling 1', restarted.summary(now, RECIPIENTS[1]))
        self.assertEqual(len(self.crm.snapshot()[0]), 1)
        for data in ({'status': 'cancelled', 'order_statuses': ['paid'], 'checked_at': now.isoformat()},
                     {'status': 'cancelled', 'order_statuses': ['cancelled'], 'checked_at': '2026-10-17T15:00:00+00:00'},
                     {'status': 'cancelled', 'order_statuses': ['cancelled'], 'checked_at': now.isoformat(), 'error': 'API failed'}):
            self.crm.meli.lookup.return_value = data
            body = restarted.summary(now, RECIPIENTS[1])
            self.assertIn('Pedido Bling 1', body)
            self.assertNotIn('CANCELAMENTO CONFIRMADO', body)
