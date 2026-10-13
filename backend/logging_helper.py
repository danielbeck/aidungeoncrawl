"""Small logging helper for CGI scripts.

Use this from a standalone CGI script to append tracebacks to the
backend/cgi_error.log file (with /tmp fallback) so logs are stored
consistently.

Example:
    try:
        ...
    except Exception:
        import logging_helper
        logging_helper.log_exception()
        raise
"""
from datetime import datetime
import os
import traceback

def _get_paths():
    base = os.environ.get('APP_BASE_DIR') or os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(base, 'logs')
    error_log = os.path.join(logs_dir, 'cgi_error.log')
    tmp_log = os.path.join(logs_dir, 'nosebeep_api_logs.txt')
    return error_log, tmp_log


def log_exception(exc_info=None):
    tb = None
    try:
        if exc_info:
            tb = ''.join(traceback.format_exception(*exc_info))
        else:
            tb = traceback.format_exc()
    except Exception:
        tb = 'Unable to format traceback'

    error_log, tmp_log = _get_paths()
    try:
        with open(error_log, 'a') as f:
            f.write('\n--- %s ---\n' % datetime.utcnow().isoformat())
            f.write(tb)
            f.write('\n')
    except Exception:
        try:
            with open(tmp_log, 'a') as tf:
                tf.write('%s\n%s\n' % (datetime.utcnow().isoformat(), tb))
        except Exception:
            pass


if __name__ == '__main__':
    # quick smoke test
    try:
        raise RuntimeError('logging_helper smoke')
    except Exception:
        log_exception()
