import os
from wsgiref.handlers import CGIHandler

os.environ.setdefault('APP_BASE_DIR', os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('APP_SECRET_FILE', os.path.expanduser('~/.config/react-python-sqlite-boilerplate/session.json'))
os.makedirs(os.path.join(os.environ['APP_BASE_DIR'], 'logs'), exist_ok=True)

import logging_helper

try:
    from app import app
    CGIHandler().run(app.wsgi_app)
except Exception:
    logging_helper.log_exception()
    raise
