import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from backend import llm_provider
from backend.endpoints.llm import bp as llm_bp
from backend.llm_calls import (
    REGISTRY, InventoryManagerCall, LLMCall, LLMResponseError, MapManagerCall,
    MessageHistory, NPCManagerCall,
)

TEST_DEFINITIONS_DIR = tempfile.mkdtemp()
SCHEMA = {'type': 'object', 'properties': {'x': {'type': 'string'}}}


def write_definition(name, prompt=None, schema=None):
    if prompt is not None:
        with open(os.path.join(TEST_DEFINITIONS_DIR, f'{name}.md'), 'w') as prompt_file:
            prompt_file.write(prompt)
    if schema is not None:
        with open(os.path.join(TEST_DEFINITIONS_DIR, f'{name}.json'), 'w') as schema_file:
            json.dump(schema, schema_file)


def setUpModule():
    write_definition('echo', '\nRepeat the input.\n')
    write_definition('schema_call', '  Do the thing.  ', SCHEMA)
    write_definition('empty', '   ')
    write_definition('bad_schema', 'Prompt', ['not', 'an', 'object'])


def tearDownModule():
    shutil.rmtree(TEST_DEFINITIONS_DIR)


class TestCall(LLMCall):
    definitions_dir = TEST_DEFINITIONS_DIR


class EchoCall(TestCall):
    name = 'echo'


class SchemaCall(TestCall):
    name = 'schema_call'


class FakeTransport:
    """Records requests and replies with a canned response."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, messages, schema, schema_name):
        self.calls.append({'messages': messages, 'schema': schema, 'schema_name': schema_name})
        return {'provider': 'fake', 'model': 'fake-model', 'response': self.response}


class LLMCallTests(unittest.TestCase):
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

    def test_loads_prompt_and_optional_schema_from_files(self):
        echo = EchoCall(transport=FakeTransport(''))
        self.assertEqual(echo.system_prompt, 'Repeat the input.')
        self.assertIsNone(echo.response_format)
        self.assertEqual(SchemaCall(transport=FakeTransport('')).response_format, SCHEMA)

    def test_every_registered_call_has_valid_definition_files(self):
        for name, call_class in REGISTRY.items():
            with self.subTest(name=name):
                self.assertEqual(call_class.name, name)
                call_class(transport=FakeTransport(''))

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
        with self.assertLogs('backend.llm_calls.base', level='INFO') as logs:
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

        with self.assertLogs('backend.llm_calls.base', level='INFO') as logs:
            with self.assertRaises(llm_provider.LLMError) as raised:
                EchoCall(transport=transport).run('hello')
        self.assertIs(raised.exception, error)
        self.assertIn('started', logs.output[0])
        self.assertIn('failed', logs.output[1])
        self.assertIn('provider unavailable', logs.output[1])

    def test_success_logs_share_a_call_id_and_mark_truncated_responses(self):
        raw = 'x' * 32769
        with self.assertLogs('backend.llm_calls.base', level='INFO') as logs:
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


class GameplayManagerResponseTests(unittest.TestCase):
    def test_manager_responses_only_allow_domain_entities_and_interactions(self):
        entity = {'name': 'Mira', 'description': 'The tavern keeper.', 'interaction': 'speak'}
        cases = (
            (InventoryManagerCall, 'items', 'use'),
            (MapManagerCall, 'locations', 'enter'),
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

            llm_provider.chat(self.MESSAGES, {'type': 'object'}, 'thing', config=config)
            self.assertEqual(post.call_args[0][1]['response_format']['json_schema']['name'], 'thing')

    def test_call_llm_still_sends_single_user_message(self):
        config = llm_provider.get_llm_config({'LLM_PROVIDER': 'ollama'})
        with patch.object(llm_provider, '_post_json', return_value={'message': {'content': '{}'}}) as post:
            llm_provider.call_llm('  prompt  ', {'type': 'object'}, config=config)

        payload = post.call_args[0][1]
        self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'prompt'}])
        self.assertEqual(payload['format'], {'type': 'object'})


class RunCallEndpointTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config['llm_settings'] = {'LLM_PROVIDER': 'ollama'}
        app.register_blueprint(llm_bp)
        self.client = app.test_client()

        self.chat_patch = patch('backend.llm_calls.base.chat')
        self.chat = self.chat_patch.start()
        self.addCleanup(self.chat_patch.stop)
        result = {'already_present': True}
        self.chat.return_value = {'provider': 'ollama', 'model': 'm', 'response': json.dumps(result)}

    def post(self, body, name='comparator'):
        return self.client.post(f'/llm/calls/{name}', json=body)

    def test_runs_registered_call(self):
        response = self.post({'input': 'a flooded crypt'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'data': {'already_present': True}, 'provider': 'ollama', 'model': 'm',
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
