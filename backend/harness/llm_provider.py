"""Minimal LLM connectivity layer supporting Ollama and OpenRouter.

Provides `chat` (a list of messages plus an optional JSON schema
response_format) and the single-prompt convenience wrapper `call_llm`. Both
send the request to whichever provider is configured and return the raw text
response for the caller to parse/validate.
"""
import json
import re
import uuid
import urllib.error
import urllib.request

DEFAULT_OLLAMA_URL = 'http://localhost:11434'
DEFAULT_OLLAMA_MODEL = 'llama3.2:latest'
DEFAULT_OPENROUTER_URL = 'https://openrouter.ai/api/v1'
DEFAULT_OPENROUTER_MODEL = 'openrouter/free'


class LLMError(Exception):
    """Raised when a provider request fails or is misconfigured."""

    def __init__(self, message, details=None, code=None):
        super().__init__(message)
        self.details = details
        self.code = code


def get_llm_config(settings):
    """Resolve provider + connection settings from environment/.env settings.

    `LLM_PROVIDER` ('ollama' or 'openrouter') takes precedence when set.
    Otherwise, defaults to `ollama` for local development and `openrouter`
    in production (FLASK_ENV == 'production').
    """
    provider = (settings.get('LLM_PROVIDER') or '').strip().lower()
    if provider not in ('ollama', 'openrouter'):
        provider = 'openrouter' if settings.get('FLASK_ENV') == 'production' else 'ollama'

    domain = (settings.get('PRODUCTION_DOMAIN') or '').strip().rstrip('/')
    sub_dir = (settings.get('DEPLOY_SUB_DIR') or '').strip('/')
    return {
        'provider': provider,
        'ollama_url': settings.get('OLLAMA_URL') or DEFAULT_OLLAMA_URL,
        'ollama_model': settings.get('OLLAMA_MODEL') or DEFAULT_OLLAMA_MODEL,
        'openrouter_url': settings.get('OPENROUTER_URL') or DEFAULT_OPENROUTER_URL,
        'openrouter_api_key': settings.get('OPENROUTER_API_KEY'),
        'openrouter_model': settings.get('OPENROUTER_MODEL') or DEFAULT_OPENROUTER_MODEL,
        'http_referer': f"{domain}/{sub_dir + '/' if sub_dir else ''}" if domain else None,
    }


def _post_json(url, payload, headers=None, timeout=60):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        details = error.read().decode('utf-8', errors='replace')
        raise LLMError(f'Failed to get a response from {url} (HTTP {error.code}).',
                       details=details) from error
    except urllib.error.URLError as error:
        raise LLMError(f'Failed to reach {url}.', details=str(error.reason)) from error


def _tool_messages(messages, provider):
    encoded = []
    for message in messages:
        message = {**message}
        if 'tool_calls' in message:
            calls = []
            for call in message['tool_calls']:
                function = {**call['function']}
                if provider == 'openrouter':
                    function['arguments'] = json.dumps(function['arguments'])
                    calls.append({**call, 'function': function})
                else:
                    calls.append({'function': function})
            message['tool_calls'] = calls
        if message['role'] == 'tool' and provider == 'ollama':
            message['tool_name'] = message.pop('name')
            message.pop('tool_call_id', None)
        encoded.append(message)
    return encoded


def _tool_response(message, provider, model):
    if not isinstance(message, dict):
        raise LLMError('The game master provider returned no assistant message.')
    content = message.get('content')
    calls = message.get('tool_calls')
    content = '' if content is None else content
    calls = [] if calls is None else calls
    if not isinstance(content, str) or not isinstance(calls, list):
        raise LLMError('The game master provider returned an invalid assistant message.')
    normalized = []
    for call in calls:
        function = call.get('function') if isinstance(call, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get('name'), str):
            raise LLMError('The game master provider returned an invalid tool call.')
        arguments = function.get('arguments')
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise LLMError('The game master returned invalid tool argument JSON.',
                               details=str(error)) from error
        if not isinstance(arguments, dict):
            raise LLMError('The game master tool arguments must be a JSON object.')
        call_id = call.get('id') or f'call_{uuid.uuid4().hex}'
        if not isinstance(call_id, str):
            raise LLMError('The game master returned an invalid tool call ID.')
        normalized.append({
            'id': call_id, 'type': 'function',
            'function': {'name': function['name'], 'arguments': arguments},
        })
    if len({call['id'] for call in normalized}) != len(normalized):
        raise LLMError('The game master returned duplicate tool call IDs.')
    return {
        'provider': provider, 'model': model, 'response': content,
        'message': {'role': 'assistant', 'content': content, 'tool_calls': normalized},
    }


