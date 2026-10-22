"""Plain story continuation without reading or updating tracked world state."""
import copy

try:
    from backend.game_engine import _required_string
    from backend.harness.history import MessageHistory
    from backend.harness.llm_provider import LLMError
    from backend.llm_calls.gameplay import FreeformNarratorCall
except ImportError:
    from game_engine import _required_string
    from harness.history import MessageHistory
    from harness.llm_provider import LLMError
    from llm_calls.gameplay import FreeformNarratorCall


def process_freeform_turn(state, action, settings=None, on_progress=None, call=None):
    action = _required_string(action, 'action', 2000)
    call = call or FreeformNarratorCall(settings=settings)
    history = MessageHistory([
        {'role': 'user' if event['kind'] == 'player_action' else 'assistant',
         'content': event['content']}
        for event in state['history']['recentEvents']
    ])

    def progress(status, **details):
        if on_progress is not None:
            on_progress({'component': 'freeform_narrator', 'status': status, **details})

    progress('started', request={'current_action': action, 'history': history.to_messages()})
    try:
        result = call.run(action, history=history)
    except LLMError as error:
        progress('failed', error=str(error), response=error.details)
        raise
    candidate = copy.deepcopy(state)
    candidate['history']['recentEvents'].extend([
        {'kind': 'player_action', 'content': action},
        {'kind': 'narration', 'content': result.data},
    ])
    progress('completed', response={'narration': result.data},
             provider=result.provider, model=result.model)
    return candidate, {
        'narration': result.data, 'provider': result.provider, 'model': result.model,
    }
