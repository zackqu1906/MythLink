import json
import unittest
from unittest.mock import Mock, patch

from tools.chatgpt_stateless_text_client import request_text


class Response:
    status = 200
    def __init__(self, events): self.events = events
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def __iter__(self):
        for event in self.events:
            yield b'data: '+json.dumps(event).encode()+b'\n\n'


class StatelessTextClientTest(unittest.TestCase):
    def run_client(self, effort='none', extra_output=None, error=None):
        response = {'model':'gpt-6-luna','status':'completed','reasoning':{'effort':effort},
                    'output':[{'type':'message','content':[{'type':'output_text','text':'edited'}]}]}
        if extra_output: response['output'].append(extra_output)
        events = [{'type':'response.output_text.delta','delta':'edited'},
                  {'type':'response.completed','response':response}]
        opener = Mock()
        opener.open.side_effect = error
        opener.open.return_value = Response(events)
        auth = {'auth_mode':'chatgpt','tokens':{'access_token':'SECRET_ACCESS','account_id':'SECRET_ACCOUNT'}}
        with patch('tools.chatgpt_stateless_text_client.Path.read_text',return_value=json.dumps(auth)), \
             patch('tools.chatgpt_stateless_text_client.build_opener',return_value=opener):
            result = request_text('EXACT_V4D','EXACT_INPUT','gpt-6-luna')
        return result,opener.open.call_args.args[0]

    def test_only_current_prompt_and_input_no_agent_context(self):
        result,request = self.run_client()
        body = json.loads(request.data)
        self.assertEqual(body['instructions'],'EXACT_V4D')
        self.assertEqual(body['input'],[{'role':'user','content':[{'type':'input_text','text':'EXACT_INPUT'}]}])
        self.assertEqual(body['tools'],[])
        self.assertEqual(body['reasoning'],{'effort':'none'})
        self.assertFalse(body['store'])
        self.assertNotIn('previous_response_id',body)
        self.assertNotIn('error',result)
        self.assertNotIn('SECRET_',json.dumps(result))

    def test_no_silent_effort_fallback_or_tool_execution(self):
        result,_ = self.run_client(effort='low')
        self.assertIn('reasoning effort',result['error'])
        result,_ = self.run_client(extra_output={'type':'function_call','name':'shell'})
        self.assertIn('Unexpected tool',result['error'])

    def test_secrets_redacted_from_error_record(self):
        result,_ = self.run_client(error=RuntimeError('SECRET_ACCESS SECRET_ACCOUNT'))
        self.assertNotIn('SECRET_',json.dumps(result))


if __name__=='__main__':unittest.main()
