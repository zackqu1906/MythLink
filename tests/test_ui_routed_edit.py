"""Third mode through the real Qt controller, native protocol and history."""
from dataclasses import replace

import pytest
from test_inline_ui import inline_ui
from test_shared_voice_capture import raw_capture
from proximic_ring.text_processing.model import (INPUT_ROUTING_ROUTED_EDIT, TextProcessingResult,
                                                ROUTED_EDIT_SIX_CLASS, ROUTED_EDIT_BOUNDARY)
from proximic_ring.text_processing.task_routing import EditPlan


def begin_routed(controller,bridge,event,monkeypatch,*,ready=True):
    monkeypatch.setattr(controller._text_processing_worker,"warmup_router",lambda:pytest.fail("Clef warmup"))
    monkeypatch.setattr(controller._text_processing_worker,"capture_context",lambda target:raw_capture())
    controller.inputRoutingMode=INPUT_ROUTING_ROUTED_EDIT
    event(phase="dictated",ready=True,revision=1,raw="",error="")
    event("settled",phase="dictated",revision=1,text="")
    controller._apply_runtime_status("[ASR] START t=2.000s")
    begin=bridge.messages[-1]
    assert begin["type"]=="begin" and begin["routed_edit_mode"] and not begin["automatic_mode"]
    if ready:
        event(phase="listening",ready=True,revision=0,routed_edit_mode=True,routed_edit_version=1,
              original="明天三点开会",edit_source_text="明天三点开会",context_complete=True)
        controller._apply_runtime_session_started(2)


def test_three_mode_selector_persists_and_tap_dictates_without_routing(inline_ui,monkeypatch):
    from PySide6.QtCore import QObject
    controller,bridge,requests,root,event=inline_ui
    begin_routed(controller,bridge,event,monkeypatch)
    combo=root.findChild(QObject,"inputRoutingModeCombo")
    assert combo.property("count")==3 and combo.property("currentIndex")==2
    assert controller._settings.value("input/routingMode")==INPUT_ROUTING_ROUTED_EDIT
    assert controller._gesture_bindings.end[0]=="tap" and controller._gesture_bindings.switch_mode[0]=="swipe-right"
    controller._apply_runtime_update("把这句话当听写",False,"",2)
    controller._inline_input.finish()
    assert bridge.messages[-1]["type"]=="finish"
    controller._apply_runtime_update("把这句话当听写",True,"",2)
    assert bridge.messages[-1]["type"]=="update" and bridge.messages[-1]["final"]
    assert "automatic_task" not in bridge.messages[-1] and not requests


@pytest.mark.parametrize("source",["明天三点开会",""])
@pytest.mark.parametrize("variant",[ROUTED_EDIT_SIX_CLASS,ROUTED_EDIT_BOUNDARY])
def test_right_swipe_requests_one_routed_edit_and_result_uses_native_key(inline_ui,monkeypatch,source,variant):
    controller,bridge,requests,root,event=inline_ui
    controller.routedEditVariant=variant
    begin_routed(controller,bridge,event,monkeypatch)
    controller._apply_runtime_update("帮我写通知",False,"",2)
    event(phase="listening",ready=True,revision=0,has_composition=True,can_convert=True,edit_requested=False)
    controller._apply_voice_action("switch_mode")
    assert bridge.messages[-1]["type"]=="convert"
    event(phase="finishing",ready=True,revision=0,edit_requested=True)
    event("finish_audio")
    assert controller._finish_utterance_event.is_set()
    controller._apply_runtime_update("帮我写通知",True,"",2)
    assert not requests
    # Changing future mode must not change this utterance's request policy.
    controller.inputRoutingMode="manual"
    controller.routedEditVariant=ROUTED_EDIT_SIX_CLASS if variant==ROUTED_EDIT_BOUNDARY else ROUTED_EDIT_BOUNDARY
    event(phase="editing",ready=True,revision=1,edit_requested=True,context_complete=True)
    event("edit_requested",revision=1,instruction="帮我写通知",original=source)
    event("edit_requested",revision=1,instruction="帮我写通知",original=source)
    assert len(requests)==1
    request=requests[0]
    assert request.routing_mode==INPUT_ROUTING_ROUTED_EDIT and request.target_text==source
    assert request.routed_edit_variant==variant
    assert request.screen_context is controller._voice_capture["model_context"]
    assert request.screen_context is not controller._voice_capture["future"]
    assert request.task_type=="" and request.edit_plan is None
    result=TextProcessingResult(request.request_id,2,"edit",request.raw_text,"明天开会。",.1,True,
        target_text=source,routing_mode=INPUT_ROUTING_ROUTED_EDIT,task_type="edit",
        edit_plan=None if variant==ROUTED_EDIT_BOUNDARY else EditPlan(("compose",)),routed_edit_variant=variant)
    controller._apply_text_processed(result)
    assert bridge.messages[-1]["type"]=="edit_result" and bridge.messages[-1]["text"]=="明天开会。"
    assert bridge.messages[-1]["revision"]==1
    count=len(bridge.messages);controller._apply_text_processed(result)
    assert len(bridge.messages)==count


def test_missing_native_source_never_submits_routed_request(inline_ui,monkeypatch):
    controller,bridge,requests,root,event=inline_ui
    begin_routed(controller,bridge,event,monkeypatch)
    event(phase="editing",ready=True,revision=1,edit_requested=True,context_complete=False)
    event("edit_requested",revision=1,instruction="写通知",original="",edit_context_available=False)
    assert not requests


def test_old_helper_is_rejected_before_routed_audio_begins(inline_ui,monkeypatch):
    controller,bridge,requests,root,event=inline_ui
    begin_routed(controller,bridge,event,monkeypatch,ready=False)
    event(phase="listening",ready=True,revision=0,context_complete=True)
    assert "更新输入法组件" in controller._inline_input.error
    assert not requests


def test_cancelled_routed_response_cannot_write_to_new_native_revision(inline_ui,monkeypatch):
    controller,bridge,requests,root,event=inline_ui
    begin_routed(controller,bridge,event,monkeypatch)
    event(phase="editing",ready=True,revision=1,edit_requested=True,context_complete=True)
    event("edit_requested",revision=1,instruction="写通知",original="原文")
    request=requests[0]
    controller._cancel_inline_requests("本句取消")
    count=len(bridge.messages)
    controller._apply_text_processed(TextProcessingResult(request.request_id,2,"edit","写通知","迟到正文",.1,True,
        routing_mode=INPUT_ROUTING_ROUTED_EDIT))
    assert len(bridge.messages)==count


def test_mode_switch_during_pending_begin_applies_only_to_next_sentence(inline_ui,monkeypatch):
    controller,bridge,requests,root,event=inline_ui
    begin_routed(controller,bridge,event,monkeypatch,ready=False)
    controller.inputRoutingMode="manual"
    event(phase="listening",ready=True,revision=0,routed_edit_mode=True,routed_edit_version=1,context_complete=True)
    assert controller._inline_input.session_routing_mode==INPUT_ROUTING_ROUTED_EDIT
