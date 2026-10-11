"""Mercado Livre OAuth and read-only reconciliation of CRM shipments."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from bling import BlingError

API = 'https://api.mercadolibre.com'
REDIRECT = 'https://fazendo-fita-webhook.onrender.com/meli/callback'


class Meli:
    def __init__(self, crm, client_id, client_secret, transport=None):
        self.crm, self.client_id, self.secret = crm, client_id, client_secret
        self.store, self.p, self.cipher = crm.store, crm.p, crm.cipher
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.transport = transport or self.http
        self.last_request = 0
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS meli_shipments (order_key TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    @staticmethod
    def http(method, url, headers, body=None):
        try:
            with urlopen(Request(url, data=body, headers=headers, method=method), timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            raise BlingError('Mercado Livre HTTP ' + str(error.code)) from None
        except Exception:
            raise BlingError('Falha de conexão com Mercado Livre') from None

    def token_request(self, grant):
        body = dict(grant, client_id=self.client_id, client_secret=self.secret)
        result = self.transport('POST', API + '/oauth/token', {'Content-Type': 'application/x-www-form-urlencoded'}, urlencode(body).encode())
        if not result.get('access_token') or not result.get('refresh_token') or not result.get('user_id'):
            raise BlingError('Autorização Mercado Livre incompleta')
        result['expires_at'] = time.time() + int(result['expires_in'])
        return result

    def token(self):
        with self.lock, self.store.connect() as db:
            if self.store.database_url:
                db.execute('SELECT pg_advisory_xact_lock(110219)')
            raw = self.crm.get(db, 'meli_tokens')
            if not raw:
                raise BlingError('Mercado Livre ainda não conectado')
            result = json.loads(self.cipher.decrypt(raw.encode()))
            if result['expires_at'] < time.time() + 60:
                refreshed = self.token_request({'grant_type': 'refresh_token', 'refresh_token': result['refresh_token']})
                if str(refreshed['user_id']) != str(result['user_id']):
                    raise BlingError('Conta diferente na renovação; reconecte')
                result = refreshed
                self.crm.put(db, 'meli_tokens', self.cipher.encrypt(json.dumps(result).encode()).decode())
            return result

    def read(self, path):
        if not re.fullmatch(r'/(orders|packs|shipments)/[0-9]+(?:/sla)?', path):
            raise BlingError('Recurso não permitido')
        with self.lock:
            token = self.token()
            time.sleep(max(0, .4 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            return self.transport('GET', API + path, {'Authorization': 'Bearer ' + token['access_token'], 'x-format-new': 'true'})

    def authorize(self, code, state, cookie):
        if not code or not state or not cookie or not hmac.compare_digest(state, cookie):
            raise BlingError('Autorização inválida; inicie novamente')
        digest = hashlib.sha256(('meli:' + state).encode()).hexdigest()
        with self.store.connect() as db:
            row = db.execute(f'DELETE FROM bling_states WHERE state={self.p} AND expires>{self.p} RETURNING state', (digest, time.time())).fetchone()
        if not row:
            raise BlingError('Autorização inválida ou expirada')
        result = self.token_request({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT})
        with self.lock, self.store.connect() as db:
            if self.store.database_url:
                db.execute('SELECT pg_advisory_xact_lock(110219)')
            old = self.crm.get(db, 'meli_tokens')
            if old and str(json.loads(self.cipher.decrypt(old.encode()))['user_id']) != str(result['user_id']):
                raise BlingError('Use a mesma conta da loja já conectada')
            self.crm.put(db, 'meli_tokens', self.cipher.encrypt(json.dumps(result).encode()).decode())
            self.crm.put(db, 'meli_dirty', '1')

    def resolve(self, number, seller):
        number = str(int(number))
        shipment_id = None
        try:
            pack = self.read('/packs/' + number)
        except BlingError as error:
            if str(error) != 'Mercado Livre HTTP 404':
                raise
            orders = [self.read('/orders/' + number)]
        else:
            orders = [self.read('/orders/' + str(int(o['id']))) for o in pack.get('orders', [])]
            shipment_id = (pack.get('shipment') or {}).get('id')
        if not orders or any(str(o.get('seller', {}).get('id')) != str(seller) for o in orders):
            raise BlingError('Pedido não pertence exclusivamente à conta autorizada')
        ids = {str((o.get('shipping') or {}).get('id')) for o in orders if (o.get('shipping') or {}).get('id')}
        if not shipment_id and len(ids) == 1:
            shipment_id = ids.pop()
        if not shipment_id:
            raise BlingError('Envio não identificado; conferir no Mercado Livre')
        shipment = self.read('/shipments/' + str(int(shipment_id)))
        # x-format-new places the sender inside origin, rather than at the root.
        sender_id = (shipment.get('origin') or {}).get('sender_id')
        if str(sender_id) != str(seller):
            raise BlingError('Envio de outra conta; consulta descartada')
        deadline, deadline_error = None, None
        try:
            sla = self.read('/shipments/' + str(int(shipment_id)) + '/sla')
            deadline = sla.get('expected_date')
            if deadline:
                datetime.fromisoformat(deadline.replace('Z', '+00:00'))
        except (BlingError, ValueError):
            deadline, deadline_error = None, 'Prazo de despacho indisponível na API; conferir no marketplace'
        return {'status': shipment.get('status'), 'substatus': shipment.get('substatus'), 'shipment_id': str(shipment_id), 'order_statuses': [o.get('status') for o in orders], 'dispatch_deadline': deadline, 'deadline_error': deadline_error, 'checked_at': datetime.now(timezone.utc).isoformat()}

    def sync(self):
        with self.sync_lock, self.store.connect() as db_lock:
            if self.store.database_url and not db_lock.execute('SELECT pg_try_advisory_xact_lock(110220)').fetchone()[0]:
                return
            token = self.token()
            rows = self.crm.snapshot()[0]
            for row in rows:
                if not row['external_number'].isdigit():
                    continue
                try:
                    result = self.resolve(row['external_number'], token['user_id'])
                except BlingError as error:
                    result = {'error': str(error), 'checked_at': datetime.now(timezone.utc).isoformat()}
                with self.store.connect() as db:
                    db.execute(f'INSERT INTO meli_shipments (order_key,payload) VALUES ({self.p},{self.p}) ON CONFLICT (order_key) DO UPDATE SET payload=excluded.payload', (row['key'], json.dumps(result)))
            with self.store.connect() as db:
                self.crm.put(db, 'meli_last_sync', datetime.now(timezone.utc).isoformat())
                self.crm.put(db, 'meli_error', '')

    def lookup(self, row):
        with self.store.connect() as db:
            saved = db.execute(f'SELECT payload FROM meli_shipments WHERE order_key={self.p}', (row['key'],)).fetchone()
        return json.loads(saved[0]) if saved else None

    def valid(self, row):
        data = self.lookup(row)
        if not data or data.get('error'):
            return None
        # Require a current canonical response; do not infer payment from delivery.
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data['checked_at'])).total_seconds()
        if age > 900:
            return None
        return bool(data['order_statuses']) and all(s == 'paid' for s in data['order_statuses'])

    def action(self, row):
        data = self.lookup(row)
        if not data:
            return None
        if data.get('error'):
            return 'Consulta automática pendente: ' + data['error']
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data['checked_at'])).total_seconds()
        prefix = 'Dados desatualizados; conferir no marketplace. ' if age > 900 else ''
        status = data.get('status')
        labels = {'delivered': 'Entregue; sem pendência de despacho', 'shipped': 'Despachado / em transporte; sem pendência de despacho', 'ready_to_ship': 'Aguardando despacho', 'pending': 'Envio pendente', 'handling': 'Em preparação', 'cancelled': 'Envio cancelado; conferir pedido', 'not_delivered': 'Não entregue; conferir ocorrência'}
        label = labels.get(status, 'Situação do envio a conferir: ' + str(status))
        deadline = data.get('dispatch_deadline')
        if status in ('ready_to_ship', 'pending', 'handling'):
            if deadline:
                label += '; prazo Mercado Livre: ' + deadline
            else:
                label += '; prazo de despacho a conferir'
        return prefix + label + ' — consultado na API do Mercado Livre'

    def summary(self):
        with self.store.connect() as db:
            connected = bool(self.crm.get(db, 'meli_tokens'))
            error = self.crm.get(db, 'meli_error')
        return connected, error

    def handle(self, env, start):
        def reply(status, text, headers=()):
            raw = text.encode()
            start(status, [('Content-Type', 'text/plain; charset=utf-8'), ('Content-Length', str(len(raw))), ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'), *headers])
            return [raw]
        path, method = env.get('PATH_INFO'), env.get('REQUEST_METHOD')
        if path == '/meli/notifications' and method == 'POST':
            try:
                length = int(env.get('CONTENT_LENGTH', 0))
                if not 0 < length <= 16384:
                    return reply('400 Bad Request', 'invalid body')
                try:
                    event = json.loads(env['wsgi.input'].read(length))
                except (ValueError, UnicodeDecodeError):
                    return reply('400 Bad Request', 'invalid JSON')
                if not isinstance(event, dict):
                    return reply('400 Bad Request', 'invalid notification')
                topic = event.get('topic')
                pattern = r'/orders/[0-9]+' if topic == 'orders_v2' else r'/shipments/[0-9]+' if topic == 'shipments' else ''
                if str(event.get('application_id')) != self.client_id or not pattern or not re.fullmatch(pattern, event.get('resource', '')):
                    return reply('400 Bad Request', 'invalid notification')
                with self.store.connect() as db:
                    raw = self.crm.get(db, 'meli_tokens')
                    if not raw:
                        return reply('503 Service Unavailable', 'account not connected')
                    seller = json.loads(self.cipher.decrypt(raw.encode()))['user_id']
                    if str(event.get('user_id')) != str(seller):
                        return reply('403 Forbidden', 'wrong account')
                    # Notifications are untrusted signals only. Never accept status or URLs from them.
                    self.crm.put(db, 'meli_dirty', '1')
                return reply('200 OK', 'received')
            except Exception:
                return reply('503 Service Unavailable', 'notification unavailable')
        if method != 'GET':
            return reply('405 Method Not Allowed', 'method not allowed')
        if path != '/meli/callback':
            expected = 'Basic ' + base64.b64encode(('admin:' + self.crm.settings['CRM_ADMIN_PASSWORD']).encode()).decode()
            if not hmac.compare_digest(env.get('HTTP_AUTHORIZATION', ''), expected):
                return reply('401 Unauthorized', 'Acesso privado', [('WWW-Authenticate', 'Basic realm="Fazendo Fita"')])
        try:
            if path == '/meli/connect':
                state = secrets.token_urlsafe(32)
                with self.store.connect() as db:
                    db.execute(f'INSERT INTO bling_states VALUES ({self.p},{self.p})', (hashlib.sha256(('meli:' + state).encode()).hexdigest(), time.time() + 600))
                url = 'https://auth.mercadolivre.com.br/authorization?' + urlencode({'response_type': 'code', 'client_id': self.client_id, 'redirect_uri': REDIRECT, 'state': state})
                return reply('302 Found', '', [('Location', url), ('Set-Cookie', 'meli_state=' + state + '; Path=/meli; Secure; HttpOnly; SameSite=Lax; Max-Age=600')])
            if path == '/meli/callback':
                query = parse_qs(env.get('QUERY_STRING', ''))
                cookie = SimpleCookie(env.get('HTTP_COOKIE', '')).get('meli_state')
                self.authorize(query.get('code', [''])[0], query.get('state', [''])[0], cookie.value if cookie else '')
                return reply('302 Found', '', [('Location', '/crm'), ('Set-Cookie', 'meli_state=; Path=/meli; Secure; HttpOnly; SameSite=Lax; Max-Age=0')])
        except BlingError as error:
            return reply('400 Bad Request', str(error))
        except Exception:
            return reply('503 Service Unavailable', 'Falha na conexão; tente novamente sem compartilhar credenciais')
        return reply('404 Not Found', 'not found')

    def run(self):
        last = 0
        while True:
            try:
                with self.store.connect() as db:
                    connected = bool(self.crm.get(db, 'meli_tokens'))
                    dirty = self.crm.get(db, 'meli_dirty') == '1'
                    if connected and (dirty or time.monotonic() - last >= 300):
                        self.crm.put(db, 'meli_dirty', '0')
                if connected and (dirty or time.monotonic() - last >= 300):
                    self.sync()
                    last = time.monotonic()
            except Exception as error:
                safe = str(error) if isinstance(error, BlingError) else 'Consulta automática requer conferência'
                try:
                    with self.store.connect() as db:
                        self.crm.put(db, 'meli_error', safe)
                except Exception:
                    pass
                last = time.monotonic()
            time.sleep(15)


def configure(crm):
    if not crm or not os.environ.get('MELI_CLIENT_ID') or not os.environ.get('MELI_CLIENT_SECRET'):
        return None
    integration = Meli(crm, os.environ['MELI_CLIENT_ID'], os.environ['MELI_CLIENT_SECRET'])
    threading.Thread(target=integration.run, daemon=True, name='meli-import').start()
    return integration
