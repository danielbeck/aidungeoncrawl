"""Typed LLM calls. To add one:

1. Write the prompt in `shared/llm/<name>.md` and, for structured output,
   the JSON schema in `shared/llm/<name>.json`.
2. Subclass `LLMCall` with `name = '<name>'` in its own module (override
   `format_user_input` / `validate_response` as needed).
3. Add it to `REGISTRY` below, which exposes it at `POST /llm/calls/<name>`.

The frontend imports schemas via `@shared/llm/<name>.json`.

Usage:
    room = DescribeRoomCall(settings=app.config['llm_settings']).run('a flooded crypt')
    room.data['title']
"""
try:
    from backend.llm_calls.base import LLMCall, LLMResponseError, LLMResult
    from backend.llm_calls.describe_room import DescribeRoomCall
    from backend.llm_calls.gameplay import (
        InventoryManagerCall, MapManagerCall, NPCManagerCall, NarratorCall, SummarizerCall,
    )
    from backend.llm_calls.history import MessageHistory
except ImportError:
    from llm_calls.base import LLMCall, LLMResponseError, LLMResult
    from llm_calls.describe_room import DescribeRoomCall
    from llm_calls.gameplay import (
        InventoryManagerCall, MapManagerCall, NPCManagerCall, NarratorCall, SummarizerCall,
    )
    from llm_calls.history import MessageHistory

REGISTRY = {call.name: call for call in (
    DescribeRoomCall, InventoryManagerCall, MapManagerCall, NPCManagerCall,
    NarratorCall, SummarizerCall,
)}

__all__ = [
    'LLMCall', 'LLMResponseError', 'LLMResult', 'DescribeRoomCall', 'MessageHistory',
    'InventoryManagerCall', 'MapManagerCall', 'NPCManagerCall', 'NarratorCall',
    'SummarizerCall', 'REGISTRY',
]
