import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
import flow
from test_control import Wire

# Sanitized shape of the real Safari app-access card from the live session.
SAFARI_APP_CONSENT = {
    'serverName':'cua_repl', 'mode':'form',
    'message':'Allow Computer Use to use "Safari"?',
    'requestedSchema':{'type':'object','properties':{}},
    '_meta':{'codex_approval_kind':'mcp_tool_call','connector_id':'computer-use',
             'persist':['session','always'],'riskLevel':'high',
             'tool_name':'get_app_state','tool_params':{'app':'com.apple.Safari'}},
}


class AutomaticApprovals(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=flow.ROOT;flow.ROOT=Path(self.tmp.name)
        self.app=flow.Flow();self.app.rpc=Wire();self.app.tid='thread';self.app.turn='turn'
    def tearDown(self):
        self.app.events.close();flow.ROOT=self.old;self.tmp.cleanup()
    def toggle(self,on=True):self.app.command({'type':'set_auto_approve','enabled':on})
    def request(self,rid=7,method='mcpServer/elicitation/request',params=None):
        if params is None:
            params={'serverName':'cua_repl','mode':'form','requestedSchema':{'properties':{}},
                    '_meta':{'riskLevel':'low','persist':['session','always'],
                             'codex_approval_kind':'mcp_tool_call','tool_name':'click',
                             'tool_params':{'app':'org.example.app'}}}
        self.app.handle({'id':rid,'method':method,'params':params})
    def question(self):
        self.request(method='item/tool/call',params={'tool':'ask_ring_user',
                     'arguments':{'question':'文件叫什么名字？','options':['甲','乙']}})
        return self.app.state['request']['token']

    def test_safari_app_risk_does_not_block_pending_or_future_auto_approval(self):
        self.request(params=deepcopy(SAFARI_APP_CONSENT))
        self.assertEqual(self.app.rpc.sent,[])
        self.toggle()
        for rid,tool in enumerate(('press_key','get_app_state','type_text','click','scroll'),8):
            p=deepcopy(SAFARI_APP_CONSENT);p['_meta']['tool_name']=tool
            self.request(rid,params=p)
        self.assertEqual(len(self.app.rpc.sent),6)
        self.assertEqual(self.app.state['autoApprovedCount'],6)
        self.assertIsNone(self.app.state['request'])
        self.assertFalse(self.app.apps)
        for _,reply in self.app.rpc.sent:
            self.assertEqual(reply['result'],{'action':'accept','content':{}})
        self.toggle(False);self.request(20,params=deepcopy(SAFARI_APP_CONSENT))
        self.assertEqual(len(self.app.rpc.sent),6)
        self.assertIsNotNone(self.app.state['request'])

    def test_safari_session_grant_covers_later_app_access(self):
        self.request(params=deepcopy(SAFARI_APP_CONSENT))
        self.app.choose(self.app.state['request']['token'],0,'test')
        self.request(8,params=deepcopy(SAFARI_APP_CONSENT))
        self.assertEqual(len(self.app.rpc.sent),2)
        self.assertIsNone(self.app.state['request'])
        self.assertEqual(len(self.app.state['history']),1)

    def test_safari_always_grant_survives_restart_with_high_app_risk(self):
        self.request(params=deepcopy(SAFARI_APP_CONSENT))
        self.app.choose(self.app.state['request']['token'],1,'test')
        self.app.events.close()
        self.app=flow.Flow();self.app.rpc=Wire();self.app.tid='thread';self.app.turn='turn'
        self.request(8,params=deepcopy(SAFARI_APP_CONSENT))
        self.assertEqual(self.app.rpc.sent[-1][1]['result'],{'action':'accept','content':{}})
        self.assertIsNone(self.app.state['request'])

    def test_safari_app_identity_does_not_cover_separate_risk_confirmations(self):
        self.toggle();self.app.apps.add('com.apple.Safari')
        variants=[('connector_id','another-connector'),('connector_id',None),
                  ('codex_approval_kind','action_confirmation'),('persist',[]),
                  ('tool_name','unknown_operation')]
        for rid,(key,value) in enumerate(variants,7):
            with self.subTest(key=key,value=value):
                p=deepcopy(SAFARI_APP_CONSENT);p['_meta'][key]=value
                self.request(rid,params=p)
        self.assertEqual(self.app.rpc.sent,[])
        self.assertEqual(self.app.state['requestCount'],len(variants))

    def test_safari_app_grant_never_fabricates_required_input(self):
        self.toggle();self.app.apps.add('com.apple.Safari')
        p=deepcopy(SAFARI_APP_CONSENT)
        p['requestedSchema']['properties']={'answer':{'type':'string'}}
        p['requestedSchema']['required']=['answer']
        self.request(params=p)
        self.assertEqual(self.app.rpc.sent,[])
        self.assertTrue(self.app.state['request']['blocked'])

    def test_safari_steer_declines_pending_old_action_before_auto_approval(self):
        self.request(params=deepcopy(SAFARI_APP_CONSENT))
        self.assertEqual(self.app.rpc.sent,[])
        self.app.text('修改操作',mode='steer',target_turn='turn')
        self.toggle()
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'decline')
        self.assertEqual(self.app.auto_approved_count,0)
        self.request(8,params=deepcopy(SAFARI_APP_CONSENT))
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'accept')

    def test_safari_cancel_rejects_pending_and_future_app_access(self):
        self.request(params=deepcopy(SAFARI_APP_CONSENT));self.app.control('cancel','test')
        self.toggle()
        self.request(8,params=deepcopy(SAFARI_APP_CONSENT))
        self.assertEqual([v['result']['action'] for m,v in self.app.rpc.sent if m=='reply'],
                         ['cancel','cancel'])
        self.assertEqual(self.app.auto_approved_count,0)

    def test_switch_defaults_off_and_requires_boolean(self):
        self.toggle('true');self.request()
        self.assertFalse(self.app.auto_approve);self.assertEqual(self.app.rpc.sent,[])
    def test_enabling_handles_pending_and_future_approvals(self):
        self.request();self.toggle();self.request(8)
        self.assertEqual(len(self.app.rpc.sent),2)
        self.assertEqual(self.app.state['autoApprovedCount'],2)
        self.assertIsNone(self.app.state['request'])
        self.assertIn('自动允许',self.app.state['history'][-1])
    def test_disabling_does_not_leave_hidden_persistent_app_grants(self):
        self.toggle();self.request();self.toggle(False);self.request(8)
        self.assertEqual(len(self.app.rpc.sent),1)
        self.assertEqual(self.app.rpc.sent[0][1]['result'],{'action':'accept','content':{}})
        self.assertFalse(self.app.apps);self.assertFalse((flow.ROOT/'app-grants.json').exists())
        self.assertIsNotNone(self.app.state['request'])
    def test_stale_action_is_rejected_before_enabling_auto_mode(self):
        self.request()
        self.assertEqual(self.app.rpc.sent,[])
        self.app.text('修改动作',mode='steer',target_turn='turn')
        self.toggle()
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'decline')
        self.assertEqual(self.app.auto_approved_count,0)

    def test_cancel_prevents_subsequent_auto_approvals(self):
        self.toggle();self.app.control('cancel','test');self.request()
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'cancel')
        self.assertEqual(self.app.auto_approved_count,0)
    def test_exit_prevents_subsequent_auto_approvals(self):
        self.toggle();self.app.control('exit','test');self.request()
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'cancel')
    def test_command_and_file_requests_use_offered_single_approval(self):
        self.toggle()
        for i,method in enumerate(('item/commandExecution/requestApproval','item/fileChange/requestApproval')):
            self.request(i,method,{'availableDecisions':['acceptForSession','accept','decline']})
            self.assertEqual(self.app.rpc.sent[-1][1]['result'],{'decision':'accept'})
    def test_no_implicit_execpolicy_amendment_or_unoffered_accept(self):
        self.toggle()
        self.request(method='item/commandExecution/requestApproval',params={
            'availableDecisions':[{'acceptWithExecpolicyAmendment':{'execpolicy_amendment':['sh']}},'decline']})
        self.assertEqual(self.app.rpc.sent,[])
    def test_permission_grants_exact_requested_scope(self):
        self.toggle()
        permissions={'network':{'enabled':True},'fileSystem':{'read':['/example/read-only']}}
        self.request(method='item/permissions/requestApproval',params={'permissions':permissions})
        self.assertEqual(self.app.rpc.sent[-1][1]['result'],{'permissions':permissions,'scope':'turn'})
    def test_high_risk_and_input_forms_still_wait(self):
        self.toggle()
        self.request(params={'serverName':'cua_repl','mode':'form','requestedSchema':{'properties':{}},
                     '_meta':{'riskLevel':'high','tool_params':{'app':'org.example.app'}}})
        self.request(8,params={'serverName':'service','mode':'form',
                     'requestedSchema':{'properties':{'name':{'type':'string'}},'required':['name']}})
        self.assertEqual(self.app.rpc.sent,[])
        self.assertEqual(self.app.state['requestCount'],2)
    def test_auto_mode_never_answers_agent_question(self):
        self.toggle();token=self.question();self.toggle()
        self.assertEqual(self.app.rpc.sent,[])
        self.assertEqual([o['label'] for o in self.app.state['request']['options']],['甲','乙'])
        self.app.command({'type':'answer','questionToken':token,'text':'我的文件'})
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['contentItems'][0]['text'],'我的文件')
    def test_main_task_field_queues_instead_of_answering_question(self):
        self.toggle();self.question()
        self.app.command({'type':'text','text':'下一条任务'})
        self.assertEqual(self.app.rpc.sent,[])
        self.assertEqual(self.app.task_queue[0]['text'],'下一条任务')
    def test_voice_double_tap_then_tap_routes_answer_without_cancelling(self):
        self.toggle();self.question()
        self.assertTrue(self.app.interaction.gesture('double-tap'))
        self.app.interaction.started(1)
        partial=self.app.interaction.partial(1,'取消')
        self.app.transcript_partial(partial)
        self.assertEqual(self.app.state['answerTranscript'],'取消')
        self.assertEqual(self.app.state['transcript'],'')
        self.assertTrue(self.app.interaction.gesture('tap',True))
        frame=self.app.interaction.finished(1,'取消')
        self.app.text(source='ring_voice',**frame)
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['contentItems'][0]['text'],'取消')
        self.assertFalse(self.app.cancelled);self.assertEqual(self.app.task_queue,[])
    def test_stale_answer_does_not_answer_next_question_or_start_task(self):
        token=self.question()
        self.app.answer_text(token,'甲','test')
        self.question();self.app.command({'type':'answer','questionToken':token,'text':'乙'})
        self.assertEqual(len(self.app.rpc.sent),1)
        self.assertEqual(self.app.task_queue,[])
    def test_structured_question_is_not_auto_answered(self):
        self.toggle();self.request(method='item/tool/requestUserInput',params={'questions':[
            {'id':'name','question':'标题？','options':[{'label':'甲','description':'方案甲'}]}]})
        self.assertEqual(self.app.rpc.sent,[])
        token=self.app.state['request']['token']
        self.app.answer_text(token,'自定义标题','test')
        self.assertEqual(self.app.rpc.sent[-1][1]['result'],{'answers':{'name':{'answers':['自定义标题']}}})


if __name__=='__main__':unittest.main()
