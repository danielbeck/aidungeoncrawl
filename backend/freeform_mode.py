"""Plain story continuation without reading or updating tracked world state."""
import copy
import json

try:
    from backend.game_engine import _required_string, current_summary
    from backend.harness.llm_provider import LLMError
    from backend.llm_calls.gameplay import FreeformNarratorCall, HistorySummarizerCall
except ImportError:
    from game_engine import _required_string, current_summary
    from harness.llm_provider import LLMError
    from llm_calls.gameplay import FreeformNarratorCall, HistorySummarizerCall


def process_freeform_turn(state, action, settings=None, on_progress=None, call=None,
                          summarizer=None):
    action = _required_string(action, 'action', 2000)
    call = call or FreeformNarratorCall(settings=settings)
    summarizer = summarizer or HistorySummarizerCall(settings=settings)

    def progress(status, **details):
        if on_progress is not None:
            on_progress({'component': 'freeform_narrator', 'status': status, **details})

    def summary_progress(component, status, **details):
        if on_progress is not None:
            on_progress({'component': component, 'status': status, **details})

    request = {
        'current_action': action,
        'historical_summary': current_summary(state, summarizer, summary_progress),
    }
    completed = state['history'].get('recentEvents', [])
    if not request['historical_summary'] and len(completed) == 1:
        request['historical_summary'] = completed[0]['content']
    progress('started', request=request)
    try:
        result = call.run(json.dumps(request, ensure_ascii=False))
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
