import tempfile
import unittest
from pathlib import Path
import flow

class Wire:
    def __init__(self):self.sent=[]
    def call(self,method,params,**_):
        assert method=='mcpServer/tool/call' and params['tool'] in {'turn_ended','js_reset'}
        return {'content':[]}
    def send(self,method,params):self.sent.append((method,params));return len(self.sent)
    def write(self,data):self.sent.append(('reply',data))

class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=flow.ROOT;flow.ROOT=Path(self.tmp.name)
        self.app=flow.Flow();self.app.rpc=Wire();self.app.tid='thread';self.app.turn='turn'
    def tearDown(self):self.app.events.close();flow.ROOT=self.old;self.tmp.cleanup()
    def request(self,rid=7,app='com.apple.calculator',persist=True,risk='low',tool='click',properties=None):
        self.app.handle({'id':rid,'method':'mcpServer/elicitation/request','params':{
            'mode':'form','message':'请求应用操作','requestedSchema':{'type':'object','properties':properties or {}},
            'serverName':'cua_repl','_meta':{'persist':['session','always'] if persist else [],'codex_approval_kind':'mcp_tool_call','riskLevel':risk,'tool_name':tool,'tool_params':{'app':app,'target':12}}}})
    def choose(self,index=0,token=None):
        self.app.choose(token or self.app.state['request']['token'],index,'test')
    def test_app_grant_automates_later_routine_calls(self):
        self.request();self.assertEqual(self.app.rpc.sent,[])
        self.assertEqual(self.app.state['request']['optionCount'],3)
        self.choose();self.request(8)
        self.assertEqual(len(self.app.rpc.sent),2)
        self.assertIsNone(self.app.state['request'])
        self.assertEqual(self.app.state['grantedApps'],['计算器（此对话）'])
        self.assertEqual(len(self.app.state['history']),1)
    def test_one_time_grant_does_not_authorize_app(self):
        self.request(persist=False);self.choose();self.request(8,persist=False)
        self.assertEqual(len(self.app.rpc.sent),1)
        self.assertEqual(self.app.state['request']['requestId'],'8')
    def test_new_app_requires_its_own_consent(self):
        self.request();self.choose();self.request(8,app='com.apple.TextEdit')
        self.assertEqual(len(self.app.rpc.sent),1)
        self.assertEqual(self.app.state['request']['optionCount'],3)
    def test_cancel_gates_previously_authorized_app(self):
        self.request();self.choose();self.app.control('cancel','test');self.request(8)
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'cancel')
        self.assertIsNone(self.app.state['request'])

    def test_additional_permission_not_covered_by_app_grant(self):
        self.request();self.choose()
        self.request(8,risk='high',persist=False)
        self.assertEqual(len(self.app.rpc.sent),1)
        self.assertIsNotNone(self.app.state['request'])
        self.choose()
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'accept')
        self.request(9,risk='high',persist=False)
        self.assertEqual(len(self.app.rpc.sent),2)
        self.assertIsNotNone(self.app.state['request'])
    def test_retired_pause_commands_do_not_change_requests_or_approve(self):
        self.request()
        for action in ('pause','resume'): self.app.command({'type':action})
        self.assertEqual(self.app.rpc.sent,[])
        self.assertNotIn('paused',self.app.state)
        self.choose();self.assertEqual(self.app.rpc.sent[0][1]['result']['action'],'accept')
        self.assertEqual(self.app.turn,'turn')

    def test_steer_immediately_expires_pending_old_action(self):
        self.request();self.app.text('改成九乘九',mode='steer',target_turn='turn')
        self.assertEqual([m for m,_ in self.app.rpc.sent],['turn/steer','reply'])
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'decline')
        self.assertIn('已失效',self.app.state['history'][-1])

    def test_cancel_resolves_approval_before_interrupt_and_rejects_later_calls(self):
        self.request();self.app.control('cancel','test')
        self.assertEqual([m for m,_ in self.app.rpc.sent],['reply'])
        self.app.handle({'method':'serverRequest/resolved','params':{'requestId':7}})
        self.assertEqual(self.app.rpc.sent[-1][0],'turn/interrupt')
        self.request(8);self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'cancel')
    def test_repeated_cancel_waits_for_same_request_resolution(self):
        self.request();self.app.control('cancel','test');self.app.control('cancel','test')
        self.assertEqual([m for m,_ in self.app.rpc.sent],['reply'])
        self.assertEqual(self.app.interrupt_waiting_for,{7})
        self.app.handle({'method':'serverRequest/resolved','params':{'requestId':7}})
        self.app.control('cancel','test')
        self.assertEqual([m for m,_ in self.app.rpc.sent],['reply','turn/interrupt'])
    def test_start_failure_cancels_queue_and_accepts_next_task(self):
        self.app.turn=None
        self.app.text('第一条任务');start=self.app.start_id
        self.app.text('排队任务')
        self.app.handle({'id':start,'error':{'message':'test start error'}})
        self.assertEqual(self.app.task_queue,[])
        self.assertFalse(self.app.cancelled)
        self.assertNotIn('paused',self.app.state)
        self.app.text('重新提交')
        self.assertEqual(self.app.rpc.sent[-1][0],'turn/start')
        self.assertEqual(self.app.rpc.sent[-1][1]['input'][0]['text'],'重新提交')
    def test_cancel_during_start_interrupts_once_turn_id_arrives(self):
        self.app.turn=None
        self.app.text('启动中的任务');self.app.control('cancel','test')
        self.assertEqual([m for m,_ in self.app.rpc.sent],['turn/start'])
        self.app.handle({'method':'turn/started','params':{'turn':{'id':'late-turn'}}})
        self.assertEqual(self.app.rpc.sent[-1],('turn/interrupt',{'threadId':'thread','turnId':'late-turn'}))
    def test_cancel_does_not_dispatch_queue_on_late_success_event(self):
        self.app.control('cancel','test')
        self.app.text('取消期间提交的任务')
        self.app.handle({'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}})
        self.assertEqual(self.app.task_queue,[])
        self.assertFalse(any(method=='turn/start' for method,_ in self.app.rpc.sent))
    def test_stale_click_cannot_approve_next_request(self):
        self.request(persist=False);token=self.app.state['request']['token'];self.choose()
        self.request(8,persist=False);self.choose(token=token)
        self.assertEqual(len(self.app.rpc.sent),1)
    def test_completed_turn_invalidates_request(self):
        self.request();token=self.app.state['request']['token']
        self.app.handle({'method':'turn/completed','params':{'turn':{'id':'turn','status':'interrupted'}}})
        self.choose(token=token);self.assertEqual(self.app.rpc.sent,[]);self.assertIsNone(self.app.state['request'])
    def test_any_application_can_be_authorized_without_static_allowlist(self):
        self.request(app='com.apple.mail');self.choose()
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'accept')
        self.assertEqual(self.app.state['grantedApps'],['com.apple.mail（此对话）'])
        self.request(8,app='com.apple.mail')
        self.assertEqual(len(self.app.rpc.sent),2)
        self.request(9,app='org.example.new-desktop-app')
        self.assertEqual(len(self.app.rpc.sent),2)
        self.assertTrue(self.app.state['request']['options'][0]['enabled'])
        self.choose()
        self.assertEqual(len(self.app.rpc.sent),3)
    def test_unsupported_request_remains_visible(self):
        self.app.handle({'id':8,'method':'new/permission','params':{'reason':'new request'}})
        self.assertEqual(self.app.rpc.sent,[])
        self.assertEqual(self.app.state['requestCount'],1)
        self.assertTrue(self.app.state['request']['blocked'])
    def test_app_grant_does_not_accept_unimplemented_form_fields(self):
        self.request(app='org.example.app');self.choose()
        self.request(8,app='org.example.app',properties={'scope':{'type':'string'}})
        self.choose()
        self.assertEqual(len(self.app.rpc.sent),1)
        self.assertTrue(self.app.state['request']['blocked'])
    def test_unknown_app_steer_declines_pending_extra_permission(self):
        self.request(app='org.example.app');self.choose()
        self.request(8,app='org.example.app',risk='high',persist=False)
        self.assertEqual(len(self.app.rpc.sent),1)
        self.app.text('修改当前操作',mode='steer',target_turn='turn')
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'decline')

    def test_decline_does_not_cancel_entire_task(self):
        self.request();self.choose(2)
        self.assertEqual(self.app.rpc.sent[0][1]['result']['action'],'decline')
        self.assertFalse(self.app.cancelled)
    def test_questions_show_options_and_send_exact_answer(self):
        self.app.handle({'id':9,'method':'item/tool/call','params':{'tool':'ask_ring_user',
            'arguments':{'question':'用哪个标题？','options':['方案甲','方案乙']}}})
        self.assertEqual(self.app.state['request']['optionCount'],2);self.choose(1)
        self.assertEqual(self.app.rpc.sent[0][1]['result']['contentItems'][0]['text'],'方案乙')
    def test_multiple_questions_keep_all_answers(self):
        self.app.handle({'id':9,'method':'item/tool/requestUserInput','params':{'questions':[
            {'id':'a','question':'标题？','options':[{'label':'甲','description':'标题甲'}]},
            {'id':'b','question':'内容？','options':[{'label':'乙','description':'内容乙'}]}]}})
        self.choose();self.assertEqual(self.app.rpc.sent,[])
        self.app.command({'type':'answer','questionToken':self.app.state['request']['token'],'text':'第二个问题的自由回答'})
        self.assertEqual(self.app.rpc.sent[0][1]['result']['answers'],{'a':{'answers':['甲']},'b':{'answers':['第二个问题的自由回答']}})
    def test_gesture_selection_does_not_submit_until_confirmed(self):
        self.request();self.app.move_option(self.app.state['request']['token'],1)
        self.assertEqual(self.app.rpc.sent,[]);self.assertEqual(self.app.state['request']['selected'],1)
    def test_extra_permissions_never_silently_granted(self):
        self.app.handle({'id':9,'method':'item/permissions/requestApproval','params':{
            'reason':'请求联网','permissions':{'network':{'enabled':True}}}})
        self.assertEqual(self.app.rpc.sent,[])
        self.choose(1);self.assertEqual(self.app.rpc.sent[-1][1]['result']['permissions'],{})

    def test_queue_is_fifo_and_does_not_steer_running_task(self):
        self.app.text('下一条甲');self.app.text('下一条乙')
        self.assertEqual(self.app.rpc.sent,[])
        self.app.handle({'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}})
        self.assertEqual(self.app.rpc.sent[0][0],'turn/start')
        self.assertEqual(self.app.rpc.sent[0][1]['input'][0]['text'],'下一条甲')
        self.assertEqual([x['text'] for x in self.app.task_queue],['下一条乙'])
    def test_steer_keeps_queue_and_targets_original_turn(self):
        self.app.text('下一条任务')
        self.app.text('改成20项',mode='steer',target_turn='turn')
        self.assertEqual(self.app.rpc.sent[0][0],'turn/steer')
        self.assertEqual(self.app.rpc.sent[0][1]['expectedTurnId'],'turn')
        self.assertEqual(len(self.app.task_queue),1)
    def test_late_steer_never_changes_next_turn(self):
        self.app.turn='next-turn'
        self.app.text('改成20项',mode='steer',target_turn='old-turn')
        self.assertEqual(self.app.rpc.sent,[]);self.assertEqual(self.app.task_queue,[])
    def test_late_spoken_cancel_cannot_cancel_new_turn(self):
        self.app.turn='next-turn'
        self.app.text('取消',mode='steer',target_turn='old-turn')
        self.assertEqual(self.app.rpc.sent,[])
        self.assertFalse(self.app.cancelled)
    def test_cancel_clears_waiting_tasks(self):
        self.app.text('下一条任务');self.app.control('cancel','test')
        self.assertEqual(self.app.task_queue,[])
    def test_always_app_consent_is_client_persistent(self):
        self.request();self.choose(1)
        self.assertIn('com.apple.calculator',self.app.always_apps)
        self.assertTrue((flow.ROOT/'app-grants.json').exists())
    def test_arbitrary_app_always_consent_survives_restart(self):
        self.request(app='org.example.persisted-app');self.choose(1)
        self.app.events.close()
        self.app=flow.Flow();self.app.rpc=Wire();self.app.tid='thread';self.app.turn='turn'
        self.request(8,app='org.example.persisted-app')
        self.assertEqual(self.app.rpc.sent[-1][1]['result']['action'],'accept')
        self.assertEqual(self.app.state['grantedApps'],['org.example.persisted-app（始终允许）'])
    def test_nonroutine_persistent_consent_keeps_future_decisions_visible(self):
        self.request(app='org.example.app',risk='high');self.choose(1)
        self.assertNotIn('_meta',self.app.rpc.sent[-1][1]['result'])
        self.request(8,app='org.example.app',risk='high')
        self.assertEqual(len(self.app.rpc.sent),1)
        self.assertIsNotNone(self.app.state['request'])
    def test_no_custom_options_for_free_question(self):
        self.app.handle({'id':9,'method':'item/tool/call','params':{'tool':'ask_ring_user','arguments':{'question':'标题叫什么？'}}})
        self.assertEqual(self.app.state['request']['optionCount'],0)
        self.assertEqual(self.app.state['request']['options'],[])
    def test_explicit_server_choices_preserve_order(self):
        self.app.handle({'id':9,'method':'item/commandExecution/requestApproval','params':{'availableDecisions':['decline','cancel']}})
        self.assertEqual([o['label'] for o in self.app.state['request']['options']],['拒绝','取消'])

    def test_main_workspace_can_answer_second_card_without_answering_first(self):
        for rid in (7,8):
            self.app.handle({'id':rid,'method':'item/tool/call','params':{'tool':'ask_ring_user',
                'arguments':{'question':f'问题{rid}？','options':['甲','乙']}}})
        cards=self.app.state['requests']
        self.assertEqual(len(cards),2)
        self.app.answer_text(cards[1]['token'],'第二个问题的回答','test')
        self.assertEqual(self.app.rpc.sent[-1][1]['id'],8)
        self.assertEqual(self.app.state['request']['requestId'],'7')

    def test_streaming_feedback_replaces_previous_message(self):
        self.app.handle({'method':'item/agentMessage/delta','params':{'itemId':'a','delta':'正在'}})
        self.app.handle({'method':'item/agentMessage/delta','params':{'itemId':'a','delta':'整理'}})
        self.assertEqual(self.app.state['agent'],'正在整理')
        self.app.handle({'method':'item/agentMessage/delta','params':{'itemId':'b','delta':'已完成'}})
        self.assertEqual(self.app.state['agent'],'已完成')

    def test_embedded_runtime_uses_own_directory_and_copies_published_state(self):
        states=[]
        with tempfile.TemporaryDirectory() as tmp:
            embedded=flow.Flow(with_ring=False,runtime_dir=tmp,on_state=states.append)
            try:
                embedded.publish(agent='主界面输出')
                states[-1]['agent']='外部改动'
                self.assertEqual(embedded.state['agent'],'主界面输出')
                self.assertTrue((Path(tmp)/'state.json').exists())
            finally:embedded.events.close()

if __name__=='__main__': unittest.main()
