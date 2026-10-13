"""Base class for defining typed, testable LLM calls.

Each kind of LLM call is a subclass of `LLMCall` that declares a `name`. Its
prompt and schema live next to each other as plain files in the top-level
`shared/llm/` folder, so they can be edited without touching Python and are
shared with the frontend:

* `shared/llm/<name>.md`    - required; the hardcoded system prompt
* `shared/llm/<name>.json`  - optional JSON schema (`response_format`); when
                              present, it is sent to the provider as a
                              structured-output constraint, appended to the
                              system prompt (see `include_schema_in_prompt`),
                              and the response is parsed and validated as
                              JSON. Otherwise raw text is returned

Subclasses may override `format_user_input` to template the user's input and
`validate_response` to add semantic checks beyond the schema.

The network layer is injected as a `transport` callable, so tests can swap in
a fake without patching modules:

    transport(messages, schema, schema_name) -> {provider, model, response}
"""
import json
import logging
import os
import time
import uuid
from abc import ABC
from typing import Any, Callable, Dict, List, NamedTuple, Optional

try:
    from backend.llm_provider import LLMError, chat, try_parse_json_object
except ImportError:
    from llm_provider import LLMError, chat, try_parse_json_object

# <repo>/shared/llm locally; <REMOTE_DIR>/shared/llm when deployed.
DEFINITIONS_DIR = os.environ.get('LLM_DEFINITIONS_DIR') or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'shared', 'llm'))

Message = Dict[str, str]
Transport = Callable[[List[Message], Optional[dict], Optional[str]], dict]
logger = logging.getLogger(__name__)
MAX_RESPONSE_LOG_CHARS = 32768


class LLMResponseError(LLMError):
    """Raised when the model's response cannot be parsed or fails validation."""


class LLMResult(NamedTuple):
    data: Any
    raw: str
    provider: str
    model: str


def default_transport(settings: Optional[dict] = None) -> Transport:
    """Build a transport that sends requests to the configured provider."""
    def transport(messages, schema, schema_name):
        return chat(messages, schema, schema_name, settings=settings)
    return transport


class LLMCall(ABC):
    name = None  # type: str
    definitions_dir = DEFINITIONS_DIR
    include_schema_in_prompt = True

    def __init__(self, transport: Optional[Transport] = None, settings: Optional[dict] = None):
        if not self.name:
            raise TypeError(f'{type(self).__name__} must define `name`.')
        self.system_prompt, self.response_format = self.load_definition()
        self._transport = transport or default_transport(settings)

    @classmethod
    def load_definition(cls):
        """Read and cache `(system_prompt, response_format)` from
        `<definitions_dir>/<name>.md` and the optional `<name>.json`."""
        cache_key = (cls.definitions_dir, cls.name)
        cached = cls.__dict__.get('_definition_cache')
        if cached and cached[0] == cache_key:
            return cached[1]

        base_path = os.path.join(cls.definitions_dir, cls.name)
        try:
            with open(f'{base_path}.md', encoding='utf-8') as prompt_file:
                system_prompt = prompt_file.read().strip()
        except FileNotFoundError:
            raise TypeError(f'{cls.__name__}: missing prompt file {base_path}.md') from None
        if not system_prompt:
            raise TypeError(f'{cls.__name__}: prompt file {base_path}.md is empty.')

        response_format = None
        if os.path.exists(f'{base_path}.json'):
            with open(f'{base_path}.json', encoding='utf-8') as schema_file:
                response_format = json.load(schema_file)
            if not isinstance(response_format, dict):
                raise TypeError(f'{cls.__name__}: {base_path}.json must contain a JSON object.')

        definition = (system_prompt, response_format)
        cls._definition_cache = (cache_key, definition)
        return definition

    def build_system_prompt(self) -> str:
        """The hardcoded prompt, plus the JSON schema when `response_format`
        is set and `include_schema_in_prompt` is True. Providers enforce the
        schema separately, but the model writes better field content when it
        can read the schema (including any `description`s)."""
        prompt = self.system_prompt
        if self.response_format is None or not self.include_schema_in_prompt:
            return prompt
        schema = json.dumps(self.response_format, indent=2)
        return f'{prompt}\n\nRespond only with a JSON object matching this JSON schema:\n{schema}'

    def format_user_input(self, user_input: str) -> str:
        return user_input.strip()

    def build_messages(self, user_input: str, history=None) -> List[Message]:
        """`history` is any object exposing `to_messages()` that returns prior
        {role, content} messages (see `llm_calls.history`)."""
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValueError('user_input must be a non-empty string.')

        messages = [{'role': 'system', 'content': self.build_system_prompt()}]
        if history is not None:
            messages.extend(history.to_messages())
        messages.append({'role': 'user', 'content': self.format_user_input(user_input)})
        return messages

    def parse_response(self, raw: str) -> Any:
        if self.response_format is None:
            return raw.strip()

        data = try_parse_json_object(raw)
        if not isinstance(data, dict):
            raise LLMResponseError(f'{self.name}: response was not a JSON object.', details=raw)

        missing = [key for key in self.response_format.get('required', []) if key not in data]
        if missing:
            raise LLMResponseError(
                f"{self.name}: response is missing required fields: {', '.join(missing)}.",
                details=raw,
            )
        return data

    def validate_response(self, data: Any) -> Any:
        """Hook for call-specific checks/normalisation. Return the final data
        or raise `LLMResponseError`."""
        return data

    def run(self, user_input: str, history=None) -> LLMResult:
        messages = self.build_messages(user_input, history)
        call_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        logger.info('LLM call %s [%s] started', self.name, call_id)
        try:
            result = self._transport(messages, self.response_format, self.name)
            raw = result.get('response') or ''
            preview = raw[:MAX_RESPONSE_LOG_CHARS]
            if len(raw) > MAX_RESPONSE_LOG_CHARS:
                preview += f' ... [truncated; {len(raw)} characters total]'
            logger.info(
                'LLM call %s [%s] response after %.3fs from %s/%s: %r',
                self.name, call_id, time.monotonic() - started,
                result.get('provider'), result.get('model'), preview,
            )
            data = self.validate_response(self.parse_response(raw))
        except (LLMError, ValueError, TypeError, KeyError) as error:
            logger.exception(
                'LLM call %s [%s] failed after %.3fs: %s',
                self.name, call_id, time.monotonic() - started, error,
            )
            raise
        return LLMResult(data=data, raw=raw, provider=result.get('provider'), model=result.get('model'))
