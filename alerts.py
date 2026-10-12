"""Owner-only email summaries with durable per-recipient send claims."""
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
import os
import smtplib
import ssl
import threading
import time
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('America/Sao_Paulo')
RECIPIENTS = ('contato@fazendofita.com.br', 'patyalves.seven@hotmail.com', 'vhm.nunes@gmail.com')


class Alerts:
    def __init__(self, crm, settings, sender=None):
        self.crm, self.settings = crm, settings
        self.store, self.p = crm.store, crm.p
        self.sender = sender or self.smtp
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS email_deliveries (job TEXT NOT NULL, recipient TEXT NOT NULL, state TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(job,recipient))')

    def smtp(self, recipient, subject, body):
        message = EmailMessage()
        message['From'] = self.settings['SMTP_USER']
        message['To'] = recipient
        message['Subject'] = subject
        message['Message-ID'] = make_msgid()
        message.set_content(body)
        with smtplib.SMTP_SSL(self.settings['SMTP_HOST'], int(self.settings['SMTP_PORT']), timeout=25, context=ssl.create_default_context()) as client:
            client.login(self.settings['SMTP_USER'], self.settings['SMTP_PASSWORD'])
            if client.send_message(message):
                raise RuntimeError('Recipient refused')

    def delivered(self, row, now):
        if not self.crm.meli:
            return False
        data = self.crm.meli.lookup(row)
        if not data or data.get('error') or data.get('status') != 'delivered':
            return False
        try:
            age = (now - datetime.fromisoformat(data['checked_at'])).total_seconds()
            return 0 <= age <= 900
        except (KeyError, ValueError, TypeError):
            return False

    def summary(self, now):
        rows, _, imported, error, connected = self.crm.snapshot()
        lines = ['Fazendo Fita — resumo de pedidos', now.astimezone(ZONE).strftime('%d/%m/%Y às %H:%M (Brasília)'), '']
        if not connected or not imported:
            lines.append('Importação ainda não disponível. Confira o painel antes de tomar decisões.')
        else:
            age = (now - datetime.fromisoformat(imported)).total_seconds()
            if error or age > 900:
                lines.append('ATENÇÃO: importação pendente ou desatualizada; os pedidos abaixo podem estar incompletos.')
            lines.append('Última importação: ' + datetime.fromisoformat(imported).astimezone(ZONE).strftime('%d/%m/%Y às %H:%M'))
        pending = [row for row in rows if not self.delivered(row, now)]
        for row in sorted(pending, key=lambda r: r['date'], reverse=True):
            lines.extend(['', 'Pedido Bling ' + str(row['bling_number']) + ' | Mercado Livre ' + str(row['external_number']),
                          self.crm.delivery_action(row),
                          'Produtos: ' + self.crm.money(row.get('products_total')) + ' | Frete: ' + self.crm.money(row.get('freight')) + ' | Total: ' + self.crm.money(row['total'])])
        if rows and not pending:
            lines.append('Nenhum pedido pendente entre os pedidos importados: todos têm entrega confirmada em consulta recente.')
        if not rows:
            lines.append('Nenhum pedido disponível no painel; isso não confirma ausência de vendas.')
        lines.extend(['', 'Conferir detalhes: https://fazendo-fita-webhook.onrender.com/crm', 'Resumo interno. Nenhuma mensagem é enviada aos compradores.'])
        return '\n'.join(lines)

    def deliver(self, job, subject, body, now):
        # Commit the claim before SMTP: uncertain attempts are never repeated automatically.
        for recipient in RECIPIENTS:
            with self.store.connect() as db:
                claimed = db.execute(f'INSERT INTO email_deliveries VALUES ({self.p},{self.p},{self.p},{self.p}) ON CONFLICT DO NOTHING RETURNING job', (job, recipient, 'sending', now.isoformat())).fetchone()
            if not claimed:
                continue
            try:
                self.sender(recipient, subject, body)
                state = 'accepted'
            except Exception:
                state = 'uncertain'
            with self.store.connect() as db:
                db.execute(f'UPDATE email_deliveries SET state={self.p},updated={self.p} WHERE job={self.p} AND recipient={self.p}', (state, datetime.now(timezone.utc).isoformat(), job, recipient))

    def tick(self, now):
        test_id = self.settings.get('EMAIL_TEST_ID', '')
        if test_id:
            self.deliver('test:' + test_id, 'Fazendo Fita — teste dos alertas', 'Este é um teste dos alertas internos da Fazendo Fita.\nDestinatário: um dos três responsáveis configurados.\nNenhuma mensagem foi enviada aos compradores.', now)
        if self.settings.get('EMAIL_ENABLED', '').lower() != 'true':
            return
        local = now.astimezone(ZONE)
        for hour, minute in ((12, 0), (16, 30)):
            due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Short grace period handles restarts without sending old summaries hours later.
            if 0 <= (local - due).total_seconds() < 900:
                job = 'summary:' + due.strftime('%Y-%m-%d:%H%M')
                self.deliver(job, 'Fazendo Fita — pedidos ' + due.strftime('%d/%m %H:%M'), self.summary(now), now)

    def status(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT job,recipient,state FROM email_deliveries ORDER BY updated DESC LIMIT 9').fetchall()
        scheduled = self.settings.get('EMAIL_ENABLED', '').lower() == 'true'
        label = 'E-mails: resumos às 12h e às 16h30 (Brasília).' if scheduled else 'E-mails: envio de teste; resumos automáticos ainda desativados.'
        labels = {'accepted': 'aceito pelo servidor de envio; conferir recebimento', 'sending': 'tentativa iniciada; conferir recebimento', 'uncertain': 'falha ou resultado incerto; conferir configuração e recebimento'}
        label += ' Pedidos com entrega confirmada em consulta recente ficam fora dos resumos.'
        return label, [' | '.join((job, recipient, labels[state])) for job, recipient, state in rows]

    def run(self):
        while True:
            try:
                self.tick(datetime.now(timezone.utc))
                with self.store.connect() as db:
                    self.crm.put(db, 'email_error', '')
            except Exception:
                try:
                    with self.store.connect() as db:
                        self.crm.put(db, 'email_error', 'Não foi possível concluir a rotina de e-mails; conferir configuração.')
                except Exception:
                    pass
            time.sleep(30)


def configure(crm):
    keys = ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD')
    if not crm or not all(os.environ.get(key) for key in keys):
        return None
    settings = {key: os.environ[key] for key in (*keys, 'EMAIL_ENABLED', 'EMAIL_TEST_ID') if key in os.environ}
    alerts = Alerts(crm, settings)
    threading.Thread(target=alerts.run, name='owner-emails', daemon=True).start()
    return alerts
