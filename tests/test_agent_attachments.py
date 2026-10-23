"""Mixed attachment inputs must survive draft routing and reach the right turn."""
import json
from pathlib import Path

import pytest

from proximic_ring.agent_workflow.agent.attachments import local_attachment, turn_input
from proximic_ring.agent_workflow.agent.flow import Flow
from proximic_ring.agent_workflow.agent.workspace import Workspace
from test_agent_workspace import request
from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice
from test_ui_shell import visual_child


@pytest.fixture
def attachments(tmp_path):
    from PySide6.QtGui import QImage, QColor
    image = QImage(80, 60, QImage.Format_RGB32)
    image.fill(QColor('#5D93C9'))
    png = tmp_path/'参考图.png'
    assert image.save(str(png))
    document = tmp_path/'需求 "草稿".txt'
    document.write_text('保留标题，按参考图修改。')
    return [local_attachment(png), local_attachment(document)]


class Wire:
    def __init__(self): self.sent = []
    def call(self, method, params, **_):
        assert method == 'mcpServer/tool/call' and params['tool'] in {'turn_ended','js_reset'}
        return {'content': []}
    def send(self, method, params):
        self.sent.append((method, params))
        return len(self.sent)


@pytest.mark.parametrize('mode', ['new', 'steer', 'queue'])
def test_semantic_context_reaches_background_turn_without_eager_image(tmp_path,mode):
    from proximic_ring.agent_workflow.agent.background_flow import BackgroundFlow
    flow=BackgroundFlow(with_ring=False,runtime_dir=tmp_path/'flow')
    flow.rpc=Wire();flow.tid='thread'
    if mode!='new':flow.turn='running'
    context={'visual_context':{'availability':'captured','path':'/tmp/send-time.png',
                             'window':{'window_id':11},'requested_at':'submit-time'}}
    try:
        flow.command({'type':'steer' if mode=='steer' else 'text',
                      'text':'看一下这些公式','targetTurn':'running','context':context})
        if mode=='queue':
            context['visual_context']['path']='/tmp/later-window.png'
            assert not flow.rpc.sent
            flow.handle({'method':'turn/completed','params':{'turn':{'id':'running','status':'completed'}}})
        method,params=flow.rpc.sent[0]
        assert method==('turn/steer' if mode=='steer' else 'turn/start')
        assert params['input'][0]['text']=='看一下这些公式'
        assert all(item['type']=='text' for item in params['input'])
        instruction=params['input'][-1]['text']
        assert 'view_image' in instruction and '/tmp/send-time.png' in instruction
        assert '/tmp/later-window.png' not in instruction
        assert json.loads(instruction.rsplit('\n',1)[1])['window']=={'window_id':11}
    finally:
        flow.events.close()


@pytest.mark.parametrize('mode', ['new', 'steer', 'queue'])
def test_mixed_input_reaches_start_steer_and_queued_turn(tmp_path, attachments, mode):
    flow = Flow(with_ring=False, runtime_dir=tmp_path/'flow')
    flow.rpc = Wire(); flow.tid = 'thread'
    if mode != 'new': flow.turn = 'running'
    try:
        flow.command({'type': 'steer' if mode == 'steer' else 'text',
                      'text': '根据图片修改文件', 'targetTurn': 'running', 'attachments': attachments})
        if mode == 'queue':
            attachments.clear()  # The queued message owns its attachment list.
            assert not flow.rpc.sent
            flow.handle({'method': 'turn/completed', 'params': {'turn': {'id': 'running', 'status': 'completed'}}})
        method, params = flow.rpc.sent[0]
        assert method == ('turn/steer' if mode == 'steer' else 'turn/start')
        assert params['input'][0] == {'type': 'text', 'text': '根据图片修改文件'}
        assert params['input'][1] == {'type': 'localImage', 'path': str(tmp_path/'参考图.png')}
        files = json.loads(params['input'][2]['text'].split('\n', 1)[1])
        assert files == [{'name': '需求 "草稿".txt', 'path': str(tmp_path/'需求 "草稿".txt')}]
        if mode == 'steer': assert params['expectedTurnId'] == 'running'
    finally:
        flow.events.close()


