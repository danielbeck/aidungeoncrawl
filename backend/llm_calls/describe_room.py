"""Example LLM call: turn a short room idea into a structured dungeon room.

Prompt: shared/llm/describe_room.md
Schema: shared/llm/describe_room.json
"""
try:
    from backend.llm_calls.base import LLMCall, LLMResponseError
except ImportError:
    from llm_calls.base import LLMCall, LLMResponseError


class DescribeRoomCall(LLMCall):
    name = 'describe_room'

    def format_user_input(self, user_input):
        return f'Room idea: {user_input.strip()}'

    def validate_response(self, data):
        allowed = self.response_format['properties']['exits']['items']['enum']
        exits = data['exits']
        if not isinstance(exits, list) or any(exit_ not in allowed for exit_ in exits):
            raise LLMResponseError(f'{self.name}: exits must be a list of {allowed}.', details=data)
        return data
