import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from backend import llm_provider
from backend.endpoints.llm import bp as llm_bp
from backend.llm_calls import REGISTRY, DescribeRoomCall, LLMCall, LLMResponseError, MessageHistory

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


ROOM = {'title': 'Flooded Crypt', 'description': 'Water laps at your ankles.', 'exits': ['north']}


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


class DescribeRoomCallTests(unittest.TestCase):
    def test_sends_prompt_schema_and_formatted_input(self):
        transport = FakeTransport(json.dumps(ROOM))
        result = DescribeRoomCall(transport=transport).run(' a flooded crypt ')

        self.assertEqual(result.data, ROOM)
        request = transport.calls[0]
        with open(os.path.join(os.path.dirname(__file__), '..', 'shared', 'llm', 'describe_room.json')) as f:
            self.assertEqual(request['schema'], json.load(f))
        self.assertEqual(request['schema_name'], 'describe_room')
        self.assertEqual(request['messages'][0]['role'], 'system')
        self.assertIn('"exits"', request['messages'][0]['content'])
        self.assertEqual(request['messages'][-1], {'role': 'user', 'content': 'Room idea: a flooded crypt'})

    def test_parses_fenced_json(self):
        result = DescribeRoomCall(transport=FakeTransport(f'```json\n{json.dumps(ROOM)}\n```')).run('crypt')
        self.assertEqual(result.data['title'], 'Flooded Crypt')

    def test_rejects_non_json(self):
        with self.assertRaises(LLMResponseError):
            DescribeRoomCall(transport=FakeTransport('not json')).run('crypt')

    def test_rejects_missing_required_fields(self):
        with self.assertRaisesRegex(LLMResponseError, 'exits'):
            DescribeRoomCall(transport=FakeTransport('{"title": "x", "description": "y"}')).run('crypt')

    def test_rejects_invalid_exits(self):
        bad = dict(ROOM, exits=['up'])
        with self.assertRaises(LLMResponseError):
            DescribeRoomCall(transport=FakeTransport(json.dumps(bad))).run('crypt')


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
        self.chat.return_value = {'provider': 'ollama', 'model': 'm', 'response': json.dumps(ROOM)}

    def post(self, body, name='describe_room'):
        return self.client.post(f'/llm/calls/{name}', json=body)

    def test_runs_registered_call(self):
        response = self.post({'input': 'a flooded crypt'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'data': ROOM, 'provider': 'ollama', 'model': 'm'})
        messages, schema, schema_name = self.chat.call_args[0]
        self.assertEqual(messages[-1]['content'], 'Room idea: a flooded crypt')
        self.assertEqual(schema_name, 'describe_room')
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
        self.assertIn('describe_room', response.get_json()['error'])

    def test_provider_failure_is_502(self):
        self.chat.side_effect = llm_provider.LLMError('down', details='boom')
        self.assertEqual(self.post({'input': 'crypt'}).status_code, 502)


if __name__ == '__main__':
    unittest.main()
