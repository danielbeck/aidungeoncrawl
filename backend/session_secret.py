import json
import os

from dotenv import dotenv_values


def load_settings():
    secret_file = os.environ.get('APP_SECRET_FILE')
    if secret_file:
        with open(secret_file) as source:
            settings = json.load(source)
        if not isinstance(settings, dict):
            raise ValueError('Private server configuration must be a JSON object.')
    else:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        settings = dict(dotenv_values(env_file, interpolate=False))
        settings.update(os.environ)
    return settings


def load_session_secret():
    key = load_settings().get('SECRET_KEY')
    if not isinstance(key, str) or not key.strip():
        raise ValueError('SECRET_KEY is required in the environment, local .env, or private server configuration.')
    return key
