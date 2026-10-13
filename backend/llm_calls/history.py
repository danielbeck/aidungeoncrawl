"""Conversation history passed to `LLMCall.run`.

The only contract `LLMCall` relies on is a `to_messages()` method returning a
list of {role, content} dicts, so richer history types (summarised, windowed,
persisted, ...) can be added later without touching the calls themselves.
"""
from typing import Iterable, List, Optional

VALID_ROLES = ('user', 'assistant')


class MessageHistory:
    def __init__(self, messages: Optional[Iterable[dict]] = None):
        self._messages = []  # type: List[dict]
        for message in messages or []:
            self.add(message['role'], message['content'])

    def add(self, role: str, content: str) -> 'MessageHistory':
        if role not in VALID_ROLES:
            raise ValueError(f'role must be one of {VALID_ROLES}, got {role!r}.')
        self._messages.append({'role': role, 'content': content})
        return self

    def to_messages(self) -> List[dict]:
        return [dict(message) for message in self._messages]

    def __len__(self):
        return len(self._messages)
