from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock
import os
import tempfile

from cryptography.fernet import Fernet
from app import Store
from monitor import Monitor, FIORELLA_GREETING
from fiorella import Fiorella, ProviderError, TEST_PHONE, TEST_TRIGGER


class FiorellaTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = Monitor(Store(local_path=os.path.join(self.tmp.name, 'db')), dict(CRM_ADMIN_PASSWORD='test-password', BLING_ENCRYPTION_KEY=Fernet.generate_key().decode()))
        self.now = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
        self.calls = []
        self.event = dict(assistantId=self.monitor.agent, channel='CLOUD_API', contextId='chat-1', messageId='incoming-1', role='user', contactPhone=TEST_PHONE, contactName='Owner', message=TEST_TRIGGER, date=self.now.isoformat())
        self.messages = [dict(id='incoming-1', role='user', text=TEST_TRIGGER, time=int(self.now.timestamp()*1000))]
        self.chat = dict(id='chat-1', agentId=self.monitor.agent, whatsappPhone=TEST_PHONE)
        self.fail_send = False
        self.sender = Fiorella(self.monitor, 'private-token', self.transport)

    def tearDown(self):
        self.tmp.cleanup()

    def transport(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == 'POST':
            if self.fail_send:
                raise ProviderError('provider unavailable')
            self.messages.append(dict(id='outgoing-1', role='assistant', text=body['message'], time=int(self.now.timestamp()*1000)))
            return {'success': True}
        if path == '/workspaces':
            return [{'id': 'workspace-1'}]
        if '/chats?' in path:
            return [self.chat]
        if path == '/chat/chat-1/messages':
            return list(self.messages)
        raise AssertionError(path)

    def test_only_owner_exact_trigger_recent_message_and_configured_key(self):
        for event in [dict(self.event, contactPhone='5537999999999'), dict(self.event, message='Hi'), dict(self.event, role='assistant'), dict(self.event, date='2026-10-19T18:00:00Z')]:
            self.sender.maybe_send(event, self.now)
        Fiorella(self.monitor, '', self.transport).maybe_send(self.event, self.now)
        self.assertEqual(self.calls, [])

    def test_canonical_phone_agent_context_message_and_time_required(self):
        for field, bad in [('agentId', 'other'), ('whatsappPhone', '5537999999999')]:
            original = self.chat[field]
            self.chat[field] = bad
            with self.assertRaises(ProviderError):
                self.sender.maybe_send(self.event, self.now)
            self.chat[field] = original
        with self.assertRaises(ProviderError):
            self.sender.maybe_send(dict(self.event, contextId='wrong'), self.now)
        with self.assertRaises(ProviderError):
            self.sender.maybe_send(dict(self.event, messageId='forged'), self.now)
        self.messages[0]['time'] = 1
        with self.assertRaises(ProviderError):
            self.sender.maybe_send(self.event, self.now)
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_once_across_retries_restarts_and_new_client_messages(self):
        self.monitor.save(self.event, self.now)
        self.assertEqual(self.sender.maybe_send(self.event, self.now), 'accepted')
        self.assertEqual(self.calls[-2][2], {'message': FIORELLA_GREETING})
        restarted = Fiorella(self.monitor, 'private-token', self.transport)
        self.assertEqual(restarted.maybe_send(self.event, self.now), 'already attempted')
        self.messages.append(dict(self.messages[0], id='incoming-2'))
        self.assertEqual(restarted.maybe_send(dict(self.event, messageId='incoming-2'), self.now), 'already attempted')
        self.assertEqual(sum(call[0] == 'POST' for call in self.calls), 1)
        self.monitor.save(dict(self.event, messageId='outgoing-1', role='assistant', message=FIORELLA_GREETING), self.now)
        self.assertIsNotNone(self.monitor.conversations(self.now)[0]['pending_since'])

    def test_uncertain_send_is_not_retried(self):
        self.fail_send = True
        with self.assertRaises(ProviderError):
            self.sender.maybe_send(self.event, self.now)
        self.assertEqual(self.sender.status()['attempts'], ['uncertain'])
        self.assertEqual(self.sender.maybe_send(self.event, self.now), 'already attempted')
        self.assertEqual(sum(call[0] == 'POST' for call in self.calls), 1)

    def test_probe_only_reads_and_does_not_expose_token_or_customer_content(self):
        self.monitor.save(self.event, self.now)
        result = self.sender.probe()
        self.assertTrue(result['ready'])
        self.assertTrue(result['context_matches'])
        self.assertTrue(result['message_id_matches'])
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))
        self.assertNotIn('private-token', str(result))
        self.assertNotIn(TEST_PHONE, str(result))
        self.assertNotIn('Owner', str(result))

    def test_path_injection_and_redirects_are_not_allowed(self):
        with self.assertRaises(ProviderError):
            Fiorella.segment('../other')
        from fiorella import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))