def test_attachment_only_and_control_word_with_attachment_are_messages(tmp_path, attachments):
    flow = Flow(with_ring=False, runtime_dir=tmp_path/'flow'); flow.rpc = Wire(); flow.tid = 'thread'
    try:
        flow.text('', attachments=attachments)
        assert flow.rpc.sent[0][1]['input'][0]['type'] == 'localImage'
        flow.start_id = None; flow.turn = 'running'
        flow.text('取消', mode='steer', target_turn='running', attachments=attachments)
        assert flow.rpc.sent[-1][0] == 'turn/steer' and not flow.cancelled
    finally:
        flow.events.close()


def test_attachments_park_with_task_and_keep_original_turn(attachments):
    workspace = Workspace(); workspace.update({'turnId': 'original'})
    workspace.attach('task', attachments)
    workspace.attach('task', attachments)
    assert len(workspace.attachments['task']) == 2
    workspace.update({'turnId': 'original', 'requests': [request('question', 'question')]})
    workspace.edit('question', '第一项')
    assert 'attachments' not in workspace.submission('question')
    workspace.sent('question'); workspace.update({'turnId': 'next'})
    workspace.edit('task', '一起使用')
    with pytest.raises(ValueError, match='原任务已结束'):
        workspace.submission('task')
    workspace.update({'turnId': 'original'})
    command = workspace.submission('task')
    workspace.sent('task')
    assert len(command['attachments']) == 2 and not workspace.attachments
    assert workspace.submission('task') is None


def test_attachment_only_removal_and_missing_paths(attachments, tmp_path):
    workspace = Workspace(); workspace.update({})
    workspace.attach('task', attachments)
    assert workspace.submission('task')['text'] == ''
    for item in attachments: workspace.remove_attachment('task', item['path'])
    assert workspace.submission('task') is None and not workspace.draft_turns
    with pytest.raises(ValueError, match='无法读取附件'): local_attachment(tmp_path)
    with pytest.raises(ValueError, match='无法读取附件'): local_attachment(tmp_path/'missing')
    assert turn_input('原有文字') == [{'type': 'text', 'text': '原有文字'}]


