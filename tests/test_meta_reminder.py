from datetime import datetime, timezone
import os
import tempfile
import unittest
from unittest.mock import Mock
from cryptography.fernet import Fernet
from app import Store
from monitor import Monitor
from fiorella import TEST_PHONE, ProviderError
from meta_reminder import MetaReminder, TEMPLATE_NAME, TEST_TRIGGER, EXPECTED_WABA, EXPECTED_PHONE_ID


class MetaReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = Monitor(Store(local_path=os.path.join(self.tmp.name, 'db')), dict(CRM_ADMIN_PASSWORD='private-test', BLING_ENCRYPTION_KEY=Fernet.generate_key().decode()))
        self.settings = dict(WHATSAPP_ACCESS_TOKEN='private-token', WHATSAPP_BUSINESS_ACCOUNT_ID=EXPECTED_WABA, WHATSAPP_PHONE_NUMBER_ID=EXPECTED_PHONE_ID)
        self.now = datetime(2026, 10, 21, 15, tzinfo=timezone.utc)
        self.calls = []
        self.template = dict(id='approved-id', name=TEMPLATE_NAME, status='APPROVED', category='UTILITY', language='pt_BR', components=[dict(type='BODY', text='Fiorella: desculpe a demora. Nosso atendimento continua pendente.')])
        self.event = dict(assistantId=self.monitor.agent, channel='CLOUD_API', contextId='chat-1', contactPhone=TEST_PHONE, messageId='in-1', role='user', date=self.now.isoformat(), message=TEST_TRIGGER)
        self.fail = False
        self.meta = MetaReminder(self.monitor, self.settings, self.transport)

    def tearDown(self):
        self.tmp.cleanup()

    def transport(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == 'POST':
            if self.fail:
                raise ProviderError('Meta HTTP 500')
            return {'messages': [{'id': 'wamid.test'}]}
        if '/message_templates?' in path:
            return {'data': [self.template]}
        if '/phone_numbers?' in path:
            return {'data': [{'id': EXPECTED_PHONE_ID}]}
        raise AssertionError(path)

    def test_readonly_probe_and_private_status(self):
        result = self.meta.probe()
        self.assertTrue(result['ready'])
        self.assertFalse(any(c[0] == 'POST' for c in self.calls))
        self.assertNotIn('private-token', str(result))
        self.assertNotIn('private-token', str(self.meta.status()))

    def test_only_approved_matching_utility_plain_template(self):
        for field, bad in [('status', 'PENDING'), ('language', 'en_US'), ('name', 'other'), ('category', 'MARKETING'), ('components', [{'type': 'BODY', 'text': 'Hi {{1}}'}])]:
            old = self.template[field]
            self.template[field] = bad
            with self.assertRaises(ProviderError):
                self.meta.send_test(self.event, self.now)
            self.template[field] = old
        self.assertFalse(any(c[0] == 'POST' for c in self.calls))

    def test_owner_only_exact_payload_and_durable_dedup(self):
        self.meta.send_test(dict(self.event, contactPhone='5537999999999'), self.now)
        self.meta.send_test(dict(self.event, message='another text'), self.now)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.meta.send_test(self.event, self.now), 'accepted')
        post = [c for c in self.calls if c[0] == 'POST'][0]
        self.assertEqual(post[1], EXPECTED_PHONE_ID + '/messages')
        self.assertEqual(post[2]['to'], TEST_PHONE)
        self.assertEqual(post[2]['type'], 'template')
        self.assertEqual(post[2]['template'], {'name': TEMPLATE_NAME, 'language': {'code': 'pt_BR'}})
        restarted = MetaReminder(self.monitor, self.settings, self.transport)
        self.assertEqual(restarted.send_test(self.event, self.now), 'already attempted')
        self.assertEqual(sum(c[0] == 'POST' for c in self.calls), 1)
        with self.monitor.store.connect() as db:
            encrypted = db.execute('SELECT message_id FROM meta_reminder_attempts').fetchone()[0]
        self.assertNotIn('wamid.test', encrypted)

    def test_uncertain_send_is_not_repeated(self):
        self.fail = True
        with self.assertRaises(ProviderError):
            self.meta.send_test(self.event, self.now)
        self.assertEqual(self.meta.status()['attempts'], ['uncertain'])
        self.assertEqual(self.meta.send_test(self.event, self.now), 'already attempted')

    def test_template_echo_does_not_clear_human_wait(self):
        self.monitor.save(self.event, self.now)
        self.meta.approved_template()
        self.monitor.save(dict(self.event, messageId='gpt-provider-echo-id', role='assistant', message=self.template['components'][0]['text']), self.now)
        self.assertIsNotNone(self.monitor.conversations(self.now)[0]['pending_since'])

    def test_wrong_account_is_not_used(self):
        self.settings['WHATSAPP_PHONE_NUMBER_ID'] = 'other'
        self.assertFalse(self.meta.probe()['ready'])
        self.assertEqual(self.calls, [])

    def test_delivery_probe_separates_acceptance_and_delivery_without_resending(self):
        self.meta.send_test(self.event, self.now)
        sender = Mock()
        sender.test_chat.return_value = {'id': 'chat-1'}
        sender.segment.return_value = 'chat-1'
        sender.read_list.return_value = [{'id': 'internal-id', 'externalId': 'wamid.test', 'hasError': True, 'metadata': {'status': 'failed', 'error': {'code': 131042, 'message': 'private provider text'}}}]
        self.monitor.fiorella = sender
        result = self.meta.delivery_probe()
        self.assertEqual(result['provider_history_matches'], 1)
        self.assertEqual(result['provider_error_codes'], [131042])
        self.assertFalse(result['delivery_confirmed'])
        self.assertNotIn('private provider text', str(result))
        self.assertNotIn('wamid.test', str(result))
        self.assertEqual(sum(c[0] == 'POST' for c in self.calls), 1)
        sender.read_list.return_value[0]['metadata'] = {'status': 'delivered'}
        self.assertTrue(self.meta.delivery_probe()['delivery_confirmed'])

    def test_missing_provider_history_does_not_prove_delivery(self):
        sender = Mock()
        sender.test_chat.return_value = {'id': 'chat-1'}
        sender.segment.return_value = 'chat-1'
        sender.read_list.return_value = []
        self.monitor.fiorella = sender
        self.assertFalse(self.meta.delivery_probe()['delivery_confirmed'])
