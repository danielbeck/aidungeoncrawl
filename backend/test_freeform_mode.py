import copy
import json
import unittest
from unittest.mock import Mock

from backend.freeform_mode import process_freeform_turn
from backend.game_engine import new_game
from backend.harness.llm_calls import LLMResponseError
from backend.llm_calls.gameplay import FreeformNarratorCall


class FreeformModeTests(unittest.TestCase):
    def test_one_plain_text_call_preserves_tracked_state(self):
        state = new_game(1)
        state['history']['summary'] = 'Old summary'
        state['history']['summaryEventCount'] = 1
        state['history']['lastMovement'] = {
            'fromLocationId': 'elsewhere', 'toLocationId': state['currentLocationId'],
        }
        original = copy.deepcopy(state)
        transport = Mock(return_value={
            'provider': 'fake', 'model': 'fake', 'response': 'You enter the tavern.',
        })
        events = []
        updated, result = process_freeform_turn(
            state, 'Enter the tavern.', call=FreeformNarratorCall(transport=transport),
            on_progress=events.append)
        transport.assert_called_once()
        messages, schema, name = transport.call_args[0]
        self.assertIsNone(schema)
        self.assertEqual(name, 'freeform_narrator')
        self.assertEqual([message['role'] for message in messages], ['system', 'user'])
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['current_action'], 'Enter the tavern.')
        self.assertEqual(payload['historical_summary'], 'Old summary')
        self.assertNotIn(state['history']['recentEvents'][0]['content'],
                         json.dumps(messages))
        self.assertEqual(state, original)
        expected = copy.deepcopy(original)
        expected['history']['recentEvents'].extend([
            {'kind': 'player_action', 'content': 'Enter the tavern.'},
            {'kind': 'narration', 'content': 'You enter the tavern.'},
        ])
        self.assertEqual(updated, expected)
        self.assertEqual(result['narration'], 'You enter the tavern.')
        self.assertEqual([event['status'] for event in events], ['started', 'completed'])

    def test_empty_response_fails_without_mutating_state(self):
        state = new_game(1)
        original = copy.deepcopy(state)
        transport = Mock(return_value={
            'provider': 'fake', 'model': 'fake', 'response': '   ',
        })
        events = []
        with self.assertRaises(LLMResponseError):
            process_freeform_turn(
                state, 'Wait.', call=FreeformNarratorCall(transport=transport),
                on_progress=events.append)
        self.assertEqual(state, original)
        self.assertEqual(events[-1]['status'], 'failed')