def test_composer_picker_preview_remove_paste_and_mixed_send(inline_ui, monkeypatch, tmp_path, attachments):
    from PySide6.QtCore import QCoreApplication, QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QGuiApplication, QImage, QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QFileDialog
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    from proximic_ring.ui.agent import agent_controller as module
    owner = inline_ui[0]; agent = owner.agent; sent = []
    finish_fixture_voice(owner)
    monkeypatch.setattr(agent, '_send', sent.append)
    monkeypatch.setattr(module, 'cache_root', lambda: tmp_path/'cache')
    agent._visible = agent._focused = True
    overlay = AgentOverlay(agent, owner); overlay.reduce_motion = True
    try:
        overlay.focus('task'); QTest.qWait(40)
        window = overlay.windows['task']; root = window.rootObject()
        editor = visual_child(root, 'agentEditor')
        button = visual_child(root, 'agentSendButton')
        def click(name):
            item = visual_child(root, name)
            assert item.isVisible() and item.isEnabled()
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point); QTest.qWait(40)
        def picker(*_):
            assert agent.pickingAttachments
            overlay._focus_changed(None)
            assert agent.focused
            return [item['path'] for item in attachments], ''
        monkeypatch.setattr(QFileDialog, 'getOpenFileNames', picker)
        assert visual_child(root, 'agentAttachButton') is None
        agent.chooseAttachments('task'); QTest.qWait(40)
        assert len(agent.inputAttachments) == 2 and button.isEnabled()
        assert visual_child(root, 'agentAttachmentPreview0').property('paintedWidth') > 0
        assert window.grabWindow().save('/tmp/agent-attachments-preview.png')
        click('agentRemoveAttachment0')
        assert len(agent.inputAttachments) == 1
        QGuiApplication.clipboard().setImage(QImage(attachments[0]['path']))
        editor.forceActiveFocus()
        # Qt maps Control to Command for StandardKey.Paste on macOS.
        QTest.keyClick(window, Qt.Key_V, Qt.ControlModifier); QTest.qWait(50)
        assert len(agent.inputAttachments) == 2
        pasted = agent.inputAttachments[-1]['path']
        assert Path(pasted).is_file()
        editor.setProperty('text', '按图片整理文档')
        click('agentSendButton')
        assert sent[-1]['text'] == '按图片整理文档'
        assert [item['kind'] for item in sent[-1]['attachments']] == ['file', 'image']
        assert editor.property('text') == '' and not agent.inputAttachments
        assert Path(pasted).is_file()  # The transport still needs the clipboard image.
        mime = QMimeData()
        mime.setUrls([QUrl(item['url']) for item in attachments])
        enter = QDragEnterEvent(QPoint(100, 35), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(window, enter)
        assert enter.isAccepted()
        drop = QDropEvent(QPointF(100, 35), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(window, drop); QTest.qWait(40)
        assert drop.isAccepted() and len(agent.inputAttachments) == 2
        editor.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Return); QTest.qWait(40)
        assert sent[-1]['text'] == '' and len(sent[-1]['attachments']) == 2
        assert not agent.inputAttachments
        QGuiApplication.clipboard().setText('普通文字仍然可以粘贴')
        QTest.keyClick(window, Qt.Key_V, Qt.ControlModifier); QTest.qWait(40)
        assert editor.property('text') == '普通文字仍然可以粘贴'
    finally:
        QGuiApplication.clipboard().clear()
        overlay.close()


def test_attachment_validation_preserves_draft_and_question_routing(inline_ui, monkeypatch, attachments):
    from PySide6.QtCore import QUrl
    owner = inline_ui[0]; agent = owner.agent; sent = []
    finish_fixture_voice(owner)
    monkeypatch.setattr(agent, '_send', sent.append)
    assert agent.addAttachmentUrls('task', [QUrl(attachments[0]['url'])])
    agent.edit('task', '处理这张图')
    Path(attachments[0]['path']).unlink()
    agent.submit('task')
    assert not sent and agent.draft('task') == '处理这张图' and agent.inputAttachments
    assert '无法读取附件' in agent.errorMessage
    agent._apply_state({'turnId': 'turn', 'requests': [request('q', 'question')]})
    assert agent.inputKey == 'q' and agent.inputAttachments == []
    assert not agent.addAttachmentUrls('q', [QUrl(attachments[1]['url'])])
    assert len(agent.workspace.attachments['task']) == 1


def test_picker_finishing_after_turn_end_preserves_attachments_for_review(inline_ui, monkeypatch, attachments):
    from PySide6.QtWidgets import QFileDialog
    owner = inline_ui[0]; agent = owner.agent; sent = []
    finish_fixture_voice(owner)
    monkeypatch.setattr(agent, '_send', sent.append)
    agent._apply_state({'turnId': 'old'})
    def picker(*_):
        agent._apply_state({'turnId': 'new'})
        return [attachments[0]['path']], ''
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', picker)
    agent.chooseAttachments('task')
    agent.submit('task')
    assert not sent and not agent.errorMessage and agent.inputAttachments
    assert inline_ui[1].messages[-1]['type'] == 'input_notice'
    assert '原任务已结束' in inline_ui[1].messages[-1]['text']
    monkeypatch.setattr(agent, '_send', lambda _: False)
    agent.submit('task')
    assert agent.inputAttachments  # A failed local send does not clear the draft.
