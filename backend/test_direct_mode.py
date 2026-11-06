import copy
import json
import unittest
from unittest.mock import Mock

from backend.direct_mode import process_direct_turn
from backend.game_engine import new_game
from backend.llm_calls.gameplay import DirectNarratorCall, HistorySummarizerCall


def direct_response(state, narration='You wait.'):
    location = state['world']['locations'][0]
    return {
        'narration': narration, 'action_succeeded': True, 'game_over': False,
        'currentLocationId': location['id'],
        'locations': [{
            'id': location['id'], 'name': location['name'],
            'description': location['description'], 'aliases': [],
            'visited': True, 'exits': [],
        }],
        'items': [], 'objects': [], 'npcs': [],
    }


class DirectModeTests(unittest.TestCase):
    def test_request_uses_summary_instead_of_the_literal_transcript(self):
        state = new_game(1)
        state['history']['recentEvents'].extend([
            {'kind': 'player_action', 'content': 'Look around.'},
            {'kind': 'narration', 'content': 'Dust and silence.'},
        ])
        state['history']['summary'] = 'Earlier: the player entered the square.'
        state['history']['summaryEventCount'] = 3
        original = copy.deepcopy(state)
        transport = Mock(return_value={
            'provider': 'fake', 'model': 'fake',
            'response': json.dumps(direct_response(state)),
        })
        summarizer = HistorySummarizerCall(transport=Mock())
        updated, result = process_direct_turn(
            state, 'Wait.', call=DirectNarratorCall(transport=transport),
            summarizer=summarizer)

        summarizer._transport.assert_not_called()
        messages, schema, name = transport.call_args[0]
        self.assertEqual(name, 'direct_narrator')
        self.assertIsNotNone(schema)
        request = json.loads(messages[-1]['content'].split('\n', 1)[1])
        self.assertNotIn('history', request)
        self.assertEqual(request['historical_summary'],
                         'Earlier: the player entered the square.')
        self.assertNotIn('Dust and silence.', json.dumps(request))
        self.assertEqual(state, original)
        self.assertEqual(result['narration'], 'You wait.')
        self.assertEqual(updated['history']['recentEvents'][-2:], [
            {'kind': 'player_action', 'content': 'Wait.'},
            {'kind': 'narration', 'content': 'You wait.'},
        ])

    def test_stale_summary_is_regenerated_before_narrating(self):
        state = new_game(1)
        state['history']['recentEvents'].extend([
            {'kind': 'player_action', 'content': 'Look around.'},
            {'kind': 'narration', 'content': 'Dust and silence.'},
        ])
        state['history']['summary'] = 'Earlier: the player entered the square.'
        state['history']['summaryEventCount'] = 1
        transport = Mock(return_value={
            'provider': 'fake', 'model': 'fake',
            'response': json.dumps(direct_response(state)),
        })
        summarizer = HistorySummarizerCall(transport=Mock(return_value={
            'provider': 'fake', 'model': 'fake',
            'response': json.dumps({'summary': 'The player looked around the square.'})}))
        process_direct_turn(state, 'Wait.', call=DirectNarratorCall(transport=transport),
                            summarizer=summarizer)
        request = json.loads(transport.call_args[0][0][-1]['content'].split('\n', 1)[1])
        self.assertEqual(request['historical_summary'],
                         'The player looked around the square.')
        self.assertNotIn('Dust and silence.', json.dumps(request))


if __name__ == '__main__':
    unittest.main()
