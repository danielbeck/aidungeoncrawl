import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from backend.harness import llm_provider
from backend.endpoints.llm import bp as llm_bp
from backend.llm_calls import (
    REGISTRY, InventoryManagerCall, LLMCall, LLMResponseError, MapManagerCall,
    MessageHistory, NPCManagerCall, MovementResolverCall,
)

TEST_DEFINITIONS_DIR = tempfile.mkdtemp()
SCHEMA = {'type': 'object', 'properties': {'x': {'type': 'string'}}}


def write_definition(name, prompt=None, schema=None, input_template=None):
    if prompt is not None:
        with open(os.path.join(TEST_DEFINITIONS_DIR, f'{name}.md'), 'w') as prompt_file:
            prompt_file.write(prompt)
    if schema is not None:
        with open(os.path.join(TEST_DEFINITIONS_DIR, f'{name}.json'), 'w') as schema_file:
            json.dump(schema, schema_file)
    if input_template is not None:
        with open(os.path.join(TEST_DEFINITIONS_DIR, f'{name}.input.md'), 'w') as input_file:
            input_file.write(input_template)


def setUpModule():
    write_definition('echo', '\nRepeat the input.\n')
    write_definition('schema_call', '  Do the thing.  ', SCHEMA)
    write_definition('empty', '   ')
    write_definition('bad_schema', 'Prompt', ['not', 'an', 'object'])
    write_definition('templated', 'Prompt', input_template='Input: {{input}}')
    write_definition('bad_input_template', 'Prompt', input_template='Input: {input}')


def tearDownModule():
    shutil.rmtree(TEST_DEFINITIONS_DIR)


class TestCall(LLMCall):
    definitions_dir = TEST_DEFINITIONS_DIR


class EchoCall(TestCall):
    name = 'echo'


class SchemaCall(TestCall):
    name = 'schema_call'


class TemplatedCall(TestCall):
    name = 'templated'


