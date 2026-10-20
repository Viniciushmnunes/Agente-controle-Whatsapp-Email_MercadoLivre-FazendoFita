"""Private GPT Maker intake and conversation review; never sends messages."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from html import escape
import json
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet


class Monitor:
    def __init__(self, store, settings):
        self.store = store
        self.password = settings['CRM_ADMIN_PASSWORD']
        self.cipher = Fernet(settings['BLING_ENCRYPTION_KEY'].encode())
        self.identity_key = hashlib.sha256(b'monitor-identity:' + settings['BLING_ENCRYPTION_KEY'].encode()).digest()
        self.agent = settings.get('GPTMAKER_MONITOR_AGENT_ID', '3FAECF6FF631002178746EE6346DCA12')
        self.placeholder = '%s' if store.database_url else '?'
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS monitor_messages (event_key TEXT PRIMARY KEY, context_key TEXT NOT NULL, occurred_at TEXT NOT NULL, role TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE INDEX IF NOT EXISTS monitor_context_date ON monitor_messages (context_key, occurred_at)')

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

    def page(self):
        rows = self.latest()
        pending = [r for r in rows if r['role'] == 'user']
        parts = ['<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Conversas — Fazendo Fita</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:16px;color:#242038}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}td{overflow-wrap:anywhere;white-space:pre-wrap}a{color:#550982}</style><h1>Conversas do WhatsApp</h1><p><a href="/crm">Pedidos e entregas</a> · <a href="/monitor">Atualizar conversas</a></p>']
        parts.append(f'<p>{len(pending)} conversa(s) com última mensagem do cliente, em {len(rows)} conversa(s) registradas.</p>')
        parts.append('<p>Revisão por ordem das mensagens recebidas. Isso não confirma uma pergunta pendente. O papel de resposta corresponde às mensagens enviadas no teste com o agente inativo; não identifica qual pessoa respondeu.</p><p>Somente mensagens encaminhadas desde a conexão do n8n aparecem aqui. Sem importação do histórico anterior e sem alerta automático nesta etapa.</p>')
        if not rows:
            parts.append('<p>Aguardando o primeiro encaminhamento do n8n.</p>')
        else:
            parts.append('<table><tr><th>Contato</th><th>Última mensagem (São Paulo)</th><th>Situação</th><th>Texto</th></tr>')
            ordered = pending + [r for r in rows if r['role'] != 'user']
            for row in ordered[:200]:
                date = datetime.fromisoformat(row['date']).astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M:%S')
                status = 'Última mensagem do cliente — revisar' if row['role'] == 'user' else 'Última mensagem de resposta'
                text = row['message'] or f"[Sem texto; {row['mediaCount']} anexo(s)]"
                parts.append('<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in (row['contactName'] + '\n' + row['contactPhone'], date, status, text[:2000])) + '</tr>')
            parts.append('</table><p>Exibindo até 200 conversas, priorizando as que precisam de revisão. Anexos não são baixados.</p>')
        parts.append('<p>Nenhuma resposta é enviada aos clientes por este painel.</p></html>')
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
        return respond('200 OK', 'EVENT_STORED')
