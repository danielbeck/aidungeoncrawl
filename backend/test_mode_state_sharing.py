import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.direct_mode import _apply, _project
from backend.game_engine import _context, new_game
from backend.tool_mode import GameMasterCall, WorldTools


def rich_game():
    """One adventure with connected locations, an NPC-held item, an object."""
    state = new_game(1)
    origin = state['world']['locations'][0]
    tavern = {'id': 'tavern', 'name': 'Tavern', 'description': 'A tavern.',
              'aliases': ['the tavern'], 'visited': True,
              'exits': [{'id': 'exit-square', 'description': 'To the square',
                         'destinationId': origin['id'], 'traversable': True,
                         'identified': True}]}
    origin['exits'].append({'id': 'exit-tavern', 'description': 'To the tavern',
                            'destinationId': tavern['id'], 'traversable': False,
                            'identified': True})
    state['world']['locations'].append(tavern)
    state['world']['npcs'].append({
        'id': 'mira', 'name': 'Mira', 'description': 'A keeper.', 'aliases': [],
        'currentLocationId': 'tavern', 'status': 'active', 'state': {'mood': 'wary'},
    })
    state['world']['items'].append({
        'id': 'key', 'name': 'Iron key', 'description': 'A key.', 'aliases': ['key'],
        'knownByPlayer': True, 'holder': {'type': 'npc', 'id': 'mira'},
        'status': 'available',
    })
    state['world']['objects'].append({
        'id': 'bar', 'name': 'Bar', 'description': 'A long bar.', 'aliases': [],
        'locationId': 'tavern', 'knownByPlayer': True, 'containedInObjectId': None,
        'status': 'present',
    })
    return state


def tool_call(tool_name, **arguments):
    return {'id': tool_name, 'type': 'function',
            'function': {'name': tool_name, 'arguments': arguments}}


def tool_world(state):
    master = GameMasterCall(transport=Mock())
    comparator = Mock()
    comparator.run.return_value = SimpleNamespace(
        data={'outcome': 'new'}, provider='fake', model='fake')
    return WorldTools(state, master.tools, comparator, lambda *args, **data: None)


class SharedWorldStateTests(unittest.TestCase):
    def test_direct_projection_round_trip_loses_nothing(self):
        """What direct mode sees and writes back is the same world as managed."""
        state = rich_game()
        before = copy.deepcopy(state['world'])
        self.assertEqual(_apply(state, _project(state)), [])
        self.assertEqual(state['world'], before)

    def test_tool_mode_changes_are_visible_to_managed_and_direct(self):
        state = new_game(1)
        origin = state['currentLocationId']
        tools = tool_world(state)
        tavern = tools.execute(tool_call('move_to_new_location', name='Tavern',
                                        description='A tavern.'))['entity']
        key = tools.execute(tool_call('create_item', name='Key', description='Iron.',
                                     location_id=tavern['id']))['entity']
        tools.execute(tool_call('pick_up_item', item_id=key['id']))
        npc = tools.execute(tool_call('create_npc', name='Mira', description='A keeper.',
                                      location_id=tavern['id']))['entity']

        # Managed mode reads the same world.
        managed = _context(state)
        self.assertEqual(managed['current_location']['name'], 'Tavern')
        self.assertEqual([item['name'] for item in managed['carried_items']], ['Key'])
        self.assertEqual([person['name'] for person in managed['npcs_here']], ['Mira'])

        # Direct mode is handed the same entities, with their holders and locations.
        projected = _project(state)
        self.assertEqual([location['id'] for location in projected['locations']],
                         [origin, tavern['id']])
        self.assertEqual(projected['currentLocationId'], tavern['id'])
        self.assertEqual(projected['items'][0]['holder'], {'type': 'player', 'id': '1'})
        self.assertEqual(projected['npcs'][0]['id'], npc['id'])
        self.assertEqual(projected['npcs'][0]['currentLocationId'], tavern['id'])

    def test_direct_turn_echoes_the_same_world_back_to_managed(self):
        state = new_game(1)
        tools = tool_world(state)
        tavern = tools.execute(tool_call('move_to_new_location', name='Tavern',
                                        description='A tavern.'))['entity']
        key = tools.execute(tool_call('create_item', name='Key', description='Iron.',
                                     location_id=tavern['id']))['entity']
        tools.execute(tool_call('pick_up_item', item_id=key['id']))
        tools.execute(tool_call('create_npc', name='Mira', description='A keeper.',
                                location_id=tavern['id']))
        before = copy.deepcopy(state['world'])

        payload = _project(state)
        payload.update({'narration': 'You wait.', 'action_succeeded': True,
                        'game_over': False})
        self.assertEqual(_apply(state, payload), [])
        self.assertEqual(state['world'], before)
        self.assertEqual([item['name'] for item in _context(state)['carried_items']],
                         ['Key'])
        self.assertEqual([npc['name'] for npc in _context(state)['npcs_here']],
                         ['Mira'])

    def test_npc_records_keep_fields_only_their_own_mode_sets(self):
        """NPC bookkeeping written by managed mode survives a direct turn."""
        state = rich_game()
        _apply(state, _project(state))
        npc = state['world']['npcs'][0]
        self.assertEqual(npc['state'], {'mood': 'wary'})
        self.assertEqual(npc['aliases'], [])
        self.assertEqual(npc['status'], 'active')


if __name__ == '__main__':
    unittest.main()
