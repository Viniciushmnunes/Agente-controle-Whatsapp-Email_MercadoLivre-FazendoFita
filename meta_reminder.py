"""Approved Meta template, owner-only validation before customer rollout."""
import json
import urllib.error
import urllib.parse
import urllib.request

from fiorella import NoRedirect, ProviderError, TEST_PHONE

TEMPLATE_NAME = 'fiorella_atendimento_pendente_24h'
TEST_TRIGGER = 'Teste lembrete Fiorella 3'
EXPECTED_WABA = '1607356107416841'
EXPECTED_PHONE_ID = '1317182464806320'


class MetaReminder:
    def __init__(self, monitor, settings, transport=None):
        self.monitor = monitor
        self.settings = settings
        self.transport = transport or self.request
        self.p = monitor.placeholder
        with monitor.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta_reminder_attempts (job TEXT PRIMARY KEY, state TEXT NOT NULL, updated TEXT NOT NULL, message_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS meta_reminder_responses (job TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def configured(self):
        return (bool(self.settings.get('WHATSAPP_ACCESS_TOKEN')) and
            self.settings.get('WHATSAPP_BUSINESS_ACCOUNT_ID') == EXPECTED_WABA and
            self.settings.get('WHATSAPP_PHONE_NUMBER_ID') == EXPECTED_PHONE_ID)

    def request(self, method, path, body=None):
        req = urllib.request.Request('https://graph.facebook.com/v22.0/' + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + self.settings['WHATSAPP_ACCESS_TOKEN'], 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=8) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ProviderError('Meta response too large')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            # Numeric error codes are safe; never expose response bodies or tokens.
            code = None
            try:
                data = json.loads(error.read(16384))
                code = data.get('error', {}).get('code')
            except (ValueError, AttributeError):
                pass
            suffix = ' code ' + str(code) if isinstance(code, int) else ''
            raise ProviderError('Meta HTTP ' + str(error.code) + suffix) from None
        except (OSError, ValueError):
            raise ProviderError('Meta unavailable') from None

    def approved_template(self):
        if not self.configured():
            raise ProviderError('Meta credentials missing or account mismatch')
        query = urllib.parse.urlencode({'name': TEMPLATE_NAME, 'fields': 'id,name,status,language,category,components', 'limit': 100})
        data = self.transport('GET', EXPECTED_WABA + '/message_templates?' + query)
        rows = data.get('data') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProviderError('unexpected Meta template schema')
        matches = [r for r in rows if isinstance(r, dict) and r.get('name') == TEMPLATE_NAME and r.get('language') == 'pt_BR']
        if len(matches) != 1 or matches[0].get('status') != 'APPROVED' or matches[0].get('category') != 'UTILITY':
            raise ProviderError('approved utility template not available')
        template = matches[0]
        components = template.get('components')
        if not isinstance(components, list) or len(components) != 1 or components[0].get('type') != 'BODY':
            raise ProviderError('template has unexpected components')
        text = components[0].get('text')
        if not isinstance(text, str) or not text or '{{' in text:
            raise ProviderError('template requires parameters or body is missing')
        # A copied template is never evidence of a human response.
        self.monitor.register_automatic_text(text)
        return template

    def probe(self):
        try:
            template = self.approved_template()
            phones = self.transport('GET', EXPECTED_WABA + '/phone_numbers?fields=id,display_phone_number')
            entries = phones.get('data', []) if isinstance(phones, dict) else []
            if not any(row.get('id') == EXPECTED_PHONE_ID for row in entries):
                raise ProviderError('configured phone not found in this account')
            return {'ready': True, 'template': template['name'], 'language': template['language'], 'status': template['status'], 'category': template['category'], 'mode': 'production' if getattr(self.monitor, 'operation', None) and self.monitor.operation.control()[0] else 'owner-only test; customer rollout disabled'}
        except ProviderError as error:
            return {'ready': False, 'status': str(error)}

    def send_test(self, event, now):
        # This is called only after Fiorella verifies the fresh incoming trigger in
        # canonical GPT Maker history. Keep recipient and text non-configurable.
        if event.get('contactPhone') != TEST_PHONE or event.get('message', '').strip() != TEST_TRIGGER:
            return 'not eligible'
        job = self.monitor.digest('meta-reminder-owner-test-v3:' + event['contextId'])
        return self.send_template(TEST_PHONE, job, now)

    def send_template(self, recipient, job, now):
        # Only called by a verified event or the canonical production dispatcher.
        import re
        if not isinstance(recipient, str) or not re.fullmatch(r'\d{11,15}', recipient):
            raise ProviderError('invalid verified recipient')
        self.approved_template()
        with self.monitor.store.connect() as db:
            claimed = db.execute(f'INSERT INTO meta_reminder_attempts VALUES ({self.p},{self.p},{self.p},{self.p}) ON CONFLICT DO NOTHING RETURNING job', (job, 'sending', now.isoformat(), '')).fetchone()
        if not claimed:
            return 'already attempted'
        state, message_id = 'uncertain', ''
        try:
            result = self.transport('POST', EXPECTED_PHONE_ID + '/messages', {
                'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': recipient, 'type': 'template',
                'template': {'name': TEMPLATE_NAME, 'language': {'code': 'pt_BR'}}})
            # Persist only diagnostic fields, encrypted, before interpreting success.
            receipt = {key: result[key] for key in ('contacts', 'messages') if isinstance(result, dict) and key in result}
            receipt['requested_recipient'] = recipient
            encrypted_receipt = self.monitor.cipher.encrypt(json.dumps(receipt).encode()).decode()
            with self.monitor.store.connect() as db:
                db.execute(f'INSERT INTO meta_reminder_responses VALUES ({self.p},{self.p}) ON CONFLICT DO NOTHING', (job, encrypted_receipt))
            messages = result.get('messages', []) if isinstance(result, dict) else []
            if len(messages) == 1 and isinstance(messages[0].get('id'), str) and messages[0]['id'].startswith('wamid.'):
                state, message_id = 'accepted', messages[0]['id']
                self.monitor.mark_automatic(message_id)
        finally:
            # Provider IDs are encrypted; never log conversation data or tokens.
            encrypted = self.monitor.cipher.encrypt(message_id.encode()).decode() if message_id else ''
            with self.monitor.store.connect() as db:
                db.execute(f'UPDATE meta_reminder_attempts SET state={self.p},updated={self.p},message_id={self.p} WHERE job={self.p}', (state, now.isoformat(), encrypted, job))
        return state

    def status(self):
        with self.monitor.store.connect() as db:
            states = [r[0] for r in db.execute('SELECT state FROM meta_reminder_attempts').fetchall()]
        return {'configured': self.configured(), 'mode': 'production' if getattr(self.monitor, 'operation', None) and self.monitor.operation.control()[0] else 'owner-only test', 'test_trigger': TEST_TRIGGER, 'attempts': states}

    def delivery_probe(self):
        """Read-only correlation; no claim of delivery without a delivery webhook."""
        sender = self.monitor.fiorella
        with self.monitor.store.connect() as db:
            attempts = db.execute('SELECT state, updated, message_id FROM meta_reminder_attempts').fetchall()
            receipts = db.execute('SELECT payload FROM meta_reminder_responses').fetchall()
        ids = {self.monitor.cipher.decrypt(row[2].encode()).decode() for row in attempts if row[2]}
        result = {'attempts': [{'state': row[0], 'at': row[1]} for row in attempts], 'delivery_confirmed': False}
        details = []
        for encrypted, in receipts:
            receipt = json.loads(self.monitor.cipher.decrypt(encrypted.encode()))
            contacts = receipt.get('contacts', [])
            messages = receipt.get('messages', [])
            details.append({'recipient_matches_sent': any(row.get('wa_id') == receipt.get('requested_recipient', TEST_PHONE) for row in contacts),
                'recipient_lengths': [len(row['wa_id']) for row in contacts if isinstance(row.get('wa_id'), str)],
                'initial_statuses': [row.get('message_status') if row.get('message_status') in ('accepted', 'held_for_quality_assessment', 'paused', 'sent', 'failed') else 'not returned' for row in messages]})
        result['send_response_details'] = details
        try:
            chat = sender.test_chat()
            result['canonical_recipient_matches'] = {field: str(chat.get(field, '')).lstrip('+') == TEST_PHONE for field in ('recipient', 'whatsappPhone')}
            result['canonical_recipient_lengths'] = {field: len(str(chat.get(field, '')).lstrip('+')) for field in ('recipient', 'whatsappPhone')}
            messages = sender.read_list('/chat/' + sender.segment(chat['id']) + '/messages')
            matches = [m for m in messages if m.get('id') in ids or m.get('externalId') in ids]
            result['provider_history_matches'] = len(matches)
            result['provider_history_has_error'] = any(m.get('hasError') is True for m in matches)
            # Only known status enum values and numeric codes may leave this probe.
            states, codes = set(), set()
            def inspect(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in ('code', 'error_code') and isinstance(item, int):
                            codes.add(item)
                        if key in ('status', 'message_status') and isinstance(item, str) and item in ('sent', 'delivered', 'read', 'failed', 'held_for_quality_assessment', 'accepted'):
                            states.add(item)
                        inspect(item)
                elif isinstance(value, list):
                    for item in value:
                        inspect(item)
            for message in matches:
                inspect(message.get('metadata'))
            result['provider_statuses'] = sorted(states)
            result['provider_error_codes'] = sorted(codes)
            result['delivery_confirmed'] = bool(states & {'delivered', 'read'})
        except ProviderError as error:
            result['history_check'] = str(error)
        return result
