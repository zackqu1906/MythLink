"""Small stateless text client using this machine's existing ChatGPT login.

No agent process, tools, filesystem context, or previous-response reference.
This is the ChatGPT Codex backend route, not the public api.openai.com route.
Credentials remain in memory and are sent only to the fixed OpenAI HTTPS host.
"""
from __future__ import annotations
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler, build_opener

ENDPOINT = 'https://chatgpt.com/backend-api/codex/responses'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_text(prompt: str, user_text: str, model: str, effort: str = 'none', timeout_s=45):
    auth = json.loads((Path.home()/'.codex/auth.json').read_text())
    if auth.get('auth_mode') != 'chatgpt' or not auth.get('tokens',{}).get('access_token'):
        raise RuntimeError('A local ChatGPT login is required')
    tokens = auth['tokens']
    payload = {'model':model, 'instructions':prompt,
               'input':[{'role':'user','content':[{'type':'input_text','text':user_text}]}],
               'tools':[], 'tool_choice':'none', 'reasoning':{'effort':effort},
               'store':False, 'stream':True}
    headers = {'Authorization':'Bearer '+tokens['access_token'],
               'Content-Type':'application/json', 'Accept':'text/event-stream'}
    if tokens.get('account_id'):
        headers['ChatGPT-Account-ID'] = tokens['account_id']
    request = Request(ENDPOINT,data=json.dumps(payload,ensure_ascii=False).encode(),headers=headers)
    result = {'endpoint':ENDPOINT,'request':payload,'events':[],'final_text':'',
              'first_text_s':None, 'http_status':None}
    started = time.perf_counter()
    try:
        with build_opener(NoRedirect()).open(request,timeout=timeout_s) as response:
            result['http_status'] = response.status
            size = 0
            for raw in response:
                size += len(raw)
                if size > 2*1024*1024:
                    raise RuntimeError('Response exceeded the 2 MiB safety limit')
                if time.perf_counter()-started > timeout_s:
                    raise TimeoutError('Stateless request timed out')
                if not raw.startswith(b'data:'):
                    continue
                raw = raw[5:].strip()
                if raw == b'[DONE]':
                    break
                event = json.loads(raw)
                result['events'].append(event)
                if event.get('type')=='response.output_text.delta' and result['first_text_s'] is None:
                    result['first_text_s'] = time.perf_counter()-started
                if event.get('type') in ('response.completed','response.failed','response.incomplete'):
                    result['response'] = event['response']
                    result['latency_s'] = time.perf_counter()-started
                    break
                if event.get('type')=='error':
                    raise RuntimeError(json.dumps(event,ensure_ascii=False))
        final = result.get('response',{})
        result['final_text'] = '\n'.join(part.get('text','') for item in final.get('output',[])
                                        if item.get('type')=='message' for part in item.get('content',[])
                                        if part.get('type')=='output_text')
        if final.get('status')!='completed' or not result['final_text']:
            raise RuntimeError('No completed text response: '+str(final.get('error') or final.get('status')))
        if any(item.get('type') not in ('message','reasoning') for item in final.get('output',[])):
            raise RuntimeError('Unexpected tool output in stateless text evaluation')
        if final.get('reasoning',{}).get('effort') != effort:
            raise RuntimeError('Returned reasoning effort does not match the requested setting')
        if final.get('model') != model:
            raise RuntimeError('Returned model does not match the requested model')
    except HTTPError as exc:
        result['http_status'] = exc.code
        result['error'] = exc.read(4096).decode(errors='replace')
    except Exception as exc:
        result['error'] = str(exc) or type(exc).__name__
    result.setdefault('latency_s',time.perf_counter()-started)
    if result.get('error'):
        for value in tokens.values():
            if isinstance(value,str) and value:
                result['error'] = result['error'].replace(value,'[redacted]')
    return result
