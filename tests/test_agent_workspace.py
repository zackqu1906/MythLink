from copy import deepcopy

import pytest

from proximic_ring.agent_workflow.agent.workspace import Workspace, bounded_frame, stack_frames
from proximic_ring.agent_workflow.agent.agent_focus import AgentFocusMemory


def request(token, kind='permission'):
    return dict(token=token, kind=kind, title='请求', message='允许使用 Safari？', options=[
        dict(index=0,label='允许此对话',enabled=True,preferred=False),
        dict(index=1,label='始终允许',enabled=True,preferred=True),
        dict(index=2,label='拒绝',enabled=True,preferred=False)])


def test_cards_follow_request_order_and_navigation_is_bounded():
    w=Workspace();w.update({'requests':[request('a'),request('b')]})
    assert w.keys == ['task','a','b']
    assert [w.move(1) for _ in range(3)] == ['a','b','b']
    assert [w.move(-1) for _ in range(3)] == ['a','task','task']
    assert w.selections['a'] == 1
    w.select('a',1)
    assert w.confirm('a') == {'type':'choose','token':'a','index':2}


def test_questions_share_one_window_with_fifo_answers_and_no_duplicate_submission():
    w=Workspace();w.update({'requests':[request('a','question'),request('b','question')]})
    assert w.keys == ['task'] and w.input_key == 'a'
    w.edit('a','回答甲');w.edit('task','新任务')
    assert w.submission('a')['questionToken']=='a'
    w.sent('a')
    w.edit('a','重复回答')
    assert w.submission('a') is None
    w.edit('a','')
    w.update({'requests':[request('b','question')]})
    assert w.input_key=='b' and 'a' not in w.drafts
    w.edit('b','回答乙')
    assert w.submission('b')['text']=='回答乙'
    assert w.submission('task') is None
    w.sent('b');w.update({'requests':[]})
    assert w.input_key=='task' and w.drafts['task']=='新任务'
    assert w.submission('task')['type']=='text'


def test_new_question_parks_task_draft_after_native_composition_finishes():
    w=Workspace();w.update({'turnId':'turn'});w.edit('task','修改原任务')
    w.update({'turnId':'turn','requests':[request('a','question')]},hold_input=True)
    assert w.input_key=='task'
    w.reconcile_input(hold_input=True)
    assert w.input_key=='task'
    w.reconcile_input()
    assert w.input_key=='a' and w.drafts['task']=='修改原任务'
    assert w.submission('task') is None and w.submission('a') is None
    w.edit('a','原问题的回答')
    w.update({'turnId':'turn','requests':[request('b','question')]})
    assert w.input_key=='a' and w.input_request['expired']
    with pytest.raises(ValueError,match='已结束'):w.submission('a')
    w.edit('a','');w.reconcile_input()
    assert w.input_key=='b'
    w.edit('b','新问题的回答');w.sent('b')
    w.update({'turnId':'turn','requests':[]})
    assert w.input_key=='task' and w.drafts['task']=='修改原任务'
    assert w.submission('task')=={'type':'steer','targetTurn':'turn','text':'修改原任务'}


@pytest.mark.parametrize('next_turn',['turn','different',''])
def test_question_priority_preserves_task_draft_and_original_turn(next_turn):
    w=Workspace();w.update({'turnId':'turn'});w.edit('task','  改为20项\n保留中文🙂  ')
    w.update({'turnId':'turn','requests':[request('q','question')]})
    assert w.input_key=='q' and w.drafts.get('q','')==''
    w.edit('q','选第二个')
    assert w.submission('q')=={'type':'choose','token':'q','index':1}
    w.sent('q')
    assert w.input_request['submitting']
    w.update({'turnId':next_turn,'requests':[]})
    assert w.input_key=='task' and w.drafts['task']=='  改为20项\n保留中文🙂  '
    assert w.draft_turns['task']=='turn'
    if next_turn=='turn':
        assert w.submission('task')['type']=='steer'
    else:
        with pytest.raises(ValueError,match='原任务已结束'):w.submission('task')


@pytest.mark.parametrize('draft',['1','选第二个'])
def test_numbered_steer_draft_is_never_used_as_a_question_choice(draft):
    w=Workspace();w.update({'turnId':'turn'});w.edit('task',draft)
    w.update({'turnId':'turn','requests':[request('q','question')]})
    assert w.submission('q') is None and w.submission('task') is None
    w.edit('q','3')
    assert w.submission('q')=={'type':'choose','token':'q','index':2}
    w.sent('q');w.update({'turnId':'turn','requests':[]})
    assert w.submission('task')=={'type':'steer','targetTurn':'turn','text':draft}


@pytest.mark.parametrize('text,index', [('1',0),('２。',1),('一',0),('第一个',0),
    ('选第一个',0),('我选择第二项。',1),('帮我选第三个吧',2),('选项二',1),
    ('choose option two',1),('the first one',0),('始终允许',1)])
def test_spoken_choices_map_display_numbers_to_offered_indices(text,index):
    w=Workspace();card=request('q','question')
    for i,o in enumerate(card['options']):o['index']=i+7
    w.update({'requests':[card]});w.edit('q',text)
    assert w.submission('q') == {'type':'choose','token':'q','index':index+7}


@pytest.mark.parametrize('text',['使用 Safari 打开论文','选第一个，但是改成英文','不是1，是2','1或2都行','2026年写的论文'])
def test_complete_free_answers_are_never_truncated_to_choices(text):
    w=Workspace();w.update({'requests':[request('q','question')]});w.edit('q',text)
    assert w.submission('q') == {'type':'answer','questionToken':'q','text':text}


