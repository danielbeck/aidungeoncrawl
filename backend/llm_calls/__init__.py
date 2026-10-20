"""Typed LLM calls. To add one:

1. Write the prompt in `shared/llm/<name>.md` and, for structured output,
   the JSON schema in `shared/llm/<name>.json`.
2. Subclass `LLMCall` with `name = '<name>'` in its own module (override
   `format_user_input` / `validate_response` as needed).
3. Add it to `REGISTRY` below, which exposes it at `POST /llm/calls/<name>`.

The frontend imports schemas via `@shared/llm/<name>.json`.

Usage:
    result = NarratorCall(settings=app.config['llm_settings']).run('The player opens a door')
    result.data['narration']
"""
try:
    from backend.harness.llm_calls import LLMCall, LLMResponseError, LLMResult
    from backend.llm_calls.gameplay import (
        ComparatorCall, InventoryManagerCall, MapManagerCall, NPCManagerCall, NarratorCall,
        HistorySummarizerCall, MovementResolverCall,
    )
    from backend.harness.history import MessageHistory
except ImportError:
    from harness.llm_calls import LLMCall, LLMResponseError, LLMResult
    from llm_calls.gameplay import (
        ComparatorCall, InventoryManagerCall, MapManagerCall, NPCManagerCall, NarratorCall,
        HistorySummarizerCall, MovementResolverCall,
    )
    from harness.history import MessageHistory

REGISTRY = {call.name: call for call in (
    ComparatorCall, InventoryManagerCall, MapManagerCall, NPCManagerCall, NarratorCall,
    HistorySummarizerCall, MovementResolverCall,
)}

__all__ = [
    'LLMCall', 'LLMResponseError', 'LLMResult', 'MessageHistory', 'ComparatorCall',
    'InventoryManagerCall', 'MapManagerCall', 'NPCManagerCall', 'NarratorCall',
    'HistorySummarizerCall', 'MovementResolverCall', 'REGISTRY',
]
