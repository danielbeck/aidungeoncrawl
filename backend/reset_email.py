import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlsplit


def reset_email_settings(config):
    required = ('SMTP_HOST', 'SMTP_FROM_EMAIL', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'PASSWORD_RESET_URL')
    if any(not config.get(key) for key in required):
        raise ValueError('SMTP settings and PASSWORD_RESET_URL are required.')
    port = int(config.get('SMTP_PORT') or 587)
    if not 1 <= port <= 65535:
        raise ValueError('SMTP_PORT must be between 1 and 65535.')
    secure = str(config.get('SMTP_SECURE') or 'false').lower()
    if secure not in ('true', 'false'):
        raise ValueError('SMTP_SECURE must be true or false.')
    url = urlsplit(config['PASSWORD_RESET_URL'])
    if url.scheme not in ('http', 'https') or not url.netloc or url.fragment or url.query:
        raise ValueError('PASSWORD_RESET_URL must be an absolute URL without a query or fragment.')
    return port, secure == 'true'


def send_reset_email(config, recipient, token):
    port, secure = reset_email_settings(config)
    message = EmailMessage()
    message['Subject'] = 'Reset your password'
    message['From'] = formataddr((config.get('SMTP_FROM_NAME') or '', config['SMTP_FROM_EMAIL']))
    message['To'] = recipient
    # A fragment keeps the secret out of HTTP request URLs and access logs.
    link = '{}#token={}'.format(config['PASSWORD_RESET_URL'], token)
    message.set_content(
        'A password reset was requested for your account.\n\n'
        'Enter and confirm a new password here:\n{}\n\n'
        'This link expires in 15 minutes and can only be used once.\n'
        'If you did not request this, you can ignore this email.\n'.format(link)
    )
    context = ssl.create_default_context()
    smtp_class = smtplib.SMTP_SSL if secure else smtplib.SMTP
    options = {'timeout': 15}
    if secure:
        options['context'] = context
    with smtp_class(config['SMTP_HOST'], port, **options) as smtp:
        if not secure:
            smtp.starttls(context=context)
        smtp.login(config['SMTP_USERNAME'], config['SMTP_PASSWORD'])
        smtp.send_message(message)
