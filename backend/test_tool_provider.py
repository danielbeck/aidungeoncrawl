import copy
import json
import unittest
from unittest.mock import patch

from backend.harness import llm_provider


TOOLS = [{
    'type': 'function', 'function': {
        'name': 'drop_item', 'description': 'Drop a carried item.',
        'parameters': {'type': 'object', 'properties': {'item_id': {'type': 'string'}},
                       'required': ['item_id'], 'additionalProperties': False},
    },
}]


class ToolProviderTests(unittest.TestCase):
    def test_native_tool_request_and_response_for_both_providers(self):
        for provider in ('ollama', 'openrouter'):
            settings = {'LLM_PROVIDER': provider, 'OPENROUTER_API_KEY': 'test-key'}
            arguments = {'item_id': 'key'}
            function = {'name': 'drop_item', 'arguments': (
                arguments if provider == 'ollama' else json.dumps(arguments))}
            message = {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call1', 'type': 'function', 'function': function},
            ]}
            response = {'message': message} if provider == 'ollama' else {
                'choices': [{'message': message, 'finish_reason': 'tool_calls'}]}
            with self.subTest(provider=provider), patch.object(
                    llm_provider, '_post_json', return_value=response) as post:
                result = llm_provider.chat([{'role': 'user', 'content': 'Drop the key.'}],
                                            tools=TOOLS, settings=settings)
            payload = post.call_args[0][1]
            self.assertEqual(payload['tools'], TOOLS)
            self.assertNotIn('format', payload)
            self.assertNotIn('response_format', payload)
            self.assertEqual(result['message']['tool_calls'][0]['function']['arguments'], arguments)
            self.assertEqual(result['message']['tool_calls'][0]['id'], 'call1')
            if provider == 'openrouter':
                self.assertEqual(payload['provider'], {'require_parameters': True})
                self.assertEqual(payload['tool_choice'], 'auto')
                self.assertNotIn('parallel_tool_calls', payload)
            else:
                self.assertEqual(payload['options']['num_ctx'], 16384)
                self.assertEqual(payload['options']['num_predict'], 2048)

    def test_tool_results_and_assistant_history_use_provider_protocol(self):
        messages = [
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call1', 'type': 'function',
                 'function': {'name': 'drop_item', 'arguments': {'item_id': 'key'}}}]},
            {'role': 'tool', 'tool_call_id': 'call1', 'name': 'drop_item',
             'content': '{"ok":true}'},
        ]
        original = copy.deepcopy(messages)
        for provider in ('ollama', 'openrouter'):
            wire = llm_provider._tool_messages(messages, provider)
            if provider == 'ollama':
                self.assertEqual(wire[0]['tool_calls'][0], {
                    'function': {'name': 'drop_item', 'arguments': {'item_id': 'key'}}})
                self.assertEqual(wire[1]['tool_name'], 'drop_item')
                self.assertNotIn('tool_call_id', wire[1])
            else:
                self.assertEqual(wire[1]['tool_call_id'], 'call1')
                self.assertEqual(json.loads(wire[0]['tool_calls'][0]['function']['arguments']),
                                 {'item_id': 'key'})
        self.assertEqual(messages, original)

    def test_ollama_missing_call_id_gets_correlated_id(self):
        message = {'content': '', 'tool_calls': [
            {'function': {'name': 'drop_item', 'arguments': {'item_id': 'key'}}}]}
        result = llm_provider._tool_response(message, 'ollama', 'model')
        self.assertTrue(result['message']['tool_calls'][0]['id'].startswith('call_'))

    def test_invalid_native_tool_messages_fail_explicitly(self):
        for message in (
                None, {'content': [], 'tool_calls': []},
                {'content': '', 'tool_calls': 'invalid'},
                {'content': '', 'tool_calls': [{}]},
                {'content': '', 'tool_calls': [
                    {'function': {'name': 'drop_item', 'arguments': 'not JSON'}}]},
                {'content': '', 'tool_calls': [
                    {'function': {'name': 'drop_item', 'arguments': '[]'}}]}):
            with self.subTest(message=message), self.assertRaises(llm_provider.LLMError):
                llm_provider._tool_response(message, 'ollama', 'model')

    def test_final_text_is_preserved_and_truncation_rejected(self):
        result = llm_provider._tool_response({'content': 'You drop the key.'}, 'ollama', 'model')
        self.assertEqual(result['message']['tool_calls'], [])
        self.assertEqual(result['response'], 'You drop the key.')
        with patch.object(llm_provider, '_post_json', return_value={'choices': [
                {'finish_reason': 'length', 'message': {'content': 'Partial story'}}]}):
            with self.assertRaises(llm_provider.LLMError):
                llm_provider.chat([], tools=TOOLS, settings={
                    'LLM_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'test-key'})
