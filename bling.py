"""Read-only Bling import and private CRM. No outbound customer messages."""
import base64
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet

API = 'https://api.bling.com.br/Api/v3'
TOKEN_URL = API + '/oauth/token'
AUTHORIZE_URL = 'https://www.bling.com.br/Api/v3/oauth/authorize'


class BlingError(Exception):
    """Safe error, without API bodies or credentials."""


class Bling:
    def __init__(self, store, settings, transport=None):
        self.store, self.settings = store, settings
        self.cipher = Fernet(settings['BLING_ENCRYPTION_KEY'].encode())
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.transport = transport or self.http
        self.last_request = 0
        self.p = '%s' if store.database_url else '?'
        self.start_date = date.fromisoformat(settings['BLING_START_DATE']).isoformat()
        self.shop = str(int(settings['BLING_STORE_ID'])) if settings.get('BLING_STORE_ID') else ''
        rules_path = Path(__file__).with_name('crm_rules.json')
        rules = json.loads(rules_path.read_text(encoding='utf-8')) if rules_path.exists() else {}
        self.rules = rules.get(self.shop, {})
        self.valid = self.ids('BLING_VALID_STATUS_IDS')
        self.excluded = self.ids('BLING_EXCLUDED_STATUS_IDS')
        self.pending = self.ids('BLING_PENDING_STATUS_IDS')
        if self.valid & self.excluded or self.pending & self.excluded:
            raise ValueError('Conflicting Bling status groups')
        if len(settings['CRM_ADMIN_PASSWORD']) < 20:
            raise ValueError('CRM_ADMIN_PASSWORD must have at least 20 characters')
        self.identity_key = base64.urlsafe_b64decode(settings['BLING_ENCRYPTION_KEY'])
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS bling_settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS bling_states (state TEXT PRIMARY KEY, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS crm_orders (order_key TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def ids(self, key):
        return {str(int(x.strip())) for x in self.settings.get(key, '').split(',') if x.strip()}

    def put(self, db, name, value):
        db.execute(f'INSERT INTO bling_settings (name,value) VALUES ({self.p},{self.p}) ON CONFLICT (name) DO UPDATE SET value=excluded.value', (name, value))

    def get(self, db, name):
        row = db.execute(f'SELECT value FROM bling_settings WHERE name={self.p}', (name,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def http(method, url, headers, body=None):
        try:
            with urlopen(Request(url, data=body, headers=headers, method=method), timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            raise BlingError('Bling HTTP ' + str(error.code)) from None
        except Exception:
            raise BlingError('Falha de conexão com o Bling') from None

    def token_request(self, data):
        auth = base64.b64encode((self.settings['BLING_CLIENT_ID'] + ':' + self.settings['BLING_CLIENT_SECRET']).encode()).decode()
        result = self.transport('POST', TOKEN_URL, {'Authorization': 'Basic ' + auth, 'Content-Type': 'application/x-www-form-urlencoded', 'enable-jwt': '1'}, urlencode(data).encode())
        if not result.get('access_token') or not result.get('refresh_token'):
            raise BlingError('Resposta de autorização incompleta')
        result['expires_at'] = time.time() + int(result['expires_in'])
        return self.cipher.encrypt(json.dumps(result).encode()).decode()

    def authorize(self, code, state, cookie):
        if not state or not cookie or not hmac.compare_digest(state, cookie):
            raise BlingError('Autorização inválida ou expirada; inicie novamente')
        hashed = hashlib.sha256(state.encode()).hexdigest()
        with self.lock, self.store.connect() as db:
            row = db.execute(f'DELETE FROM bling_states WHERE state={self.p} AND expires>{self.p} RETURNING state', (hashed, time.time())).fetchone()
        if not row:
            raise BlingError('Autorização inválida ou expirada; inicie novamente')
        encrypted = self.token_request({'grant_type': 'authorization_code', 'code': code})
        with self.lock, self.store.connect() as db:
            if self.store.database_url:
                db.execute('SELECT pg_advisory_xact_lock(110217)')
            self.put(db, 'tokens', encrypted)

    def access_token(self):
        # Serializes refresh-token rotation across calls within the single worker.
        with self.lock, self.store.connect() as db:
            if self.store.database_url:
                db.execute('SELECT pg_advisory_xact_lock(110217)')
            raw = self.get(db, 'tokens')
            if not raw:
                raise BlingError('Bling ainda não conectado')
            token = json.loads(self.cipher.decrypt(raw.encode()))
            if token['expires_at'] <= time.time() + 60:
                raw = self.token_request({'grant_type': 'refresh_token', 'refresh_token': token['refresh_token']})
                self.put(db, 'tokens', raw)
                token = json.loads(self.cipher.decrypt(raw.encode()))
            return token['access_token']

    def read(self, path, query=None):
        # Bling permits three requests per second; all GETs are serialized.
        with self.lock:
            time.sleep(max(0, 0.4 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            return self.transport('GET', API + path + ('?' + urlencode(query) if query else ''), {'Authorization': 'Bearer ' + self.access_token(), 'enable-jwt': '1'})['data']

    def identity(self, document, contact_id):
        digits = re.sub(r'\D', '', document or '')
        # Do not retain complete documents. Invalid/absent documents stay separate.
        if len(digits) in (11, 14) and len(set(digits)) > 1:
            key = hmac.new(self.identity_key, digits.encode(), hashlib.sha256).hexdigest()
            return 'doc:' + key, '*' * (len(digits) - 4) + digits[-4:]
        return 'bling:' + str(contact_id), 'Não disponível'

    def normalize(self, order, contact):
        identity, mask = self.identity(contact.get('numeroDocumento'), contact['id'])
        status = str(order.get('situacao', {}).get('id', ''))
        shop = str(order.get('loja', {}).get('id', ''))
        external = str(order.get('numeroLoja') or '')
        return {
            'key': 'shop:' + shop + ':' + external if external else 'bling:' + str(order['id']),
            'bling_id': str(order['id']), 'bling_number': str(order.get('numero', '')),
            'external_number': external, 'customer_key': identity, 'name': contact.get('nome', ''),
            'document': mask, 'date': order['data'], 'total': str(order.get('total', 0)),
            'products_total': str(order['totalProdutos']) if order.get('totalProdutos') is not None else None,
            'freight': str(order['transporte']['frete']) if (order.get('transporte') or {}).get('frete') is not None else None,
            'status': status, 'expected_date': order.get('dataPrevista', ''),
            'items': [{'description': i.get('descricao', ''), 'sku': i.get('codigo', ''), 'quantity': i.get('quantidade', 0)} for i in order.get('itens', [])],
        }

    def sync(self):
        if not self.shop:
            raise BlingError('Selecione a loja antes de importar')
        with self.sync_lock:
            if not self.store.database_url:
                return self._sync()
            # Protect rolling deployments from replacing the same snapshot concurrently.
            with self.store.connect() as db:
                acquired = db.execute('SELECT pg_try_advisory_xact_lock(110218)').fetchone()[0]
                if not acquired:
                    return None
                return self._sync()

    def _sync(self):
        # Refresh the selected history each time so old cancellations are updated.
        # Scoped to one configured store; never imports other Bling contacts en masse.
        contacts, rows, page = {}, {}, 1
        while True:
            orders = self.read('/pedidos/vendas', {'pagina': page, 'limite': 100, 'idLoja': self.shop, 'dataInicial': self.start_date})
            if not orders:
                break
            for stub in orders:
                order = self.read('/pedidos/vendas/' + str(int(stub['id'])))
                if str(order.get('loja', {}).get('id')) != self.shop:
                    continue
                contact_id = int(order['contato']['id'])
                if contact_id not in contacts:
                    contacts[contact_id] = self.read('/contatos/' + str(contact_id))
                row = self.normalize(order, contacts[contact_id])
                rows[row['key']] = row
            page += 1
        # Commit only a complete successful snapshot; errors preserve the last import.
        with self.store.connect() as db:
            db.execute('DELETE FROM crm_orders')
            for key, row in rows.items():
                db.execute(f'INSERT INTO crm_orders (order_key,payload) VALUES ({self.p},{self.p})', (key, json.dumps(row, ensure_ascii=False)))
            self.put(db, 'last_sync', datetime.now(timezone.utc).isoformat())
            self.put(db, 'last_error', '')
        return len(rows)

    def purchase_valid(self, row):
        if row['status'] in self.excluded:
            return False
        return row['status'] in self.valid or self.rules.get('orders', {}).get(row['external_number'], {}).get('valid_purchase', False)

    def delivery_action(self, row):
        if row['status'] in self.excluded:
            return 'Excluído da recorrência'
        rule = self.rules.get('orders', {}).get(row['external_number'], {})
        if rule.get('delivery') == 'delivered':
            return 'Entregue — confirmado pelo responsável; sem pendência de despacho'
        if rule.get('delivery') == 'scheduled':
            return 'Despacho previsto em ' + date.fromisoformat(rule['dispatch_date']).strftime('%d/%m/%Y') + ' — informado pelo responsável; conferir realização'
        return 'Conferir etiqueta e despacho' if row['status'] in self.pending else 'Compra válida; envio a conferir' if self.purchase_valid(row) else 'Conferir situação'

    def snapshot(self):
        with self.store.connect() as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM crm_orders').fetchall()]
            last_sync, error = self.get(db, 'last_sync'), self.get(db, 'last_error')
            connected = bool(self.get(db, 'tokens'))
        rows = [r for r in rows if r['bling_number'] not in self.rules.get('excluded_bling_numbers', [])]
        customers = defaultdict(list)
        for row in rows:
            customers[row['customer_key']].append(row)
        return rows, customers, last_sync, error, connected

    @staticmethod
    def money(value):
        return 'Não informado' if value is None else 'R$ ' + format(Decimal(value), '.2f').replace('.', ',')

    def products_sum(self, rows):
        if any(x.get('products_total') is None for x in rows):
            return 'Aguardando atualização'
        return self.money(sum((Decimal(x['products_total']) for x in rows), Decimal('0')))

    def page(self):
        rows, customers, last_sync, error, connected = self.snapshot()
        esc = lambda value: html.escape(str(value if value is not None else ''))
        parts = ['<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Fazendo Fita — CRM</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:16px;color:#263238}table{border-collapse:collapse;width:100%;margin:20px 0}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}small{color:#666}</style><h1>Clientes e pedidos — Fazendo Fita</h1>']
        parts.append('<p>' + ('Bling conectado.' if connected else '<a href="/bling/connect">Conectar ao Bling</a>') + '</p>')
        parts.append('<p>Última importação (UTC): ' + esc(last_sync or 'Ainda não realizada') + '</p>')
        if error:
            parts.append('<p>Importação pendente: ' + esc(error) + '. Os dados anteriores foram preservados.</p>')
        if not self.valid and not self.rules.get('orders'):
            parts.append('<p>As situações de compra válida ainda precisam ser configuradas. Os pedidos importados não são automaticamente contados como compras confirmadas.</p>')
        if connected and not self.shop:
            parts.append('<p>Selecione a loja do Mercado Livre na configuração do serviço. <a href="/bling/stores">Consultar IDs das lojas</a>.</p>')
        if self.rules.get('orders'):
            parts.append('<p>Compras e informações de envio dos pedidos já conferidos foram confirmadas pelo responsável. Novos pedidos continuam a conferir até validação. O andamento logístico automático ainda não está conectado.</p>')
        parts.append('<h2>Histórico de clientes</h2><table><tr><th>Cliente</th><th>Documento mascarado</th><th>Pedidos importados</th><th>Compras válidas</th><th>Perfil</th><th>Primeira compra válida</th><th>Última compra válida</th><th>Produtos nas compras válidas</th></tr>')
        for orders in customers.values():
            latest = max(orders, key=lambda x: x['date'])
            valid = [x for x in orders if self.purchase_valid(x)]
            profile = 'Recorrente' if len(valid) >= 2 else 'Compra única' if valid else 'A conferir'
            parts.append('<tr>' + ''.join('<td>' + esc(x) + '</td>' for x in (latest['name'], latest['document'], len(orders), len(valid), profile, min((x['date'] for x in valid), default=''), max((x['date'] for x in valid), default=''), self.products_sum(valid))) + '</tr>')
        parts.append('</table><h2>Pedidos e pendências</h2><p>A previsão do Bling não confirma o prazo de postagem do Mercado Livre. Confira o prazo no marketplace.</p><table><tr><th>Pedido Mercado Livre</th><th>Pedido Bling</th><th>Cliente</th><th>Produtos</th><th>Produtos</th><th>Frete do pedido</th><th>Total com frete</th><th>Data</th><th>Situação (ID)</th><th>Ação</th><th>Previsão Bling</th></tr>')
        for row in sorted(rows, key=lambda x: x['date'], reverse=True):
            status = row['status']
            action = self.delivery_action(row)
            parts.append('<tr>' + ''.join('<td>' + esc(x) + '</td>' for x in (row['external_number'], row['bling_number'], row['name'], '; '.join(str(i['quantity']) + ' × ' + i['description'] for i in row['items']), self.money(row.get('products_total')), self.money(row.get('freight')), self.money(row['total']), row['date'], status, action, row['expected_date'] if row['expected_date'] != '0000-00-00' else 'Não informada')) + '</tr>')
        parts.append('</table><p>Consulta interna. Nenhuma mensagem é enviada aos compradores.</p></html>')
        return ''.join(parts)

    def handle(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        def response(status, text, headers=(), content_type='text/plain; charset=utf-8'):
            body = text.encode()
            start_response(status, [('Content-Type', content_type), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'), ('X-Content-Type-Options', 'nosniff'), ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"), *headers])
            return [body]
        if environ.get('REQUEST_METHOD') != 'GET':
            return response('405 Method Not Allowed', 'method not allowed')
        if path != '/bling/callback':
            expected = 'Basic ' + base64.b64encode(('admin:' + self.settings['CRM_ADMIN_PASSWORD']).encode()).decode()
            if not hmac.compare_digest(environ.get('HTTP_AUTHORIZATION', ''), expected):
                return response('401 Unauthorized', 'Acesso privado', [('WWW-Authenticate', 'Basic realm="Fazendo Fita"')])
        try:
            if path == '/bling/stores':
                query = parse_qs(environ.get('QUERY_STRING', ''))
                page = max(1, min(100, int(query.get('pagina', ['1'])[0])))
                shops = self.read('/canais-venda', {'pagina': page, 'limite': 100})
                return response('200 OK', json.dumps([{'id': s.get('id'), 'nome': s.get('descricao') or s.get('nome'), 'tipo': s.get('tipo')} for s in shops], ensure_ascii=False), content_type='application/json; charset=utf-8')
            if path == '/bling/statuses':
                query = parse_qs(environ.get('QUERY_STRING', ''))
                module = query.get('modulo', [''])[0]
                path_api = '/situacoes/modulos/' + str(int(module)) if module else '/situacoes/modulos'
                return response('200 OK', json.dumps(self.read(path_api), ensure_ascii=False), content_type='application/json; charset=utf-8')
            if path == '/crm':
                return response('200 OK', self.page(), content_type='text/html; charset=utf-8')
            if path == '/bling/connect':
                state = secrets.token_urlsafe(32)
                with self.store.connect() as db:
                    db.execute(f'DELETE FROM bling_states WHERE expires < {self.p}', (time.time(),))
                    db.execute(f'INSERT INTO bling_states (state,expires) VALUES ({self.p},{self.p})', (hashlib.sha256(state.encode()).hexdigest(), time.time() + 600))
                url = AUTHORIZE_URL + '?' + urlencode({'response_type': 'code', 'client_id': self.settings['BLING_CLIENT_ID'], 'state': state})
                return response('302 Found', '', [('Location', url), ('Set-Cookie', 'bling_state=' + state + '; Path=/bling; Secure; HttpOnly; SameSite=Lax; Max-Age=600')])
            if path == '/bling/callback':
                query = parse_qs(environ.get('QUERY_STRING', ''))
                cookies = SimpleCookie(environ.get('HTTP_COOKIE', ''))
                cookie = cookies.get('bling_state')
                self.authorize(query.get('code', [''])[0], query.get('state', [''])[0], cookie.value if cookie else '')
                return response('302 Found', '', [('Location', '/crm'), ('Set-Cookie', 'bling_state=; Path=/bling; Secure; HttpOnly; SameSite=Lax; Max-Age=0')])
        except BlingError as error:
            return response('400 Bad Request', str(error))
        except Exception:
            return response('503 Service Unavailable', 'Não foi possível concluir. Nenhuma credencial foi exibida; tente novamente.')
        return response('404 Not Found', 'not found')

    def run(self):
        while True:
            try:
                with self.store.connect() as db:
                    connected = bool(self.get(db, 'tokens'))
                if connected and self.shop:
                    self.sync()
            except Exception as error:
                safe = str(error) if isinstance(error, BlingError) else 'Falha interna; requer conferência'
                try:
                    with self.store.connect() as db:
                        self.put(db, 'last_error', safe)
                except Exception:
                    pass
            time.sleep(300)


def configure(store):
    # Optional until the owner configures the application and authorizes it.
    if not os.environ.get('BLING_CLIENT_ID'):
        return None
    keys = ('BLING_CLIENT_ID', 'BLING_CLIENT_SECRET', 'BLING_ENCRYPTION_KEY', 'BLING_START_DATE', 'CRM_ADMIN_PASSWORD')
    settings = {key: os.environ[key] for key in keys}
    settings['BLING_STORE_ID'] = os.environ.get('BLING_STORE_ID', '')
    for key in ('BLING_VALID_STATUS_IDS', 'BLING_EXCLUDED_STATUS_IDS', 'BLING_PENDING_STATUS_IDS'):
        settings[key] = os.environ.get(key, '')
    integration = Bling(store, settings)
    threading.Thread(target=integration.run, name='bling-import', daemon=True).start()
    return integration