class FakeTransport:
    """Records requests and replies with a canned response."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, messages, schema, schema_name):
        self.calls.append({'messages': messages, 'schema': schema, 'schema_name': schema_name})
        return {'provider': 'fake', 'model': 'fake-model', 'response': self.response}


class LLMCallTests(unittest.TestCase):
    def test_new_instances_pick_up_definition_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            call_class = type('EditableCall', (LLMCall,), {
                'name': 'editable', 'definitions_dir': directory,
            })
            prompt_path = os.path.join(directory, 'editable.md')
            with open(prompt_path, 'w') as prompt:
                prompt.write('Original prompt.')
            first = call_class(transport=FakeTransport('ok'))
            with open(prompt_path, 'w') as prompt:
                prompt.write('Updated prompt.')
            with open(os.path.join(directory, 'editable.json'), 'w') as schema:
                json.dump(SCHEMA, schema)
            with open(os.path.join(directory, 'editable.input.md'), 'w') as template:
                template.write('Updated input: {{input}}')
            second = call_class(transport=FakeTransport('ok'))
            self.assertEqual(first.system_prompt, 'Original prompt.')
            self.assertEqual(second.system_prompt, 'Updated prompt.')
            self.assertEqual(second.response_format, SCHEMA)
            self.assertEqual(second.format_user_input('action'), 'Updated input: action')

    def test_requires_name(self):
        with self.assertRaisesRegex(TypeError, 'name'):
            TestCall(transport=FakeTransport(''))

    def test_requires_non_empty_prompt_file(self):
        for name, message in (('missing', 'missing prompt file'), ('empty', 'is empty')):
            call_class = type('Call', (TestCall,), {'name': name})
            with self.subTest(name=name), self.assertRaisesRegex(TypeError, message):
                call_class(transport=FakeTransport(''))

    def test_schema_file_must_be_an_object(self):
        call_class = type('Call', (TestCall,), {'name': 'bad_schema'})
        with self.assertRaisesRegex(TypeError, 'JSON object'):
            call_class(transport=FakeTransport(''))

    def test_input_template_must_contain_one_input_placeholder(self):
        call_class = type('Call', (TestCall,), {'name': 'bad_input_template'})
        with self.assertRaisesRegex(TypeError, 'exactly one'):
            call_class(transport=FakeTransport(''))

    def test_loads_prompt_and_optional_schema_from_files(self):
        echo = EchoCall(transport=FakeTransport(''))
        self.assertEqual(echo.system_prompt, 'Repeat the input.')
        self.assertIsNone(echo.response_format)
        self.assertEqual(SchemaCall(transport=FakeTransport('')).response_format, SCHEMA)

    def test_every_registered_call_has_valid_definition_files(self):
        for name, call_class in REGISTRY.items():
            with self.subTest(name=name):
                self.assertEqual(call_class.name, name)
                self.assertIsNotNone(call_class(transport=FakeTransport('')).user_input_template)

    def test_user_input_template_preserves_input(self):
        messages = TemplatedCall(transport=FakeTransport('')).build_messages('  hello  ')

        self.assertEqual(messages[-1], {'role': 'user', 'content': 'Input:   hello  '})

    def test_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            EchoCall(transport=FakeTransport('')).run('   ')

    def test_text_call_without_response_format_returns_text(self):
        transport = FakeTransport('  hello  ')
        result = EchoCall(transport=transport).run('hello')

        self.assertEqual(result.data, 'hello')
        self.assertEqual(result.provider, 'fake')
        self.assertIsNone(transport.calls[0]['schema'])

    def test_logs_start_and_raw_response_before_validation_failure(self):
        call = REGISTRY['comparator'](transport=FakeTransport('not valid JSON'))
        with self.assertLogs('backend.harness.llm_calls', level='INFO') as logs:
            with self.assertRaises(LLMResponseError):
                call.run('an identical item')
        self.assertIn('started', logs.output[0])
        self.assertIn('comparator', logs.output[0])
        self.assertIn('response', logs.output[1])
        self.assertIn('not valid JSON', logs.output[1])
        self.assertIn('fake-model', logs.output[1])
        self.assertIn('failed', logs.output[2])

    def test_logs_transport_failure_and_preserves_exception(self):
        error = llm_provider.LLMError('provider unavailable')

        def transport(messages, schema, schema_name):
            raise error

        with self.assertLogs('backend.harness.llm_calls', level='INFO') as logs:
            with self.assertRaises(llm_provider.LLMError) as raised:
                EchoCall(transport=transport).run('hello')
        self.assertIs(raised.exception, error)
        self.assertIn('started', logs.output[0])
        self.assertIn('failed', logs.output[1])
        self.assertIn('provider unavailable', logs.output[1])

    def test_success_logs_share_a_call_id_and_mark_truncated_responses(self):
        raw = 'x' * 32769
        with self.assertLogs('backend.harness.llm_calls', level='INFO') as logs:
            result = EchoCall(transport=FakeTransport(raw)).run('hello')
        self.assertEqual(result.raw, raw)
        self.assertEqual(result.data, raw)
        self.assertEqual(len(logs.records), 2)
        self.assertEqual(logs.records[0].args[1], logs.records[1].args[1])
        self.assertIn('[truncated; 32769 characters total]', logs.output[1])

    def test_history_is_inserted_between_system_prompt_and_user_input(self):
        transport = FakeTransport('ok')
        history = MessageHistory().add('user', 'earlier question').add('assistant', 'earlier answer')

        EchoCall(transport=transport).run('new question', history=history)

        self.assertEqual(transport.calls[0]['messages'], [
            {'role': 'system', 'content': 'Repeat the input.'},
            {'role': 'user', 'content': 'earlier question'},
            {'role': 'assistant', 'content': 'earlier answer'},
            {'role': 'user', 'content': 'new question'},
        ])

    def test_text_call_system_prompt_has_no_schema(self):
        self.assertEqual(EchoCall(transport=FakeTransport('')).build_system_prompt(), 'Repeat the input.')

    def test_schema_is_appended_to_system_prompt(self):
        prompt = SchemaCall(transport=FakeTransport('')).build_system_prompt()

        self.assertTrue(prompt.startswith('Do the thing.\n\n'))
        schema_text = prompt[prompt.index('{'):]
        self.assertEqual(json.loads(schema_text), SCHEMA)

    def test_schema_in_prompt_can_be_disabled(self):
        class QuietSchemaCall(SchemaCall):
            include_schema_in_prompt = False

        self.assertEqual(QuietSchemaCall(transport=FakeTransport('')).build_system_prompt(), 'Do the thing.')

    def test_movement_calls_use_deterministic_sampling_without_changing_other_calls(self):
        with patch('backend.harness.llm_calls.chat', return_value={
                'provider': 'test', 'model': 'test', 'response': '{"movement":false,"target":null}',
        }) as chat:
            MapManagerCall().run('Wait.')
            self.assertEqual(chat.call_args[1]['temperature'], 0)
        with patch('backend.harness.llm_calls.chat', return_value={
                'provider': 'test', 'model': 'test',
                'response': '{"destination":null,"clarification":"Which place?"}',
        }) as chat:
            MovementResolverCall().run('Go there.')
            self.assertEqual(chat.call_args[1]['temperature'], 0)
        with patch('backend.harness.llm_calls.chat', return_value={
                'provider': 'test', 'model': 'test', 'response': 'Hello',
        }) as chat:
            EchoCall().run('Hello')
            self.assertNotIn('temperature', chat.call_args[1])


class GameplayManagerResponseTests(unittest.TestCase):
    def test_inventory_schema_requires_recipient_only_for_give(self):
        call = InventoryManagerCall(transport=FakeTransport(''))
        branches = call.response_format['properties']['items']['items']['anyOf']
        self.assertEqual(len(branches), 2)
        for interaction in ('take', 'drop', 'consume', 'break', 'give'):
            with self.subTest(interaction=interaction):
                matches = [branch for branch in branches
                           if interaction in branch['properties']['interaction']['enum']]
                self.assertEqual(len(matches), 1)
                branch = matches[0]
                expected = {'name', 'description', 'interaction'}
                if interaction == 'give':
                    expected.add('recipient')
                self.assertEqual(set(branch['required']), expected)
                self.assertEqual(set(branch['properties']), expected)
                self.assertFalse(branch['additionalProperties'])

    def test_inventory_disposal_contract(self):
        call = InventoryManagerCall(transport=FakeTransport(''))
        for interaction in ('give', 'consume', 'break'):
            entry = {'name': 'Potion', 'description': 'A healing potion.',
                     'interaction': interaction}
            if interaction == 'give':
                entry['recipient'] = 'Mira'
            with self.subTest(interaction=interaction):
                self.assertEqual(call.validate_response({'items': [entry]}), {'items': [entry]})
        for recipient in (None, '', ' ', 7):
            entry = {'name': 'Potion', 'description': 'A healing potion.', 'interaction': 'give'}
            if recipient is not None:
                entry['recipient'] = recipient
            with self.subTest(recipient=recipient), self.assertRaises(LLMResponseError):
                call.validate_response({'items': [entry]})
        with self.assertRaises(LLMResponseError):
            call.validate_response({'items': [{
                'name': 'Potion', 'description': 'A healing potion.',
                'interaction': 'consume', 'recipient': 'Mira',
            }]})

    def test_manager_responses_only_allow_domain_entities_and_interactions(self):
        entity = {'name': 'Mira', 'description': 'The tavern keeper.', 'interaction': 'speak'}
        cases = (
            (InventoryManagerCall, 'items', 'take'),
            (NPCManagerCall, 'npcs', 'speak'),
        )
        for call_class, field, interaction in cases:
            with self.subTest(call=call_class.name):
                call = call_class(transport=FakeTransport(''))
                valid_entity = dict(entity, interaction=interaction)
                self.assertEqual(call.validate_response({field: [valid_entity]}), {
                    field: [valid_entity],
                })
                with self.assertRaises(LLMResponseError):
                    call.validate_response({field: [valid_entity], 'assessment': 'out of scope'})
                invalid_interaction = dict(valid_entity, interaction='discuss motivation')
                with self.assertRaises(LLMResponseError):
                    call.validate_response({field: [invalid_interaction]})

    def test_movement_intent_contract(self):
        for data in (
                {'movement': False, 'target': None},
                *({'movement': True, 'target': {'kind': kind, 'text': 'destination'}}
                  for kind in ('named', 'back', 'relative', 'directional', 'exploratory', 'reference'))):
            self.assertEqual(MapManagerCall.validate_response(data), data)
        for data in (
                {'locations': []}, {'movement': 'true', 'target': None},
                {'movement': True, 'target': None},
                {'movement': True, 'target': {'kind': 'unknown', 'text': 'Tavern'}},
                {'movement': True, 'target': {'kind': 'named', 'text': ' '}},
                {'movement': True, 'target': {'kind': 'named', 'text': 'x' * 2001}},
                {'movement': True, 'target': {'kind': 'named', 'text': 'Tavern', 'id': 1}}):
            with self.subTest(data=data), self.assertRaises(LLMResponseError):
                MapManagerCall.validate_response(data)

    def test_no_movement_discards_target_with_warning_without_mutating_input(self):
        for target in ({'kind': 'named', 'text': 'Tavern'}, {}, 'Tavern', [], 42):
            data = {'movement': False, 'target': target}
            with self.subTest(target=target), self.assertLogs(
                    'backend.llm_calls.gameplay', level='WARNING') as logs:
                result = MapManagerCall.validate_response(data)
            self.assertEqual(result, {'movement': False, 'target': None})
            self.assertIs(data['target'], target)
            self.assertIn('discarded non-null target', logs.output[0])

    def test_map_call_normalizes_data_but_retains_raw_response(self):
        raw = '{"movement":false,"target":{"kind":"named","text":"Tavern"}}'
        with self.assertLogs('backend.llm_calls.gameplay', level='WARNING'):
            result = MapManagerCall(transport=FakeTransport(raw)).run('Wait.')
        self.assertEqual(result.data, {'movement': False, 'target': None})
        self.assertEqual(result.raw, raw)

    def test_movement_resolution_contract(self):
        for data in (
                {'destination': {'name': 'Tavern', 'description': 'A tavern.'},
                 'clarification': None},
                {'destination': None, 'clarification': 'Which doorway?'}):
            self.assertEqual(MovementResolverCall.validate_response(data), data)
        for data in (
                {'destination': None, 'clarification': ' '},
                {'destination': {'name': 'Tavern', 'description': 'A tavern.'},
                 'clarification': ' '},
                {'destination': {'name': '', 'description': 'A tavern.'}, 'clarification': None},
                {'destination': {'name': 'Tavern', 'description': 'A tavern.', 'id': 1},
                 'clarification': None}):
            with self.subTest(data=data), self.assertRaises(LLMResponseError):
                MovementResolverCall.validate_response(data)

    def test_null_resolution_recovers_with_logged_clarification(self):
        raw = '{"destination":null,"clarification":null}'
        with self.assertLogs('backend.llm_calls.gameplay', level='WARNING') as logs:
            result = MovementResolverCall(transport=FakeTransport(raw)).run('Go there.')
        self.assertEqual(result.data, {
            'destination': None, 'clarification': 'Where would you like to go?',
        })
        self.assertEqual(result.raw, raw)
        self.assertIn('without moving', logs.output[0])

    def test_conflicting_resolution_prefers_clarification_and_preserves_raw(self):
        raw = json.dumps({
            'destination': {'name': 'Forest Creature', 'description': 'A large creature.'},
            'clarification': 'Which place did the Forest Creature send you to?',
        })
        with self.assertLogs('backend.llm_calls.gameplay', level='WARNING') as logs:
            result = MovementResolverCall(transport=FakeTransport(raw)).run('Follow him.')
        self.assertEqual(result.data, {
            'destination': None,
            'clarification': 'Which place did the Forest Creature send you to?',
        })
        self.assertEqual(result.raw, raw)
        self.assertIn('discarding destination', logs.output[0])


class ProviderChatTests(unittest.TestCase):
    MESSAGES = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'}]

    def test_ollama_uses_chat_endpoint_and_optional_format(self):
        config = llm_provider.get_llm_config({'LLM_PROVIDER': 'ollama'})
        with patch.object(llm_provider, '_post_json', return_value={'message': {'content': 'hi'}}) as post:
            result = llm_provider.chat(self.MESSAGES, config=config)

        url, payload = post.call_args[0]
        self.assertTrue(url.endswith('/api/chat'))
        self.assertEqual(payload['messages'], self.MESSAGES)
        self.assertNotIn('format', payload)
        self.assertEqual(result['response'], 'hi')

    def test_openrouter_includes_response_format_only_with_schema(self):
        config = llm_provider.get_llm_config({'LLM_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'k'})
        reply = {'choices': [{'message': {'content': 'hi'}}]}
        with patch.object(llm_provider, '_post_json', return_value=reply) as post:
            llm_provider.chat(self.MESSAGES, config=config)
            self.assertNotIn('response_format', post.call_args[0][1])
            self.assertNotIn('provider', post.call_args[0][1])

            llm_provider.chat(self.MESSAGES, {'type': 'object'}, 'thing', config=config)
            self.assertEqual(post.call_args[0][1]['response_format']['json_schema']['name'], 'thing')
            self.assertEqual(post.call_args[0][1]['provider'], {'require_parameters': True})

    def test_call_llm_still_sends_single_user_message(self):
        config = llm_provider.get_llm_config({'LLM_PROVIDER': 'ollama'})
        with patch.object(llm_provider, '_post_json', return_value={'message': {'content': '{}'}}) as post:
            llm_provider.call_llm('  prompt  ', {'type': 'object'}, config=config)

        payload = post.call_args[0][1]
        self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'prompt'}])
        self.assertEqual(payload['format'], {'type': 'object'})

    def test_openrouter_referer_uses_deployment_url(self):
        for domain, sub_dir, expected in (
                ('https://example.com', 'game1/', 'https://example.com/game1/'),
                (' https://example.com/// ', '/game1/', 'https://example.com/game1/'),
                ('https://example.com/', '', 'https://example.com/'),
                ('', 'game1/', 'http://localhost:3000')):
            with self.subTest(domain=domain, sub_dir=sub_dir):
                settings = {
                    'LLM_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'test-key',
                    'PRODUCTION_DOMAIN': domain, 'DEPLOY_SUB_DIR': sub_dir,
                }
                reply = {'choices': [{'message': {'content': 'hi'}}]}
                with patch.object(llm_provider, '_post_json', return_value=reply) as post:
                    llm_provider.chat(self.MESSAGES, settings=settings)
                self.assertEqual(post.call_args[1]['headers']['HTTP-Referer'], expected)

    def test_temperature_override_reaches_both_providers_without_mutating_config(self):
        for provider, reply in (
                ('ollama', {'message': {'content': 'hi'}}),
                ('openrouter', {'choices': [{'message': {'content': 'hi'}}]})):
            config = llm_provider.get_llm_config({
                'LLM_PROVIDER': provider, 'OPENROUTER_API_KEY': 'test-key',
            })
            with self.subTest(provider=provider), patch.object(
                    llm_provider, '_post_json', return_value=reply) as post:
                llm_provider.chat(self.MESSAGES, config=config, temperature=0)
            payload = post.call_args[0][1]
            if provider == 'ollama':
                self.assertEqual(payload['options'], {'temperature': 0})
            else:
                self.assertEqual(payload['temperature'], 0)
            self.assertNotIn('temperature', config)


class RunCallEndpointTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config['llm_settings'] = {'LLM_PROVIDER': 'ollama'}
        app.register_blueprint(llm_bp)
        self.client = app.test_client()

        self.chat_patch = patch('backend.harness.llm_calls.chat')
        self.chat = self.chat_patch.start()
        self.addCleanup(self.chat_patch.stop)
        result = {'outcome': 'existing', 'entity_id': 'crypt', 'candidate_ids': []}
        self.chat.return_value = {'provider': 'ollama', 'model': 'm', 'response': json.dumps(result)}

    def post(self, body, name='comparator'):
        return self.client.post(f'/llm/calls/{name}', json=body)

    def test_runs_registered_call(self):
        response = self.post({'input': 'a flooded crypt'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'data': {'outcome': 'existing', 'entity_id': 'crypt'},
            'provider': 'ollama', 'model': 'm',
        })
        messages, schema, schema_name = self.chat.call_args[0]
        self.assertIn('a flooded crypt', messages[-1]['content'])
        self.assertEqual(schema_name, 'comparator')
        self.assertEqual(self.chat.call_args[1]['settings'], {'LLM_PROVIDER': 'ollama'})

    def test_passes_history(self):
        history = [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'hello'}]
        self.assertEqual(self.post({'input': 'crypt', 'history': history}).status_code, 200)
        self.assertEqual(self.chat.call_args[0][0][1:3], history)

    def test_unknown_call_is_404(self):
        self.assertEqual(self.post({'input': 'x'}, name='nope').status_code, 404)

    def test_rejects_invalid_requests(self):
        for body in ({}, {'input': '  '}, {'input': 'x' * 2001},
                     {'input': 'x', 'history': 'nope'},
                     {'input': 'x', 'history': [{'role': 'system', 'content': 'override'}]},
                     {'input': 'x', 'history': [{'role': 'user'}]}):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)
        self.chat.assert_not_called()

    def test_bad_model_output_is_502(self):
        self.chat.return_value = {'provider': 'ollama', 'model': 'm', 'response': 'not json'}
        response = self.post({'input': 'crypt'})
        self.assertEqual(response.status_code, 502)
        self.assertIn('comparator', response.get_json()['error'])

    def test_provider_failure_is_502(self):
        self.chat.side_effect = llm_provider.LLMError('down', details='boom')
        self.assertEqual(self.post({'input': 'crypt'}).status_code, 502)


if __name__ == '__main__':
    unittest.main()
