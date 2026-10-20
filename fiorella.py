"""Fixed greeting pilot, restricted to the owner's existing test conversation."""
from datetime import datetime, timezone
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from monitor import FIORELLA_GREETING

TEST_PHONE = '553197118070'
TEST_TRIGGER = 'Teste Fiorella'


class ProviderError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Fiorella:
    def __init__(self, monitor, token, transport=None):
        self.monitor, self.token = monitor, token
        self.transport = transport or self.request
        self.p = monitor.placeholder
        with monitor.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS fiorella_pilot (job TEXT PRIMARY KEY, state TEXT NOT NULL, updated TEXT NOT NULL)')

    def request(self, method, path, body=None):
        # URL and payload are built here; caller cannot supply another host.
        req = urllib.request.Request('https://api.gptmaker.ai/v2' + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=8) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ProviderError('provider response too large')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            raise ProviderError('provider HTTP ' + str(error.code)) from None
        except (OSError, ValueError):
            raise ProviderError('provider unavailable') from None

    def read_list(self, path):
        data = self.transport('GET', path)
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ProviderError('unexpected provider schema')
        return data

    @staticmethod
    def segment(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
            raise ProviderError('invalid provider identifier')
        return value

    def test_chat(self):
        matches = []
        for workspace in self.read_list('/workspaces')[:3]:
            workspace_id = self.segment(workspace.get('id'))
            # Restrict to the configured agent, then verify returned agent and phone.
            query = urllib.parse.urlencode({'agentId': self.monitor.agent, 'page': 1, 'pageSize': 100})
            chats = self.read_list('/workspace/' + workspace_id + '/chats?' + query)
            for chat in chats:
                phone = re.sub(r'\D', '', str(chat.get('whatsappPhone', '')))
                if chat.get('agentId') == self.monitor.agent and phone == TEST_PHONE:
                    self.segment(chat.get('id'))
                    matches.append(chat)
        if len(matches) != 1:
            raise ProviderError('test conversation not uniquely identified')
        return matches[0]

    def probe(self):
        if not self.token:
            return {'ready': False, 'status': 'missing API key'}
        try:
            chat = self.test_chat()
            messages = self.read_list('/chat/' + self.segment(chat['id']) + '/messages')
            local = [r for r in self.monitor.latest() if r.get('contactPhone') == TEST_PHONE]
            return {'ready': True, 'status': 'read-only access confirmed',
                'chat_fields': sorted(chat), 'message_fields': sorted({key for message in messages for key in message}),
                'context_matches': any(r['contextId'] == chat['id'] for r in local),
                'message_id_matches': any(m.get('id') == r['messageId'] for m in messages for r in local),
                'pilot': 'owner only; exact trigger: ' + TEST_TRIGGER}
        except ProviderError as error:
            return {'ready': False, 'status': str(error)}

    def maybe_send(self, event, now=None):
        now = now or datetime.now(timezone.utc)
        if not self.token or event.get('role') != 'user' or event.get('contactPhone') != TEST_PHONE or event.get('message', '').strip() != TEST_TRIGGER:
            return 'not eligible'
        age = (now - datetime.fromisoformat(event['date'].replace('Z', '+00:00'))).total_seconds()
        if not 0 <= age <= 300:
            return 'old event ignored'
        chat = self.test_chat()
        if event.get('contextId') != chat['id']:
            raise ProviderError('context mismatch')
        messages = self.read_list('/chat/' + self.segment(chat['id']) + '/messages')
        canonical = [m for m in messages if m.get('id') == event.get('messageId') and m.get('role') == 'user' and m.get('text', '').strip() == TEST_TRIGGER]
        if len(canonical) != 1:
            raise ProviderError('incoming message not verified')
        # Verify freshness from provider, not just the public incoming webhook.
        timestamp = canonical[0].get('time')
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            raise ProviderError('provider timestamp missing')
        timestamp = timestamp / 1000 if timestamp > 100000000000 else timestamp
        if not 0 <= now.timestamp() - timestamp <= 300:
            raise ProviderError('provider message not recent')
        job = self.monitor.digest('fiorella-pilot-v1:' + chat['id'])
        with self.monitor.store.connect() as db:
            claimed = db.execute(f'INSERT INTO fiorella_pilot VALUES ({self.p},{self.p},{self.p}) ON CONFLICT DO NOTHING RETURNING job', (job, 'sending', now.isoformat())).fetchone()
        if not claimed:
            return 'already attempted'
        state = 'uncertain'
        try:
            result = self.transport('POST', '/chat/' + self.segment(chat['id']) + '/send-message', {'message': FIORELLA_GREETING})
            if isinstance(result, dict) and result.get('success') is True:
                state = 'accepted'
                # The documented send response has no message ID. Reconcile using
                # canonical history; the exact greeting also keeps the clock running.
                try:
                    after = self.read_list('/chat/' + self.segment(chat['id']) + '/messages')
                    for message in after:
                        if message.get('role') == 'assistant' and message.get('text', '').strip() == FIORELLA_GREETING and isinstance(message.get('id'), str):
                            self.monitor.mark_automatic(message['id'])
                except ProviderError:
                    pass
        finally:
            with self.monitor.store.connect() as db:
                db.execute(f'UPDATE fiorella_pilot SET state={self.p},updated={self.p} WHERE job={self.p}', (state, now.isoformat(), job))
        return state

    def status(self):
        with self.monitor.store.connect() as db:
            rows = db.execute('SELECT state FROM fiorella_pilot').fetchall()
        return {'key_configured': bool(self.token), 'mode': 'owner-only pilot', 'attempts': [r[0] for r in rows]}
