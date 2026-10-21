"""Private GPT Maker intake and conversation review; never sends messages."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from html import escape
import json
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet


ZONE = ZoneInfo('America/Sao_Paulo')
FIORELLA_GREETING = 'Olá! Sou a Fiorella, assistente virtual da Fazendo Fita. Recebemos sua mensagem! Uma pessoa da nossa equipe dará continuidade ao seu atendimento assim que possível. 🌷'


def business_deadline(start, minutes=60):
    """Add business minutes: Monday-Friday, 08:00-18:00 in Sao Paulo."""
    current = start.astimezone(ZONE)
    remaining = timedelta(minutes=minutes)
    while True:
        opening = current.replace(hour=8, minute=0, second=0, microsecond=0)
        closing = current.replace(hour=18, minute=0, second=0, microsecond=0)
        if current.weekday() >= 5 or current >= closing:
            current = (current + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            continue
        current = max(current, opening)
        available = closing - current
        if remaining <= available:
            return (current + remaining).astimezone(timezone.utc)
        remaining -= available
        current = (current + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)


class Monitor:
    def __init__(self, store, settings):
        self.fiorella = None
        self.fiorella_status = 'not attempted'
        self.store = store
        self.password = settings['CRM_ADMIN_PASSWORD']
        self.cipher = Fernet(settings['BLING_ENCRYPTION_KEY'].encode())
        self.identity_key = hashlib.sha256(b'monitor-identity:' + settings['BLING_ENCRYPTION_KEY'].encode()).digest()
        self.agent = settings.get('GPTMAKER_MONITOR_AGENT_ID', '3FAECF6FF631002178746EE6346DCA12')
        self.placeholder = '%s' if store.database_url else '?'
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS monitor_messages (event_key TEXT PRIMARY KEY, context_key TEXT NOT NULL, occurred_at TEXT NOT NULL, role TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE INDEX IF NOT EXISTS monitor_context_date ON monitor_messages (context_key, occurred_at)')
            db.execute('CREATE TABLE IF NOT EXISTS monitor_automatic_messages (event_key TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS monitor_automatic_texts (text_key TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS monitor_greetings (greeting_key TEXT PRIMARY KEY, context_key TEXT NOT NULL, sent_at TEXT NOT NULL)')

    def digest(self, value):
        return hmac.new(self.identity_key, value.encode(), hashlib.sha256).hexdigest()

    def save(self, payload, now=None):
        now = now or datetime.now(timezone.utc)
        if not isinstance(payload, dict) or payload.get('assistantId') != self.agent or payload.get('channel') != 'CLOUD_API':
            raise ValueError('invalid source')
        for key, limit in [('messageId', 256), ('contextId', 512), ('contactPhone', 32), ('contactName', 256), ('message', 64000)]:
            value = payload.get(key, '')
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError('invalid field')
        if not payload.get('messageId') or not payload.get('contextId') or payload.get('role') not in ('user', 'assistant'):
            raise ValueError('invalid identity or role')
        date = payload.get('date')
        if not isinstance(date, str) or len(date) > 64:
            raise ValueError('missing date')
        occurred = datetime.fromisoformat(date.replace('Z', '+00:00'))
        if occurred.tzinfo is None or occurred > now + timedelta(minutes=5):
            raise ValueError('invalid date')
        # Store only the fields needed for review. Media links are not fetched or retained.
        event = {k: payload.get(k, '') for k in ('assistantId', 'messageId', 'contextId', 'contactPhone', 'contactName', 'message', 'role')}
        event['date'] = occurred.astimezone(timezone.utc).isoformat(timespec='microseconds')
        event['mediaCount'] = sum(len(payload.get(k, [])) for k in ('images', 'documents', 'videos', 'audios') if isinstance(payload.get(k, []), list))
        plain = json.dumps(event, ensure_ascii=False, sort_keys=True)
        event_key = self.digest(self.agent + ':' + event['messageId'])
        fingerprint = self.digest(plain)
        p = self.placeholder
        with self.store.connect() as db:
            db.execute(f'INSERT INTO monitor_messages (event_key, context_key, occurred_at, role, fingerprint, payload) VALUES ({p},{p},{p},{p},{p},{p}) ON CONFLICT (event_key) DO NOTHING', (event_key, self.digest(self.agent + ':' + event['contextId']), event['date'], event['role'], fingerprint, self.cipher.encrypt(plain.encode()).decode()))
            stored = db.execute(f'SELECT fingerprint FROM monitor_messages WHERE event_key={p}', (event_key,)).fetchone()[0]
            if not hmac.compare_digest(stored, fingerprint):
                raise ValueError('conflicting duplicate')

    def latest(self):
        # A late webhook cannot replace a newer reply. Equal timestamps prefer review.
        with self.store.connect() as db:
            rows = db.execute("SELECT payload FROM (SELECT payload, occurred_at, ROW_NUMBER() OVER (PARTITION BY context_key ORDER BY occurred_at DESC, CASE WHEN role='user' THEN 1 ELSE 0 END DESC, event_key DESC) AS position FROM monitor_messages) ranked WHERE position=1 ORDER BY occurred_at DESC").fetchall()
        return [json.loads(self.cipher.decrypt(row[0].encode())) for row in rows]

    def mark_automatic(self, message_id):
        # Only a trusted sender with the returned provider message ID may register this.
        # It is not inferred from message text or a client-controlled webhook flag.
        with self.store.connect() as db:
            db.execute(f'INSERT INTO monitor_automatic_messages VALUES ({self.placeholder}) ON CONFLICT DO NOTHING', (self.digest(self.agent + ':' + message_id),))

    def register_automatic_text(self, text):
        with self.store.connect() as db:
            db.execute(f'INSERT INTO monitor_automatic_texts VALUES ({self.placeholder}) ON CONFLICT DO NOTHING', (self.digest(text.strip()),))

    def record_greeting(self, greeting_key, context_id, sent_at):
        sent_at = datetime.fromisoformat(sent_at).astimezone(timezone.utc).isoformat(timespec='microseconds')
        p = self.placeholder
        with self.store.connect() as db:
            db.execute(f'INSERT INTO monitor_greetings VALUES ({p},{p},{p}) ON CONFLICT DO NOTHING', (greeting_key, self.digest(self.agent + ':' + context_id), sent_at))

    def conversations(self, now=None):
        now = now or datetime.now(timezone.utc)
        with self.store.connect() as db:
            events = db.execute("SELECT m.context_key, m.event_key, m.payload, a.event_key FROM monitor_messages m LEFT JOIN monitor_automatic_messages a ON a.event_key=m.event_key ORDER BY m.occurred_at, CASE WHEN m.role='assistant' THEN 0 ELSE 1 END, m.event_key").fetchall()
            greetings = db.execute('SELECT greeting_key, context_key, sent_at FROM monitor_greetings ORDER BY sent_at').fetchall()
            automatic_texts = {r[0] for r in db.execute('SELECT text_key FROM monitor_automatic_texts').fetchall()}
        contexts = {}
        for context, key, encrypted, automatic in events:
            event = json.loads(self.cipher.decrypt(encrypted.encode()))
            row = contexts.setdefault(context, {'key': context, 'pending_since': None, 'pending_key': None, 'last_reply_key': 'start', 'episode_key': None})
            row['latest'] = event
            # Temporary fail-safe: a greeting copied manually is also kept for review.
            # Exact text is not accepted as evidence of a human reply.
            greeting = event['message'].strip() == FIORELLA_GREETING or self.digest(event['message'].strip()) in automatic_texts
            if event['role'] == 'user':
                if row['pending_since'] is None:
                    row['pending_since'] = event['date']
                    row['pending_key'] = key
                    row['episode_key'] = self.digest(context + ':after:' + row['last_reply_key'])
            elif not automatic and not greeting:
                row['pending_since'] = None
                row['pending_key'] = None
                row['episode_key'] = None
                row['last_reply_key'] = key
        for row in contexts.values():
            row['deadline'] = business_deadline(datetime.fromisoformat(row['pending_since'])) if row['pending_since'] else None
            row['overdue'] = row['deadline'] is not None and now >= row['deadline']
            row['critical_at'] = None
            row['greeting_key'] = None
            if row['pending_since']:
                # Only an accepted greeting in the still-unanswered episode counts.
                candidates = [(key, sent) for key, context, sent in greetings if context == row['key'] and sent >= row['pending_since']]
                if candidates:
                    key, sent = candidates[0]
                    row['critical_at'] = datetime.fromisoformat(sent) + timedelta(hours=24)
                    row['greeting_key'] = key
            row['critical'] = row['critical_at'] is not None and now >= row['critical_at']
        return sorted(contexts.values(), key=lambda r: (not r['critical'], not r['overdue'], r['pending_since'] is None, r['pending_since'] or r['latest']['date']))

    def overdue(self, now):
        return [row for row in self.conversations(now) if row['overdue']]

    def page(self):
        now = datetime.now(timezone.utc)
        rows = self.conversations(now)
        pending = [r for r in rows if r['pending_since']]
        overdue = [r for r in pending if r['overdue']]
        critical = [r for r in pending if r['critical']]
        parts = ['<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Conversas — Fazendo Fita</title><style>body{font:16px system-ui;max-width:1200px;margin:32px auto;padding:16px;color:#242038}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}td{overflow-wrap:anywhere;white-space:pre-wrap}a{color:#550982}</style><h1>Conversas do WhatsApp</h1><p><a href="/crm">Pedidos e entregas</a> · <a href="/monitor">Atualizar conversas</a></p>']
        parts.append(f'<p>{len(pending)} conversa(s) aguardando resposta, sendo {len(overdue)} com prazo vencido, em {len(rows)} conversa(s) registradas.</p>')
        parts.append(f'<p style="color:#b00020;font-weight:bold">🔴 {len(critical)} alarme(s) crítico(s): 24 horas após a saudação da Fiorella sem resposta humana.</p>')
        parts.append('<p>Prazo de resposta humana: 1 hora útil, de segunda a sexta, das 8h às 18h (São Paulo). Novas mensagens do cliente não reiniciam o prazo. A saudação cadastrada da Fiorella não encerra a espera.</p><p>Indicador para revisão: uma mensagem de agradecimento também pode aparecer como espera. Respostas são identificadas pelo fluxo validado com o agente inativo, sem identificar qual pessoa respondeu. Somente eventos encaminhados desde a conexão do n8n; sem histórico anterior. Atualize a página para conferir o prazo.</p>')
        if not rows:
            parts.append('<p>Aguardando o primeiro encaminhamento do n8n.</p>')
        else:
            parts.append('<table><tr><th>Contato</th><th>Última mensagem (São Paulo)</th><th>Situação</th><th>Espera e limite (São Paulo)</th><th>Texto</th></tr>')
            for item in rows[:200]:
                row = item['latest']
                fmt = lambda date: date.astimezone(ZONE).strftime('%d/%m/%Y %H:%M:%S')
                status = '🔴 CRÍTICO — 24h após a Fiorella sem resposta humana' if item['critical'] else 'Prazo vencido — revisar atendimento' if item['overdue'] else 'Aguardando resposta — dentro do prazo' if item['pending_since'] else 'Última mensagem de resposta'
                waiting = ('Desde: ' + fmt(datetime.fromisoformat(item['pending_since'])) + '\nLimite: ' + fmt(item['deadline'])) if item['pending_since'] else '—'
                if item['critical_at']:
                    waiting += '\nAlarme de 24h: ' + fmt(item['critical_at'])
                if item['critical']:
                    status += '\nMensagem de desculpas ainda não configurada.'
                text = row['message'] or f"[Sem texto; {row['mediaCount']} anexo(s)]"
                parts.append(('<tr style="background:#fff0f1;color:#9d001b">' if item['critical'] else '<tr>') + ''.join('<td>' + escape(v) + '</td>' for v in (row['contactName'] + '\n' + row['contactPhone'], fmt(datetime.fromisoformat(row['date'])), status, waiting, text[:2000])) + '</tr>')
            parts.append('</table><p>Exibindo até 200 conversas, priorizando os prazos vencidos. Anexos não são baixados.</p>')
        parts.append('<p>Fiorella: teste limitado ao celular do responsável, com a mensagem exata Teste Fiorella. O envio geral aos clientes está desativado. O agente de IA permanece inativo.</p></html>')
        return ''.join(parts)

    def handle(self, environ, start_response):
        def respond(status, text, html=False):
            body = text.encode()
            headers = [('Content-Type', 'text/html; charset=utf-8' if html else 'text/plain; charset=utf-8'), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'), ('X-Content-Type-Options', 'nosniff'), ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")]
            if status.startswith('401'):
                headers.append(('WWW-Authenticate', 'Basic realm="Fazendo Fita"'))
            start_response(status, headers)
            return [body]
        expected = 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()
        if not hmac.compare_digest(environ.get('HTTP_AUTHORIZATION', '').encode(), expected.encode()):
            return respond('401 Unauthorized', 'Acesso privado')
        path, method = environ.get('PATH_INFO'), environ.get('REQUEST_METHOD')
        if path in ('/monitor/meta', '/monitor/meta/probe', '/monitor/meta/delivery') and method == 'GET':
            meta = getattr(self.fiorella, 'meta', None)
            result = meta.delivery_probe() if meta and path.endswith('/delivery') else meta.probe() if meta and path.endswith('/probe') else meta.status() if meta else {'configured': False}
            return respond('200 OK', json.dumps(result))
        if path in ('/monitor/fiorella', '/monitor/fiorella/probe') and method == 'GET':
            if not self.fiorella:
                return respond('200 OK', json.dumps({'key_configured': False}))
            result = self.fiorella.probe() if path.endswith('/probe') else dict(self.fiorella.status(), last_result=self.fiorella_status)
            return respond('200 OK', json.dumps(result))
        if (path, method) == ('/monitor', 'GET'):
            try:
                return respond('200 OK', self.page(), True)
            except Exception:
                return respond('503 Service Unavailable', 'storage unavailable')
        if (path, method) != ('/monitor/events', 'POST'):
            return respond('405 Method Not Allowed', 'method not allowed')
        try:
            length = int(environ.get('CONTENT_LENGTH') or '0')
            if not 0 < length <= 1024 * 1024:
                return respond('413 Payload Too Large', 'invalid size')
            raw = environ['wsgi.input'].read(length)
            if len(raw) != length:
                raise ValueError('incomplete body')
            payload = json.loads(raw)
            self.save(payload)
        except (ValueError, TypeError, OverflowError, UnicodeDecodeError):
            return respond('400 Bad Request', 'invalid event')
        except Exception:
            return respond('503 Service Unavailable', 'storage unavailable')
        if self.fiorella:
            try:
                self.fiorella_status = self.fiorella.maybe_send(payload)
            except Exception:
                self.fiorella_status = 'pilot not completed; check read-only probe'
        return respond('200 OK', 'EVENT_STORED')
