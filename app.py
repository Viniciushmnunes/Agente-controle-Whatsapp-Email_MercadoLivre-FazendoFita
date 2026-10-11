"""WhatsApp ingress. No outbound messages or classification in this stage."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import sqlite3
from urllib.parse import parse_qs

MAX_BODY = 1024 * 1024


class Store:
    def __init__(self, database_url=None, local_path=None):
        self.database_url = database_url
        self.local_path = local_path
        if not database_url and not local_path:
            raise ValueError('Configure DATABASE_URL; SQLITE_PATH is only for local tests')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS webhook_events (event_key TEXT PRIMARY KEY, payload TEXT NOT NULL, received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)')

    @contextmanager
    def connect(self):
        if self.database_url:
            import psycopg
            connection = psycopg.connect(self.database_url, connect_timeout=5)
        else:
            connection = sqlite3.connect(self.local_path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, raw):
        key = hashlib.sha256(raw).hexdigest()
        placeholder = '%s' if self.database_url else '?'
        with self.connect() as db:
            db.execute(f'INSERT INTO webhook_events (event_key, payload) VALUES ({placeholder}, {placeholder}) ON CONFLICT (event_key) DO NOTHING', (key, raw.decode('utf-8')))


class Webhook:
    def __init__(self, verify_token, app_secret, store, bling=None):
        if not verify_token or not app_secret:
            raise ValueError('VERIFY_TOKEN and META_APP_SECRET are required')
        self.verify_token = verify_token
        self.app_secret = app_secret.encode()
        self.store = store
        self.bling = bling
        self.meli = None

    def __call__(self, environ, start_response):
        def respond(status, text):
            body = text.encode()
            start_response(status, [('Content-Type', 'text/plain; charset=utf-8'), ('Content-Length', str(len(body)))])
            return [body]

        path, method = environ.get('PATH_INFO'), environ.get('REQUEST_METHOD')
        if self.meli and path in ('/meli/connect', '/meli/callback', '/meli/notifications'):
            return self.meli.handle(environ, start_response)
        if self.bling and (path == '/crm' or path in ('/bling/connect', '/bling/callback', '/bling/stores', '/bling/statuses')):
            return self.bling.handle(environ, start_response)
        if path == '/healthz' and method == 'GET':
            try:
                with self.store.connect() as db:
                    db.execute('SELECT 1')
                return respond('200 OK', 'ok')
            except Exception:
                return respond('503 Service Unavailable', 'storage unavailable')
        if path != '/webhook':
            return respond('404 Not Found', 'not found')
        if method == 'GET':
            query = parse_qs(environ.get('QUERY_STRING', ''))
            token = query.get('hub.verify_token', [''])[0]
            challenge = query.get('hub.challenge', [''])[0]
            if query.get('hub.mode') == ['subscribe'] and challenge and hmac.compare_digest(token.encode(), self.verify_token.encode()):
                return respond('200 OK', challenge)
            return respond('403 Forbidden', 'verification failed')
        if method != 'POST':
            return respond('405 Method Not Allowed', 'method not allowed')
        try:
            length = int(environ.get('CONTENT_LENGTH') or '0')
        except ValueError:
            return respond('400 Bad Request', 'invalid length')
        if length <= 0:
            return respond('400 Bad Request', 'empty body')
        if length > MAX_BODY:
            return respond('413 Payload Too Large', 'payload too large')
        raw = environ['wsgi.input'].read(length)
        if len(raw) != length:
            return respond('400 Bad Request', 'incomplete body')
        signature = environ.get('HTTP_X_HUB_SIGNATURE_256', '')
        expected = 'sha256=' + hmac.new(self.app_secret, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            return respond('403 Forbidden', 'invalid signature')
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get('object') != 'whatsapp_business_account' or not isinstance(payload.get('entry'), list):
                return respond('400 Bad Request', 'invalid event')
        except (ValueError, UnicodeDecodeError):
            return respond('400 Bad Request', 'invalid JSON')
        try:
            self.store.save(raw)
        except Exception:
            # Never print customer data, secrets, or database URLs.
            return respond('503 Service Unavailable', 'storage unavailable')
        return respond('200 OK', 'EVENT_RECEIVED')


def create_app():
    if os.environ.get('RENDER') and not os.environ.get('DATABASE_URL'):
        raise ValueError('Render requires persistent Postgres DATABASE_URL')
    store = Store(os.environ.get('DATABASE_URL'), os.environ.get('SQLITE_PATH'))
    from bling import configure
    crm = configure(store)
    from meli import configure as configure_meli
    app = Webhook(os.environ.get('VERIFY_TOKEN'), os.environ.get('META_APP_SECRET'), store, crm)
    app.meli = configure_meli(crm)
    if crm:
        crm.meli = app.meli
    return app
