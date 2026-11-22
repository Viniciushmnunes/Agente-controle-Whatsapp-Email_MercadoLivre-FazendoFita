"""Server-side forwarding from the validated WhatsApp monitor to the Fazendo Fita CRM."""
import hashlib
import hmac
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class CrmBridge:
    def __init__(self, url='', secret='', transport=None):
        self.url = (url or '').strip()
        self.secret = (secret or '').strip().encode()
        self.transport = transport or self._send
        if bool(self.url) != bool(self.secret):
            raise ValueError('CRM bridge URL and HMAC secret must be configured together')
        if self.url and not self.url.lower().startswith('https://'):
            raise ValueError('CRM bridge requires HTTPS')

    @property
    def enabled(self):
        return bool(self.url and self.secret)

    def signature(self, raw, timestamp):
        digest = hmac.new(self.secret, f'{timestamp}.'.encode() + raw, hashlib.sha256).hexdigest()
        return 'sha256=' + digest

    @staticmethod
    def _send(url, headers, raw):
        try:
            with urlopen(Request(url, data=raw, headers=headers, method='POST'), timeout=10) as response:
                return response.status
        except HTTPError as error:
            return error.code

    def forward(self, event, automatic=False):
        if not self.enabled:
            return False
        direction = 'inbound' if event.get('role') == 'user' else 'outbound'
        payload = {
            'messageId': event['messageId'],
            'phone': event['contactPhone'],
            'name': event.get('contactName', ''),
            'text': event.get('message', ''),
            'occurredAt': event['date'],
            'direction': direction,
            'automatic': bool(automatic),
            'source': 'whatsapp-agent',
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        timestamp = str(int(time.time()))
        status = self.transport(self.url, {
            'Content-Type': 'application/json',
            'Content-Length': str(len(raw)),
            'User-Agent': 'FazendoFita-CRM-Bridge/1.0',
            'X-CRM-Timestamp': timestamp,
            'X-CRM-Signature': self.signature(raw, timestamp),
        }, raw)
        if status not in (200, 201):
            raise RuntimeError('CRM bridge rejected message')
        return True