def test_invalid_or_disabled_number_keeps_draft_and_does_not_submit():
    w=Workspace();card=request('q','question');card['options'][1]['enabled']=False
    w.update({'requests':[card]})
    for text in ['2','选第十个','0']:
        w.edit('q',text)
        with pytest.raises(ValueError):w.submission('q')
        assert w.drafts['q']==text
    card['options']=[];w.update({'requests':[card]});w.edit('q','123')
    assert w.submission('q')['text']=='123'


def test_active_task_input_is_steer_bound_to_original_turn():
    w=Workspace();w.update({'turnId':'first'});w.edit('task','改为20项')
    assert w.submission('task') == {'type':'steer','targetTurn':'first','text':'改为20项'}
    w.update({'turnId':'second'})
    with pytest.raises(ValueError):w.submission('task')
    assert w.drafts['task']=='改为20项'


def test_permission_never_accepts_text_or_unoffered_default():
    w=Workspace();card=request('a');card['options']=card['options'][2:]
    w.update({'requests':[card]});w.edit('a','始终允许')
    assert w.selections['a']==2 and w.submission('a') is None
    assert w.confirm('a',1) is None
    w.update({'requests':[card],'paused':True})
    assert w.confirm('a') == {'type':'choose','token':'a','index':2}  # Legacy pause state no longer blocks input.


def test_receiving_state_does_not_mutate_server_options():
    original={'requests':[request('a')]};snapshot=deepcopy(original)
    w=Workspace();w.update(original);w.select('a',1)
    assert original==snapshot


@pytest.mark.parametrize('bounds',[(0,25,1440,850),(-1920,-200,1920,1050),
                                    (300,80,640,430),(0,0,320,240)])
@pytest.mark.parametrize('focused',['task','permission','question','last'])
def test_stack_keeps_task_and_focused_request_inside_every_display(bounds,focused):
    sizes={'task':(460,246),'permission':(460,288),'question':(460,360),'last':(460,360)}
    frames=stack_frames(sizes,bounds,focused)
    assert 'task' in frames and focused in frames
    bx,by,bw,bh=bounds
    previous_top=by+bh
    for x,y,w,h in frames.values():
        assert bx <= x < x+w <= bx+bw
        assert by <= y < y+h <= previous_top
        previous_top=y
    assert list(frames)==[key for key in sizes if key in frames]


@pytest.mark.parametrize('bounds',[(0,25,1440,850),(-1920,-200,1920,1050),(300,80,320,240)])
def test_every_intermediate_entrance_resize_and_exit_frame_stays_inside(bounds):
    bx,by,bw,bh=bounds
    targets=[(bx+bw-460-24,by+bh-246-24,460,246),
             (bx+bw-344-24,by+bh-92-24,344,92),
             (bx+bw-460-24,by-200,460,1200)]
    for start in targets:
        for end in targets:
            for frame in range(31):
                t=1-(1-frame/30)**3
                interpolated=tuple(round(a+(b-a)*t) for a,b in zip(start,end))
                x,y,w,h=bounded_frame(interpolated,bounds)
                assert bx+8 <= x < x+w <= bx+bw-8
                assert by+8 <= y < y+h <= by+bh-8


class FakeAX:
    def __init__(self):
        self.pid=10;self.nodes=['old'];self.activated=[];self.app=object()
    def front(self):return {'pid':self.pid}
    def apps(self):return {10:self.app}
    def application(self,pid):return pid
    def attr(self,app,name):return self.nodes[0] if self.nodes else None
    def windows(self,pid):return self.nodes
    def activate(self,app,node):self.activated.append((app,node));return True


def test_focus_restores_exact_window_without_guessing_replacement():
    ax=FakeAX();memory=AgentFocusMemory(ax)
    assert memory.capture(99)['status']=='remembered'
    ax.pid=99
    assert memory.capture(99)['status']=='unchanged'
    assert memory.restore()['status']=='restored'
    assert ax.activated==[(ax.app,'old')]
    ax.nodes=['same-title-but-new-window']
    assert memory.restore()['status']=='missing'
    assert len(ax.activated)==1


@pytest.mark.parametrize('method',['item/tool/call','item/tool/requestUserInput'])
def test_display_choice_reaches_server_with_actual_offered_answer(tmp_path,method):
    from proximic_ring.agent_workflow.agent.flow import Flow
    class Wire:
        def __init__(self): self.sent=[]
        def write(self,message): self.sent.append(message)
    flow=Flow(with_ring=False,runtime_dir=tmp_path);flow.rpc=Wire();flow.turn='turn'
    params=({'tool':'ask_ring_user','arguments':{'question':'使用哪个应用？','options':['Safari','Chrome']}}
            if method=='item/tool/call' else
            {'questions':[{'id':'app','question':'使用哪个应用？','options':[{'label':'Safari'},{'label':'Chrome'}]}]})
    try:
        flow.handle({'id':23,'method':method,'params':params})
        w=Workspace();w.update(flow.state);w.edit(w.input_key,'我选择第二项。')
        flow.command(w.submission(w.input_key))
        result=flow.rpc.sent[-1]['result']
        assert (result['contentItems'][0]['text'] if method=='item/tool/call'
                else result['answers']['app']['answers'][0])=='Chrome'
        assert flow.rpc.sent[-1]['id']==23 and flow.state['requests']==[]
    finally:flow.events.close()
