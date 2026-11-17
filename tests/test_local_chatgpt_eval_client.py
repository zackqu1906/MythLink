"""Protocol contract checks; no real model calls or account access."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import signal
from tools.local_chatgpt_eval_client import LocalChatGPTClient


class FakeClient(LocalChatGPTClient):
    def __init__(self, *, model='gpt-6-astra', instructions=(), tool=False):
        self.workdir = Path('/tmp/proximic-test')
        self.timeout_s = 2
        self.config = {'model_reasoning_effort':'low'}
        self.notifications = []
        self.requests = []
        self.selected_model = model
        self.instruction_sources = list(instructions)
        self.tool = tool
        self.counter = 0

    def call(self, method, params):
        self.requests.append((method,params))
        if method == 'thread/start':
            self.counter += 1
            self.tid = f'ephemeral-{self.counter}'
            return {'thread':{'id':self.tid}, 'model':self.selected_model,
                    'instructionSources':self.instruction_sources}
        self.events = iter([
            {'method':'item/agentMessage/delta','params':{'threadId':self.tid,'delta':'清禾。'}},
            *([{'method':'item/completed','params':{'threadId':self.tid,'item':{'type':'commandExecution'}}}] if self.tool else []),
            {'method':'item/completed','params':{'threadId':self.tid,'item':{'type':'agentMessage','text':'清禾。'}}},
            {'method':'turn/completed','params':{'threadId':self.tid,'turn':{'status':'completed'}}},
        ])
        return {'turn':{'id':'turn-id'}}

    def receive(self, deadline):
        return next(self.events)


class ClientContract(unittest.TestCase):
    def test_each_case_gets_empty_session_with_exact_prompt_and_input(self):
        c = FakeClient()
        first = c.generate('冻结提示词', '{"utterance":"清晨的清"}')
        second = c.generate('冻结提示词', '{"utterance":"晴天的晴"}')
        self.assertEqual(first['final_text'],'清禾。')
        self.assertNotEqual(first['session_response']['thread']['id'],second['session_response']['thread']['id'])
        self.assertEqual(c.requests[0][1]['baseInstructions'],'冻结提示词')
        self.assertTrue(c.requests[0][1]['ephemeral'])
        self.assertEqual(c.requests[1][1]['input'],[{'type':'text','text':'{"utterance":"清晨的清"}'}])
        self.assertEqual(c.requests[1][1]['effort'],'low')

    def test_different_model_or_extra_instructions_fail_before_generation(self):
        for client in [FakeClient(model='other'),FakeClient(instructions=['/other/AGENTS.md'])]:
            with self.assertRaises(RuntimeError):
                client.generate('prompt','input')
            self.assertEqual(len(client.requests),1)

    def test_tool_use_is_not_reported_as_clean_text_generation(self):
        result = FakeClient(tool=True).generate('prompt','input')
        self.assertIn('error',result)
        self.assertTrue(result['tool_items'])

    def test_cleanup_never_signals_empty_group_and_does_not_hide_live_failure(self):
        client = object.__new__(LocalChatGPTClient)
        client.process = Mock(pid=12345)
        client.live_group_members = Mock(return_value=[])
        with patch('tools.local_chatgpt_eval_client.os.killpg') as kill:
            client.signal_group(signal.SIGTERM)
            kill.assert_not_called()
        client.live_group_members = Mock(side_effect=[[12345],[]])
        with patch('tools.local_chatgpt_eval_client.os.killpg',side_effect=PermissionError):
            client.signal_group(signal.SIGTERM)
        client.live_group_members = Mock(return_value=[12345])
        with patch('tools.local_chatgpt_eval_client.os.killpg',side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                client.signal_group(signal.SIGTERM)


if __name__ == '__main__':
    unittest.main()
