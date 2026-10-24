"""Persistent production dispatcher: fixed greeting and one 24-hour reminder."""
from datetime import datetime, timedelta, timezone
import json
import hmac
import re
import threading
import time
import urllib.parse

from fiorella import ProviderError
from monitor import FIORELLA_GREETING


def instant(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, timezone.utc)
    raise ProviderError('message timestamp unavailable')


class Operation:
    def __init__(self, monitor):
        self.monitor, self.sender = monitor, monitor.fiorella
        self.p = monitor.placeholder
        self.ready, self.last_error, self.last_check = False, '', None
        self.channel_id = None
        self.cache, self.cache_at = {}, None
        self.last_reconcile = None
        self.lock = threading.Lock()
        with monitor.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operation_control (id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL, activated_at TEXT NOT NULL)')
            db.execute("INSERT INTO operation_control VALUES (1,0,'') ON CONFLICT DO NOTHING")
            db.execute('CREATE TABLE IF NOT EXISTS operation_events (event_key TEXT PRIMARY KEY, state TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS operation_sessions (context_key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS operation_attempts (job TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, updated TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS operation_webhook_backup (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')

    def ingress_path(self):
        return '/gptmaker/events/' + self.monitor.digest('gptmaker-ingress-v1')

    def accepts_path(self, path):
        return isinstance(path, str) and hmac.compare_digest(path.encode(), self.ingress_path().encode())

    def connect(self):
        if not self.preflight()['ready']:
            raise ProviderError('preflight failed')
        path = '/agent/' + self.sender.segment(self.monitor.agent) + '/webhooks'
        existing = self.sender.transport('GET', path)
        if not isinstance(existing, dict) or 'onNewMessage' not in existing:
            raise ProviderError('webhook settings unavailable')
        target = 'https://fazendo-fita-webhook.onrender.com' + self.ingress_path()
        if existing.get('onNewMessage') != target:
            encrypted = self.monitor.cipher.encrypt(json.dumps(existing).encode()).decode()
            with self.monitor.store.connect() as db:
                db.execute(f'INSERT INTO operation_webhook_backup VALUES (1,{self.p}) ON CONFLICT DO NOTHING', (encrypted,))
            allowed = ('onNewMessage', 'onLackKnowLedge', 'onTransfer', 'onFirstInteraction', 'onStartInteraction', 'onFinishInteraction', 'onCreateEvent', 'onCancelEvent')
            updated = {k: v for k, v in existing.items() if k in allowed}
            updated['onNewMessage'] = target
            result = self.sender.transport('PUT', path, updated)
            if not isinstance(result, dict) or result.get('success') is not True:
                raise ProviderError('webhook update not confirmed')
            check = self.sender.transport('GET', path)
            if not isinstance(check, dict) or check.get('onNewMessage') != target:
                raise ProviderError('webhook verification failed')
        return {'connected': True, 'mode': 'direct verified intake'}

    def verified_payload(self, event, now=None):
        now = now or datetime.now(timezone.utc)
        if not isinstance(event, dict) or event.get('assistantId') != self.monitor.agent or event.get('channel') != 'CLOUD_API' or event.get('role') not in ('user', 'assistant'):
            raise ProviderError('invalid event source')
        context = self.sender.segment(event.get('contextId'))
        phone = event.get('contactPhone')
        if not isinstance(phone, str):
            raise ProviderError('invalid recipient')
        chat, messages = self.canonical(context, phone, now)
        matches = [m for m in messages if m.get('id') == event.get('messageId') and m.get('role') == event['role']]
        if len(matches) != 1:
            raise ProviderError('event not found in canonical history')
        message = matches[0]
        if message.get('role') == 'assistant' and (message.get('hasError') or message.get('deletedMessage') or message.get('requestApproval')):
            self.monitor.mark_automatic(message['id'])
        verified = dict(event, message=message.get('text') or '', date=instant(message.get('time')).isoformat(), contactName=chat.get('name') or chat.get('title') or event.get('contactName', ''))
        for field, source in (('images', 'imageUrl'), ('audios', 'audioUrl'), ('documents', 'documentUrl'), ('videos', 'videoUrl')):
            verified[field] = [''] if message.get(source) else []
        return verified

    def control(self):
        with self.monitor.store.connect() as db:
            row = db.execute('SELECT enabled, activated_at FROM operation_control WHERE id=1').fetchone()
        return bool(row[0]), row[1]

    def preflight(self, now=None):
        now = now or datetime.now(timezone.utc)
        try:
            result = self.sender.meta.probe()
            if not result.get('ready'):
                raise ProviderError(result.get('status', 'Meta check failed'))
            reference = self.sender.test_chat()
            channel = reference.get('channelId')
            if not isinstance(channel, str) or not channel or reference.get('channelIsConnected') is False:
                raise ProviderError('commercial channel unavailable')
            history = self.sender.read_list('/chat/' + self.sender.segment(reference.get('id')) + '/messages')
            valid = [m for m in history if m.get('role') in ('user', 'assistant') and isinstance(m.get('id'), str)]
            if not valid:
                raise ProviderError('canonical message history unavailable')
            instant(valid[-1].get('time'))
            self.channel_id = channel
            self.ready, self.last_error, self.last_check = True, '', now
        except ProviderError as error:
            self.ready, self.last_error, self.last_check = False, str(error), now
        return self.status()

    def set_enabled(self, enabled, now=None):
        now = now or datetime.now(timezone.utc)
        if enabled and not self.preflight(now)['ready']:
            raise ProviderError('preflight failed')
        # No historical campaign: only events from this activation are eligible.
        with self.lock, self.monitor.store.connect() as db:
            if self.monitor.store.database_url:
                db.execute('SELECT pg_advisory_lock(110224)')
            try:
                db.execute(f'UPDATE operation_control SET enabled={self.p},activated_at={self.p} WHERE id=1', (int(enabled), now.isoformat(timespec='microseconds')))
            finally:
                if self.monitor.store.database_url:
                    db.execute('SELECT pg_advisory_unlock(110224)')
        return self.status()

    def status(self):
        enabled, activated = self.control()
        with self.monitor.store.connect() as db:
            counts = db.execute('SELECT kind, state, COUNT(*) FROM operation_attempts GROUP BY kind,state').fetchall()
        return {'enabled': enabled, 'ready': self.ready, 'activated_at': activated,
            'last_check': self.last_check.isoformat() if self.last_check else None,
            'last_error': self.last_error,
            'attempts': [{'kind': kind, 'state': state, 'count': count} for kind, state, count in counts]}

    def chats(self, now):
        if self.cache_at and now - self.cache_at < timedelta(seconds=30):
            return self.cache
        found = {}
        for workspace in self.sender.read_list('/workspaces')[:3]:
            wid = self.sender.segment(workspace.get('id'))
            for page in range(1, 21):
                query = urllib.parse.urlencode({'page': page, 'pageSize': 100})
                rows = self.sender.read_list('/workspace/' + wid + '/chats?' + query)
                new = 0
                for chat in rows:
                    if chat.get('channelId') == self.channel_id and isinstance(chat.get('id'), str):
                        if chat['id'] not in found:
                            new += 1
                        found[chat['id']] = chat
                if len(rows) < 100 or (page > 1 and not new):
                    break
        self.cache, self.cache_at = found, now
        return found

    def canonical(self, context, phone, now):
        if not self.channel_id:
            reference = self.sender.test_chat()
            self.channel_id = reference.get('channelId')
            if not isinstance(self.channel_id, str) or not self.channel_id:
                raise ProviderError('commercial channel unavailable')
        chat = self.chats(now).get(context)
        if not chat:
            self.cache_at = None
            chat = self.chats(now).get(context)
        if not chat or chat.get('channelId') != self.channel_id:
            raise ProviderError('conversation not yet verified')
        if chat.get('isGroup') or chat.get('blocked') or chat.get('channelIsConnected') is False:
            raise ProviderError('conversation blocked or unavailable')
        phones = [re.sub(r'\D', '', str(chat.get(k, ''))) for k in ('recipient', 'whatsappPhone')]
        if not re.fullmatch(r'\d{11,15}', phone) or phone not in phones:
            raise ProviderError('recipient mismatch')
        messages = self.sender.read_list('/chat/' + self.sender.segment(context) + '/messages')
        return chat, sorted(messages, key=lambda m: instant(m.get('time')))

    def automatic(self, message):
        text = (message.get('text') or '').strip()
        if text == FIORELLA_GREETING:
            return True
        with self.monitor.store.connect() as db:
            return bool(db.execute(f'SELECT 1 FROM monitor_automatic_texts WHERE text_key={self.p}', (self.monitor.digest(text),)).fetchone() or
                db.execute(f'SELECT 1 FROM monitor_automatic_messages WHERE event_key={self.p}', (self.monitor.digest(self.monitor.agent + ':' + str(message.get('id'))),)).fetchone())

    def human(self, message):
        return message.get('role') == 'assistant' and not message.get('hasError') and not message.get('deletedMessage') and not message.get('requestApproval') and not self.automatic(message)

    def reconcile(self, activated, now):
        # Recover a missed webhook from canonical provider history. This only reads
        # chats updated after activation; it never imports an old marketing list.
        if self.last_reconcile and now - self.last_reconcile < timedelta(seconds=60):
            return
        since = datetime.fromisoformat(activated)
        for context, chat in self.chats(now).items():
            if chat.get('isGroup') or chat.get('blocked'):
                continue
            try:
                if instant(chat.get('time')) < since:
                    continue
            except (ProviderError, ValueError, OverflowError):
                continue
            phone = re.sub(r'\D', '', str(chat.get('recipient') or chat.get('whatsappPhone') or ''))
            if not re.fullmatch(r'\d{11,15}', phone):
                continue
            messages = self.sender.read_list('/chat/' + self.sender.segment(context) + '/messages')
            for message in messages:
                if message.get('role') not in ('user', 'assistant') or not isinstance(message.get('id'), str):
                    continue
                date = instant(message.get('time'))
                if date < since or date > now + timedelta(seconds=5):
                    continue
                event = dict(assistantId=self.monitor.agent, channel='CLOUD_API', contextId=context, contactPhone=phone, contactName=chat.get('name') or chat.get('title') or '', messageId=message['id'], role=message['role'], message=message.get('text') or '', date=date.isoformat())
                for field, source in (('images', 'imageUrl'), ('audios', 'audioUrl'), ('documents', 'documentUrl'), ('videos', 'videoUrl')):
                    event[field] = [''] if message.get(source) else []
                if message.get('role') == 'assistant' and (message.get('hasError') or message.get('deletedMessage') or message.get('requestApproval')):
                    self.monitor.mark_automatic(message['id'])
                # Provider edits/profile renames must not turn immutable history
                # duplicates into a failure that blocks all other conversations.
                event_key = self.monitor.digest(self.monitor.agent + ':' + message['id'])
                with self.monitor.store.connect() as db:
                    exists = db.execute(f'SELECT 1 FROM monitor_messages WHERE event_key={self.p}', (event_key,)).fetchone()
                if not exists:
                    self.monitor.save(event, now)
        self.last_reconcile = now

    def session(self, key):
        with self.monitor.store.connect() as db:
            row = db.execute(f'SELECT payload FROM operation_sessions WHERE context_key={self.p}', (key,)).fetchone()
        return json.loads(self.monitor.cipher.decrypt(row[0].encode())) if row else None

    def put_session(self, key, session):
        encrypted = self.monitor.cipher.encrypt(json.dumps(session).encode()).decode()
        with self.monitor.store.connect() as db:
            db.execute(f'INSERT INTO operation_sessions VALUES ({self.p},{self.p}) ON CONFLICT (context_key) DO UPDATE SET payload=excluded.payload', (key, encrypted))

    def claim(self, job, kind, now):
        with self.monitor.store.connect() as db:
            return bool(db.execute(f'INSERT INTO operation_attempts VALUES ({self.p},{self.p},{self.p},{self.p}) ON CONFLICT DO NOTHING RETURNING job', (job, kind, 'sending', now.isoformat())).fetchone())

    def finish(self, job, state, now):
        with self.monitor.store.connect() as db:
            db.execute(f'UPDATE operation_attempts SET state={self.p},updated={self.p} WHERE job={self.p}', (state, now.isoformat(), job))

    def validate_event(self, event, now):
        context, phone = event['contextId'], event['contactPhone']
        chat, messages = self.canonical(context, phone, now)
        matches = [m for m in messages if m.get('id') == event['messageId'] and m.get('role') == event['role'] and (m.get('text') or '').strip() == event['message'].strip()]
        if len(matches) != 1:
            raise ProviderError('incoming event not verified')
        current = matches[0]
        date = instant(current['time'])
        if abs((date - datetime.fromisoformat(event['date'])).total_seconds()) > 5:
            raise ProviderError('incoming timestamp mismatch')
        return chat, messages, current, date

    def handle_event(self, event, now):
        context, phone = event['contextId'], event['contactPhone']
        chat, messages, current, date = self.validate_event(event, now)
        key = self.monitor.digest(self.monitor.agent + ':' + context)
        session = self.session(key)
        # Explicit opt-outs suppress greeting and reminder, including on a blocked chat.
        stop = current.get('role') == 'user' and (current.get('text') or '').strip().casefold() in ('pare', 'parar', 'sair', 'não me envie mensagens', 'nao me envie mensagens')
        if stop:
            self.put_session(key, dict(session or {}, context=context, phone=phone, stopped=True))
            return
        if session and session.get('stopped'):
            return
        if current.get('role') != 'user':
            return
        if not 0 <= (now - date).total_seconds() <= 300:
            return
        # No greeting over an already ongoing exchange or after a human has replied
        # to this batch while our worker was validating the event.
        if any(self.human(m) and instant(m['time']) >= date for m in messages):
            return
        previous = [m for m in messages if instant(m['time']) < date]
        last_activity = max((instant(m['time']) for m in previous), default=None)
        previous_humans = [m for m in previous if self.human(m)]
        if session:
            since = datetime.fromisoformat(session.get('wait_since', session['greet_at']))
            replied = any(self.human(m) and instant(m['time']) >= since for m in previous)
            if not replied or (last_activity and date - last_activity < timedelta(hours=24) and not chat.get('finished')):
                return
        elif previous_humans and last_activity and date - last_activity < timedelta(hours=24) and not chat.get('finished'):
            return
        job = self.monitor.digest('production-greeting:' + context + ':' + event['messageId'])
        if not self.claim(job, 'greeting', now):
            return
        # Save intent before the API call. Uncertain sends are never repeated.
        session = dict(context=context, phone=phone, greet_at=now.isoformat(), wait_since=event['date'], greeting_key=job, state='uncertain', stopped=False)
        self.put_session(key, session)
        state = 'uncertain'
        try:
            result = self.sender.transport('POST', '/chat/' + self.sender.segment(context) + '/send-message', {'message': FIORELLA_GREETING})
            if isinstance(result, dict) and result.get('success') is True:
                state = 'accepted'
                self.monitor.record_greeting(job, context, now.isoformat())
        finally:
            session['state'] = state
            self.put_session(key, session)
            self.finish(job, state, now)

    def reminders(self, now):
        with self.monitor.store.connect() as db:
            rows = db.execute('SELECT context_key,payload FROM operation_sessions').fetchall()
        for key, encrypted in rows:
            session = json.loads(self.monitor.cipher.decrypt(encrypted.encode()))
            if session.get('state') != 'accepted' or session.get('stopped') or session.get('reminder_done'):
                continue
            since = datetime.fromisoformat(session['greet_at'])
            if now < since + timedelta(hours=24):
                continue
            _, messages = self.canonical(session['context'], session['phone'], now)
            wait_since = datetime.fromisoformat(session.get('wait_since', session['greet_at']))
            if any(self.human(m) and instant(m['time']) >= wait_since for m in messages):
                session['reminder_done'] = 'human replied'
                self.put_session(key, session)
                continue
            if any(m.get('role') == 'user' and (m.get('text') or '').strip().casefold() in ('pare', 'parar', 'sair', 'não me envie mensagens', 'nao me envie mensagens') and instant(m['time']) >= since for m in messages):
                session['stopped'] = True
                self.put_session(key, session)
                continue
            job = self.monitor.digest('production-reminder:' + session['greeting_key'])
            if not self.claim(job, 'reminder', now):
                session['reminder_done'] = 'already attempted'
                self.put_session(key, session)
                continue
            state = 'uncertain'
            try:
                state = self.sender.meta.send_template(session['phone'], job, now)
            finally:
                session['reminder_done'] = state
                self.put_session(key, session)
                self.finish(job, state, now)

    def tick(self, now=None):
        now = now or datetime.now(timezone.utc)
        enabled, activated = self.control()
        if not enabled:
            return
        if not self.last_check or now - self.last_check >= timedelta(minutes=5):
            self.preflight(now)
        if not self.ready:
            return
        # Single dispatcher across simultaneous workers/deploys. Claim-before-send
        # is still the final duplicate barrier when a process dies during a call.
        with self.lock, self.monitor.store.connect() as coordinator:
            if self.monitor.store.database_url and not coordinator.execute('SELECT pg_try_advisory_lock(110224)').fetchone()[0]:
                return
            try:
                self.reconcile(activated, now)
                with self.monitor.store.connect() as db:
                    events = db.execute(f'SELECT m.event_key,m.payload FROM monitor_messages m LEFT JOIN operation_events o ON o.event_key=m.event_key WHERE o.event_key IS NULL AND m.occurred_at>={self.p} ORDER BY m.occurred_at, m.event_key LIMIT 20', (activated,)).fetchall()
                for event_key, encrypted in events:
                    event = json.loads(self.monitor.cipher.decrypt(encrypted.encode()))
                    try:
                        self.handle_event(event, now)
                    except ProviderError as error:
                        self.last_error = str(error)
                        if now - datetime.fromisoformat(event['date']) < timedelta(minutes=5):
                            continue
                    with self.monitor.store.connect() as db:
                        db.execute(f'INSERT INTO operation_events VALUES ({self.p},{self.p}) ON CONFLICT DO NOTHING', (event_key, 'checked'))
                self.reminders(now)
            finally:
                if self.monitor.store.database_url:
                    coordinator.execute('SELECT pg_advisory_unlock(110224)')

    def run(self):
        while True:
            try:
                self.tick()
            except ProviderError as error:
                self.last_error = str(error)
            except Exception:
                self.last_error = 'operation unavailable; check private panel'
            time.sleep(5)

    def start(self):
        threading.Thread(target=self.run, name='fiorella-operation', daemon=True).start()
