import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from cryptography.fernet import Fernet
from app import Store
from crm_bridge import CrmBridge
from monitor import Monitor


class CrmBridgeTests(unittest.TestCase):
    def test_signature_and_payload_are_server_side(self):
        calls = []

        def transport(url, headers, raw):
            calls.append((url, headers, raw))
            return 201

        bridge = CrmBridge('https://fazendofita.com.br/api/crm/bridge/messages', 'dedicated-secret', transport)
        event = {
            'messageId': 'wamid.1', 'contactPhone': '5537984133209', 'contactName': 'Contato',
            'message': 'Quero saber o valor', 'date': '2026-10-20T16:00:00+00:00', 'role': 'user',
        }
        self.assertTrue(bridge.forward(event))
        self.assertEqual(len(calls), 1)
        url, headers, raw = calls[0]
        self.assertTrue(url.startswith('https://'))
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(headers['User-Agent'], 'FazendoFita-CRM-Bridge/1.0')
        payload = json.loads(raw)
        self.assertEqual(payload['direction'], 'inbound')
        self.assertFalse(payload['automatic'])
        self.assertNotIn('dedicated-secret', raw.decode())
        self.assertTrue(headers['X-CRM-Signature'].startswith('sha256='))

    def test_outbound_automatic_payload(self):
        received = {}
        bridge = CrmBridge('https://crm.example.test/bridge', 'secret', lambda url, headers, raw: received.update(json.loads(raw)) or 200)
        bridge.forward({
            'messageId': 'wamid.2', 'contactPhone': '5537984133209', 'contactName': 'Contato',
            'message': 'Mensagem automática', 'date': '2026-10-20T16:00:00+00:00', 'role': 'assistant',
        }, automatic=True)
        self.assertEqual(received['direction'], 'outbound')
        self.assertTrue(received['automatic'])

    def test_requires_https_and_both_settings(self):
        with self.assertRaises(ValueError):
            CrmBridge('http://crm.example.test/bridge', 'secret')
        with self.assertRaises(ValueError):
            CrmBridge('https://crm.example.test/bridge', '')
        self.assertFalse(CrmBridge('', '').enabled)

    def monitor_settings(self, directory, encryption_key=None):
        store = Store(local_path=os.path.join(directory, 'monitor.db'))
        monitor = Monitor(store, {
            'CRM_ADMIN_PASSWORD': 'private-test-password',
            'BLING_ENCRYPTION_KEY': encryption_key or Fernet.generate_key().decode(),
            'GPTMAKER_MONITOR_AGENT_ID': 'agent',
            'CRM_BRIDGE_URL': 'https://crm.example.test/bridge',
            'CRM_BRIDGE_HMAC_SECRET': 'secret',
        })
        return store, monitor

    def event(self, date):
        return {
            'assistantId': 'agent', 'channel': 'CLOUD_API', 'messageId': 'm1', 'contextId': 'c1',
            'role': 'user', 'date': date.isoformat(), 'contactName': 'Contato',
            'contactPhone': '5537984133209', 'message': 'Olá',
        }

    def test_outbox_success_and_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            _, monitor = self.monitor_settings(directory)
            monitor.bridge.transport = Mock(return_value=201)
            now = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
            monitor.save(self.event(now), now)
            with monitor.store.connect() as db:
                row = db.execute('SELECT status, attempts, delivered_at FROM crm_bridge_outbox').fetchone()
            self.assertEqual(row[0], 'delivered')
            self.assertEqual(row[1], 0)
            self.assertIsNotNone(row[2])

    def test_temporary_unavailable_is_pending_then_retried_with_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            _, monitor = self.monitor_settings(directory)
            monitor.bridge.transport = Mock(side_effect=[503, 201])
            now = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
            monitor.save(self.event(now), now)
            with monitor.store.connect() as db:
                status, attempts, next_attempt = db.execute('SELECT status, attempts, next_attempt_at FROM crm_bridge_outbox').fetchone()
            self.assertEqual(status, 'pending')
            self.assertEqual(attempts, 1)
            due = datetime.fromisoformat(next_attempt)
            self.assertGreaterEqual(due, now + timedelta(seconds=5))
            monitor.flush_crm_outbox(due)
            with monitor.store.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM crm_bridge_outbox').fetchone()[0], 'delivered')
            self.assertEqual(monitor.bridge.transport.call_count, 2)

    def test_duplicate_crm_response_is_delivery_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            _, monitor = self.monitor_settings(directory)
            monitor.bridge.transport = Mock(return_value=200)
            now = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
            monitor.save(self.event(now), now)
            with monitor.store.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM crm_bridge_outbox').fetchone()[0], 'delivered')

    def test_pending_outbox_survives_agent_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            encryption_key = Fernet.generate_key().decode()
            _, monitor = self.monitor_settings(directory, encryption_key)
            monitor.bridge.transport = Mock(return_value=503)
            now = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
            monitor.save(self.event(now), now)
            restarted = self.monitor_settings(directory, encryption_key)[1]
            restarted.bridge.transport = Mock(return_value=201)
            with restarted.store.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM crm_bridge_outbox').fetchone()[0], 'pending')
                due = datetime.fromisoformat(db.execute('SELECT next_attempt_at FROM crm_bridge_outbox').fetchone()[0])
            restarted.flush_crm_outbox(due)
            with restarted.store.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM crm_bridge_outbox').fetchone()[0], 'delivered')
            restarted.bridge.transport.assert_called_once()

    def test_monitor_forwards_only_new_persisted_events(self):
        with tempfile.TemporaryDirectory() as directory:
            _, monitor = self.monitor_settings(directory)
            monitor.bridge.transport = Mock(return_value=201)
            now = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
            monitor.save(self.event(now), now)
            monitor.save(self.event(now), now)
            monitor.bridge.transport.assert_called_once()


if __name__ == '__main__':
    unittest.main()
