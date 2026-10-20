from flask import Blueprint, current_app, jsonify, request

try:
    from backend.llm_calls import REGISTRY, LLMResponseError, MessageHistory
    from backend.harness.llm_provider import LLMError, call_llm, get_llm_config
except ImportError:
    from llm_calls import REGISTRY, LLMResponseError, MessageHistory
    from harness.llm_provider import LLMError, call_llm, get_llm_config

bp = Blueprint('llm', __name__, url_prefix='/llm')


MAX_INPUT_LENGTH = 2000
MAX_HISTORY_MESSAGES = 50


def get_llm_settings():
    return current_app.config['llm_settings']


def llm_error_response(error, label):
    current_app.logger.error(f'{label} failed: {error}')
    if isinstance(error, LLMResponseError):
        return jsonify({'error': str(error)}), 502
    status_code = 500 if error.code == 'MISSING_OPENROUTER_API_KEY' else 502
    return jsonify({'error': str(error), 'details': error.details}), status_code


def parse_history(raw_history):
    """Build a MessageHistory from a request's optional `history` list, or
    raise ValueError with a client-facing message."""
    if raw_history is None:
        return None
    if not isinstance(raw_history, list) or len(raw_history) > MAX_HISTORY_MESSAGES:
        raise ValueError(f'history must be a list of at most {MAX_HISTORY_MESSAGES} messages.')
    for message in raw_history:
        if not isinstance(message, dict) or not isinstance(message.get('content'), str):
            raise ValueError('Each history message needs a role and string content.')
    try:
        return MessageHistory(raw_history)
    except (KeyError, ValueError) as error:
        raise ValueError(f'Invalid history: {error}') from error


@bp.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
    except Exception:
        return jsonify({'error': 'Invalid JSON data'}), 400

    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON request body is required.'}), 400

    prompt = data.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({'error': 'A non-empty prompt is required.'}), 400

    response_format = data.get('response_format')
    if not isinstance(response_format, dict):
        return jsonify({'error': 'A response_format JSON schema object is required.'}), 400

    schema_name = data.get('schema_name')
    if schema_name is not None and not isinstance(schema_name, str):
        return jsonify({'error': 'schema_name must be a string.'}), 400

    try:
        result = call_llm(prompt, response_format, schema_name, settings=get_llm_settings())
    except LLMError as error:
        return llm_error_response(error, 'LLM chat request')

    return jsonify(result)


@bp.route('/calls/<name>', methods=['POST'])
def run_call(name):
    """Run a registered LLMCall: {input, history?} -> {data, provider, model}."""
    call_class = REGISTRY.get(name)
    if call_class is None:
        return jsonify({'error': f'Unknown LLM call: {name}'}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON request body is required.'}), 400

    user_input = data.get('input')
    if not isinstance(user_input, str) or not user_input.strip():
        return jsonify({'error': 'A non-empty input is required.'}), 400
    if len(user_input) > MAX_INPUT_LENGTH:
        return jsonify({'error': f'input must be at most {MAX_INPUT_LENGTH} characters.'}), 400

    try:
        history = parse_history(data.get('history'))
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    try:
        result = call_class(settings=get_llm_settings()).run(user_input, history=history)
    except LLMError as error:
        return llm_error_response(error, f'LLM call {name!r}')

    return jsonify({'data': result.data, 'provider': result.provider, 'model': result.model})


@bp.route('/health', methods=['GET'])
def health():
    config = get_llm_config(get_llm_settings())
    is_openrouter = config['provider'] == 'openrouter'
    return jsonify({
        'status': 'ok',
        'provider': config['provider'],
        'model': config['openrouter_model'] if is_openrouter else config['ollama_model'],
        'apiUrl': config['openrouter_url'] if is_openrouter else config['ollama_url'],
        'apiKeyConfigured': bool(config['openrouter_api_key']),
    })
