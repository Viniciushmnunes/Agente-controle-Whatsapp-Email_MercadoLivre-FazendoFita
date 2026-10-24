from datetime import datetime, timedelta, timezone
import base64
import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock

from cryptography.fernet import Fernet
from app import Store, Webhook
from monitor import Monitor, FIORELLA_GREETING
from meta_reminder import MetaReminder, EXPECTED_WABA, EXPECTED_PHONE_ID, TEMPLATE_NAME
from operation import Operation
from fiorella import ProviderError


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = Monitor(Store(local_path=os.path.join(self.tmp.name, 'db')), dict(CRM_ADMIN_PASSWORD='private-test', BLING_ENCRYPTION_KEY=Fernet.generate_key().decode()))
        self.now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        self.phone, self.context = '5531888881234', 'customer-chat'
        self.chat = dict(id=self.context, recipient=self.phone, channelId='commercial-channel', finished=False, isGroup=False, blocked=False, channelIsConnected=True)
        self.messages, self.posts = [], []
        self.fail_greeting = False
        self.sender = Mock()
        self.sender.test_chat.return_value = dict(id='reference-chat', channelId='commercial-channel', channelIsConnected=True)
        self.sender.segment.side_effect = lambda x: x
        self.sender.read_list.side_effect = self.read_list
        self.sender.transport.side_effect = self.transport
        settings = dict(WHATSAPP_ACCESS_TOKEN='private-token', WHATSAPP_BUSINESS_ACCOUNT_ID=EXPECTED_WABA, WHATSAPP_PHONE_NUMBER_ID=EXPECTED_PHONE_ID)
        self.sender.meta = MetaReminder(self.monitor, settings, self.meta_transport)
        self.monitor.fiorella = self.sender
        self.operation = Operation(self.monitor)
        self.monitor.operation = self.operation
        self.operation.set_enabled(True, self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def read_list(self, path):
        if path == '/workspaces':
            return [{'id': 'workspace'}]
        if '/chats?' in path:
            return [dict(self.chat)]
        if path == '/chat/reference-chat/messages':
            return [dict(id='reference-1', role='user', time=int(self.now.timestamp()*1000))]
        if path.endswith('/messages'):
            return list(self.messages)
        raise AssertionError(path)

    def transport(self, method, path, body):
        self.posts.append(('greeting', path, body))
        if self.fail_greeting:
            raise ProviderError('provider unavailable')
        self.messages.append(dict(id='automatic-' + str(len(self.posts)), role='assistant', text=body['message'], time=int(self.now.timestamp()*1000)))
        return {'success': True}

    def meta_transport(self, method, path, body=None):
        if method == 'POST':
            self.posts.append(('reminder', path, body))
            return {'messages': [{'id': 'wamid.reminder'}]}
        if '/message_templates?' in path:
            return {'data': [dict(name=TEMPLATE_NAME, language='pt_BR', status='APPROVED', category='UTILITY', components=[{'type': 'BODY', 'text': 'Fiorella: desculpe a demora.'}])]}
        if '/phone_numbers?' in path:
            return {'data': [{'id': EXPECTED_PHONE_ID}]}
        raise AssertionError(path)

    def event(self, identifier='incoming-1', date=None, role='user', text='Olá, preciso de atendimento', store=True):
        date = date or self.now
        event = dict(assistantId=self.monitor.agent, channel='CLOUD_API', messageId=identifier, contextId=self.context, contactPhone=self.phone, contactName='PRIVATE NAME', role=role, message=text, date=date.isoformat())
        self.messages.append(dict(id=identifier, role=role, text=text, time=int(date.timestamp()*1000)))
        if store:
            self.monitor.save(event, date)
        return event

    def test_general_customer_greeting_and_duplicates_after_restart(self):
        self.event()
        self.operation.tick(self.now)
        self.event('incoming-2', self.now + timedelta(seconds=20))
        self.operation.tick(self.now + timedelta(seconds=20))
        restarted = Operation(self.monitor)
        restarted.tick(self.now + timedelta(seconds=30))
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0][2], {'message': FIORELLA_GREETING})
        self.assertIsNotNone(self.monitor.conversations(self.now)[0]['pending_since'])
        with self.monitor.store.connect() as db:
            encrypted = db.execute('SELECT payload FROM operation_sessions').fetchone()[0]
        self.assertNotIn(self.phone, encrypted)
        self.assertNotIn(self.context, encrypted)

    def test_reminder_once_after_24_hours_without_new_customer_message(self):
        self.event()
        self.operation.tick(self.now)
        self.operation.tick(self.now + timedelta(hours=24) - timedelta(seconds=1))
        self.assertEqual(len(self.posts), 1)
        self.operation.tick(self.now + timedelta(hours=24))
        self.operation.tick(self.now + timedelta(hours=48))
        self.assertEqual([p[0] for p in self.posts], ['greeting', 'reminder'])
        self.assertEqual(self.posts[-1][2]['to'], self.phone)
        self.assertTrue(self.monitor.conversations(self.now + timedelta(hours=24))[0]['critical'])

    def test_new_customer_messages_neither_reset_clock_nor_repeat_greeting(self):
        self.event()
        self.operation.tick(self.now)
        self.event('next', self.now + timedelta(hours=23))
        self.operation.tick(self.now + timedelta(hours=23))
        self.operation.tick(self.now + timedelta(hours=24))
        self.assertEqual([p[0] for p in self.posts], ['greeting', 'reminder'])

    def test_human_reply_cancels_reminder(self):
        self.event()
        self.operation.tick(self.now)
        self.event('human', self.now + timedelta(minutes=10), 'assistant', 'Já vou te atender')
        self.operation.tick(self.now + timedelta(hours=24))
        self.assertEqual(len(self.posts), 1)
        self.assertIsNone(self.monitor.conversations(self.now + timedelta(hours=24))[0]['pending_since'])

    def test_human_reply_before_worker_suppresses_greeting(self):
        self.event()
        self.event('human', self.now + timedelta(seconds=1), 'assistant', 'Oi')
        self.operation.tick(self.now + timedelta(seconds=2))
        self.assertEqual(self.posts, [])

    def test_human_reply_same_second_as_greeting_cancels_reminder(self):
        self.event()
        self.operation.tick(self.now + timedelta(microseconds=500000))
        self.event('human', self.now, 'assistant', 'Oi')
        self.operation.tick(self.now + timedelta(hours=25))
        self.assertEqual(len(self.posts), 1)

    def test_ongoing_human_exchange_not_greeted_again_but_24h_return_is(self):
        self.event()
        self.operation.tick(self.now)
        self.event('human', self.now + timedelta(minutes=1), 'assistant', 'Oi')
        self.event('next', self.now + timedelta(minutes=2))
        self.operation.tick(self.now + timedelta(minutes=2))
        self.assertEqual(len(self.posts), 1)
        later = self.now + timedelta(hours=25)
        self.event('new-attendance', later)
        self.operation.tick(later)
        self.assertEqual(len(self.posts), 2)

    def test_optout_and_blocked_contacts_are_not_messaged(self):
        self.event(text='Pare')
        self.operation.tick(self.now)
        self.event('next', self.now + timedelta(seconds=10))
        self.operation.tick(self.now + timedelta(seconds=10))
        self.assertEqual(self.posts, [])
        self.chat['blocked'] = True
        self.operation.cache_at = None
        with self.assertRaises(ProviderError):
            self.operation.canonical(self.context, self.phone, self.now)

    def test_wrong_channel_or_forged_event_is_rejected(self):
        event = self.event(store=False)
        with self.assertRaises(ProviderError):
            self.operation.handle_event(dict(event, messageId='forged'), self.now)
        self.chat['channelId'] = 'another-channel'
        self.operation.cache_at = None
        with self.assertRaises(ProviderError):
            self.operation.handle_event(event, self.now)
        self.assertEqual(self.posts, [])

    def test_old_messages_and_pause_do_not_trigger_campaign(self):
        self.event(date=self.now - timedelta(minutes=1))
        self.operation.tick(self.now)
        self.assertEqual(self.posts, [])
        self.operation.set_enabled(False, self.now)
        self.event('paused', self.now + timedelta(minutes=1))
        self.operation.tick(self.now + timedelta(minutes=1))
        self.operation.set_enabled(True, self.now + timedelta(minutes=2))
        self.operation.tick(self.now + timedelta(minutes=2))
        self.assertEqual(self.posts, [])

    def test_uncertain_greeting_not_retried(self):
        self.fail_greeting = True
        self.event()
        self.operation.tick(self.now)
        self.operation.tick(self.now + timedelta(seconds=5))
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.operation.status()['attempts'][0]['state'], 'uncertain')

    def test_private_pause_control_requires_authentication_and_csrf(self):
        app = Webhook('verify', 'secret', self.monitor.store)
        app.monitor = self.monitor
        def request(auth, csrf):
            raw = json.dumps({'enabled': False, 'csrf': csrf}).encode()
            env = {'PATH_INFO': '/monitor/operation', 'REQUEST_METHOD': 'POST', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
            if auth:
                env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(b'admin:private-test').decode()
            result = []
            b''.join(app(env, lambda status, headers: result.append(status)))
            return result[0]
        self.assertEqual(request(False, ''), '401 Unauthorized')
        self.assertEqual(request(True, 'wrong'), '403 Forbidden')
        self.assertTrue(self.operation.control()[0])
        self.assertEqual(request(True, self.monitor.digest('operation-toggle')), '200 OK')
        self.assertFalse(self.operation.control()[0])

    def test_missed_webhook_is_recovered_from_recent_canonical_history(self):
        self.chat['time'] = int(self.now.timestamp()*1000)
        self.event(store=False)
        self.operation.tick(self.now)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.monitor.latest()), 1)
        self.operation.tick(self.now + timedelta(seconds=61))
        self.assertEqual(len(self.posts), 1)

    def test_direct_intake_normalizes_from_api_and_rejects_unknown_message(self):
        self.now = datetime.now(timezone.utc)
        event = self.event(store=False)
        app = Webhook('verify', 'secret', self.monitor.store)
        app.monitor = self.monitor
        def request(payload, path):
            raw = json.dumps(payload).encode()
            env = {'PATH_INFO': path, 'REQUEST_METHOD': 'POST', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
            result = []
            b''.join(app(env, lambda status, headers: result.append(status)))
            return result[0]
        self.assertEqual(request(event, '/gptmaker/events/wrong-secret'), '404 Not Found')
        self.assertEqual(request(dict(event, message='Forged text'), self.operation.ingress_path()), '200 OK')
        self.assertEqual(self.monitor.latest()[0]['message'], event['message'])
        self.assertEqual(request(dict(event, messageId='fake'), self.operation.ingress_path()), '503 Service Unavailable')

    def test_connect_backs_up_existing_settings_and_preserves_other_events(self):
        existing = {'onNewMessage': 'https://old.example/webhook', 'onTransfer': 'https://transfer.example/event'}
        def provider(method, path, body=None):
            if method == 'GET':
                return dict(existing)
            if method == 'PUT':
                existing.update(body)
                return {'success': True}
            raise AssertionError(method)
        self.sender.transport.side_effect = provider
        self.assertTrue(self.operation.connect()['connected'])
        self.assertEqual(existing['onTransfer'], 'https://transfer.example/event')
        self.assertTrue(existing['onNewMessage'].endswith(self.operation.ingress_path()))
        with self.monitor.store.connect() as db:
            encrypted = db.execute('SELECT payload FROM operation_webhook_backup').fetchone()[0]
        self.assertNotIn('old.example', encrypted)
        backup = json.loads(self.monitor.cipher.decrypt(encrypted.encode()))
        self.assertEqual(backup['onNewMessage'], 'https://old.example/webhook')

    def test_uncertain_reminder_is_not_retried(self):
        self.event()
        self.operation.tick(self.now)
        self.sender.meta.send_template = Mock(side_effect=ProviderError('Meta unavailable'))
        with self.assertRaises(ProviderError):
            self.operation.tick(self.now + timedelta(hours=24))
        self.operation.tick(self.now + timedelta(hours=25))
        self.sender.meta.send_template.assert_called_once()
        self.assertTrue(any(r['kind'] == 'reminder' and r['state'] == 'uncertain' for r in self.operation.status()['attempts']))