def call_ollama(messages, schema, config, tools=None):
    payload = {
        'model': config['ollama_model'],
        'messages': _tool_messages(messages, 'ollama') if tools is not None else messages,
        'stream': False,
    }
    if config.get('temperature') is not None:
        payload['options'] = {'temperature': config['temperature']}
    if schema is not None:
        payload['format'] = schema
    if tools is not None:
        payload['tools'] = tools
        payload.setdefault('options', {}).update({'num_ctx': 16384, 'num_predict': 2048})

    data = _post_json(f"{config['ollama_url']}/api/chat", payload)
    message = data.get('message')
    if tools is not None:
        if data.get('done_reason') == 'length':
            raise LLMError('The game master response was truncated before it completed.')
        return _tool_response(message, 'ollama', config['ollama_model'])
    content = message.get('content', '') if isinstance(message, dict) else ''
    return {
        'provider': 'ollama',
        'model': config['ollama_model'],
        'response': content,
    }


def call_openrouter(messages, schema, schema_name, config, tools=None):
    api_key = config.get('openrouter_api_key')
    if not api_key:
        raise LLMError('OPENROUTER_API_KEY is not set.', code='MISSING_OPENROUTER_API_KEY')

    payload = {
        'model': config['openrouter_model'],
        'messages': _tool_messages(messages, 'openrouter') if tools is not None else messages,
    }
    if config.get('temperature') is not None:
        payload['temperature'] = config['temperature']
    if schema is not None:
        payload['response_format'] = {
            'type': 'json_schema',
            'json_schema': {
                'name': schema_name or 'response',
                'schema': schema,
                'strict': True,
            },
        }
        # Only route to providers that honor response_format; some free models ignore it.
        payload['provider'] = {'require_parameters': True}
    if tools is not None:
        payload['tools'] = tools
        payload['tool_choice'] = 'auto'
        payload['parallel_tool_calls'] = False
        payload['provider'] = {'require_parameters': True}

    data = _post_json(
        f"{config['openrouter_url']}/chat/completions",
        payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'HTTP-Referer': config.get('http_referer') or 'http://localhost:3000',
            'X-Title': config.get('app_title') or 'AI Dungeon Crawl',
        },
    )
    choices = data.get('choices') or []
    message = choices[0].get('message') if choices else None
    if tools is not None:
        if choices and choices[0].get('finish_reason') == 'length':
            raise LLMError('The game master response was truncated before it completed.')
        return _tool_response(message, 'openrouter', config['openrouter_model'])
    content = message.get('content', '') if isinstance(message, dict) else ''
    return {
        'provider': 'openrouter',
        'model': config['openrouter_model'],
        'response': content,
    }


def chat(messages, schema=None, schema_name=None, settings=None, config=None, temperature=None,
         tools=None):
    """Send a list of chat `messages` ({role, content}) to the configured
    provider. `schema` is an optional JSON schema used as the structured
    response_format. Returns {provider, model, response}. With native `tools`,
    also returns a normalized assistant `message` containing tool_calls."""
    config = config or get_llm_config(settings or {})
    if temperature is not None:
        config = {**config, 'temperature': temperature}
    if config['provider'] == 'openrouter':
        return call_openrouter(messages, schema, schema_name, config, tools=tools)
    return call_ollama(messages, schema, config, tools=tools)


def call_llm(prompt, schema, schema_name=None, settings=None, config=None):
    """Send a single-message `prompt` to the configured provider, requesting
    `schema` as the structured response_format, and return
    {provider, model, response}."""
    messages = [{'role': 'user', 'content': prompt.strip()}]
    return chat(messages, schema, schema_name, settings=settings, config=config)


_FENCE_RE = re.compile(r'```(?:json)?\s*([\s\S]*?)\s*```', re.IGNORECASE)


def try_parse_json_object(raw_text):
    """Best-effort JSON parsing of an LLM response, tolerating fenced code
    blocks or extra surrounding text. Returns None if parsing fails."""
    if not isinstance(raw_text, str):
        return None

    trimmed = raw_text.strip()
    if not trimmed:
        return None

    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        pass

    fence_match = _FENCE_RE.search(trimmed)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    first_brace = trimmed.find('{')
    last_brace = trimmed.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = trimmed[first_brace:last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    return None
