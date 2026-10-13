"""Structured LLM calls used by the gameplay turn processor."""
try:
    from backend.llm_calls.base import LLMCall, LLMResponseError
except ImportError:
    from llm_calls.base import LLMCall, LLMResponseError


class InventoryManagerCall(LLMCall):
    name = 'inventory_manager'

    def format_user_input(self, user_input):
        return f'Assess the action in this game context:\n{user_input}'

    def validate_response(self, data):
        if not isinstance(data.get('assessment'), str) or not data['assessment'].strip():
            raise LLMResponseError('inventory_manager: assessment must be non-empty text.', details=data)
        return data


class MapManagerCall(LLMCall):
    name = 'map_manager'

    def format_user_input(self, user_input):
        return f'Assess movement and map implications in this game context:\n{user_input}'

    def validate_response(self, data):
        if not isinstance(data.get('assessment'), str) or not data['assessment'].strip():
            raise LLMResponseError('map_manager: assessment must be non-empty text.', details=data)
        return data


class NPCManagerCall(LLMCall):
    name = 'npc_manager'

    def format_user_input(self, user_input):
        return f'Assess NPC references and changes in this game context:\n{user_input}'

    def validate_response(self, data):
        if not isinstance(data.get('assessment'), str) or not data['assessment'].strip():
            raise LLMResponseError('npc_manager: assessment must be non-empty text.', details=data)
        return data


class NarratorCall(LLMCall):
    name = 'game_narrator'

    def format_user_input(self, user_input):
        return f'Resolve the player action and return narration and state proposals:\n{user_input}'

    def validate_response(self, data):
        if not isinstance(data.get('narration'), str) or not data['narration'].strip():
            raise LLMResponseError('game_narrator: narration must be non-empty text.', details=data)
        if not isinstance(data.get('state_changes'), list):
            raise LLMResponseError('game_narrator: state_changes must be a list.', details=data)
        operation_schema = self.response_format['properties']['state_changes']['items']
        payload_schema = operation_schema['properties']['payload']
        for index, operation in enumerate(data['state_changes'], 1):
            if not isinstance(operation, dict) or set(operation) != {'type', 'payload'}:
                raise LLMResponseError('game_narrator: each operation needs type and payload.', details=data)
            payload = operation['payload']
            if not isinstance(payload, dict):
                raise LLMResponseError('game_narrator: payload must be a JSON object, not a string.', details=data)
            contract = next((variant['properties']['payload']
                             for variant in operation_schema['anyOf']
                             if operation['type'] in variant['properties']['type']['enum']), None)
            if contract is None:
                raise LLMResponseError(
                    f'game_narrator: operation {index} has an unknown type.', details=data)
            missing = sorted(set(contract['required']) - set(payload))
            unexpected = sorted(set(payload) - set(contract['properties']))
            if missing or unexpected:
                raise LLMResponseError(
                    f"game_narrator: operation {index} ({operation['type']}) payload "
                    f"missing required fields {missing}; unexpected fields {unexpected}.",
                    details=data)
            if 'anyOf' in contract and not any(
                    all(key in payload for key in option['required'])
                    for option in contract['anyOf']):
                raise LLMResponseError(
                    f'game_narrator: operation {index} needs exit_id or destination_location_id.',
                    details=data)
            for key, value in payload.items():
                field_schema = payload_schema['properties'].get(key)
                if field_schema is None:
                    raise LLMResponseError(f'game_narrator: unknown payload field {key}.', details=data)
                allowed = field_schema['type']
                allowed = allowed if isinstance(allowed, list) else [allowed]
                actual = (
                    'null' if value is None else
                    'boolean' if isinstance(value, bool) else
                    'string' if isinstance(value, str) else
                    'object' if isinstance(value, dict) else
                    'array' if isinstance(value, list) else 'number'
                )
                if actual not in allowed:
                    raise LLMResponseError(
                        f"game_narrator: operation {index} ({operation['type']}) "
                        f"field {key} must have type {allowed}, got {actual}.",
                        details=data)
                if 'enum' in field_schema and value not in field_schema['enum']:
                    raise LLMResponseError(f'game_narrator: invalid value for payload field {key}.', details=data)
                constraint = contract['properties'][key]
                if ('enum' in constraint and value not in constraint['enum']) or (
                        constraint.get('type') == 'string' and not isinstance(value, str)):
                    raise LLMResponseError(
                        f"game_narrator: operation {index} ({operation['type']}) "
                        f"has an invalid value for {key}.", details=data)
                if key == 'aliases' and any(not isinstance(alias, str) for alias in value):
                    raise LLMResponseError('game_narrator: aliases must contain strings.', details=data)
        if not isinstance(data.get('action_succeeded'), bool) or not isinstance(data.get('game_over'), bool):
            raise LLMResponseError('game_narrator: action_succeeded and game_over must be booleans.', details=data)
        return data


class SummarizerCall(LLMCall):
    name = 'game_summarizer'

    def format_user_input(self, user_input):
        return f'Update the narrative summary from this history:\n{user_input}'

    def validate_response(self, data):
        if not isinstance(data.get('summary'), str) or not data['summary'].strip():
            raise LLMResponseError('game_summarizer: summary must be non-empty text.', details=data)
        return data
