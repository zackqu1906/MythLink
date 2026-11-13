"""Selectable variant, persistence, active-session isolation and exact retry."""
from dataclasses import replace
import pytest
from test_inline_ui import inline_ui, _recording_test_begin_edit
from proximic_ring.text_processing.model import ROUTED_EDIT_BOUNDARY,ROUTED_EDIT_SIX_CLASS,TextProcessingResult

@pytest.mark.parametrize('inline_ui,expected',[
    ({},ROUTED_EDIT_SIX_CLASS),
    ({'input/routedEditVariant':ROUTED_EDIT_BOUNDARY},ROUTED_EDIT_BOUNDARY),
    ({'input/routedEditVariant':ROUTED_EDIT_SIX_CLASS},ROUTED_EDIT_SIX_CLASS),
    ({'input/routedEditVariant':'unknown-future-version'},ROUTED_EDIT_SIX_CLASS),
],indirect=['inline_ui'])
def test_saved_or_missing_variant_loads_without_changing_mode(inline_ui,expected):
    from PySide6.QtCore import QObject
    c,_,_,root,_=inline_ui
    assert c.routedEditVariant==expected and c.inputRoutingMode=='manual'
    combo=root.findChild(QObject,'routedEditVariantCombo')
    assert combo.property('count')==2
    assert combo.property('currentIndex')==(1 if expected==ROUTED_EDIT_BOUNDARY else 0)

def test_settings_combo_changes_persistent_variant_and_renders(inline_ui,monkeypatch):
    from PySide6.QtCore import QObject,QMetaObject,Q_ARG,QSettings,QPointF
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    c,_,_,root,event=inline_ui
    event(phase='idle',ready=True)
    monkeypatch.setattr(c._text_processing_worker,'warmup_router',lambda:None)
    c.inputRoutingMode='manual_routed_edit'
    root.resize(1200,780);root.show()
    QMetaObject.invokeMethod(root,'showSettings',Q_ARG('QVariant',0))
    QMetaObject.invokeMethod(root.findChild(QObject,'settingsAudioEntry'),'clicked')
    QTest.qWait(100)
    row=root.findChild(QQuickItem,'routedEditVariantRow')
    combo=root.findChild(QObject,'routedEditVariantCombo')
    assert row.isVisible()
    for index,value in [(1,ROUTED_EDIT_BOUNDARY),(0,ROUTED_EDIT_SIX_CLASS),(1,ROUTED_EDIT_BOUNDARY)]:
        combo.setProperty('currentIndex',index)
        assert QMetaObject.invokeMethod(combo,'activated',Q_ARG(int,index))
        assert c.routedEditVariant==value
        c._settings.sync()
        assert QSettings(c._settings.fileName(),QSettings.IniFormat).value('input/routedEditVariant')==value
    scroll=root.findChild(QQuickItem,'runtimeSettingsScroll');flick=scroll.property('contentItem')
    y=row.mapToItem(scroll,QPointF()).y()
    flick.setProperty('contentY',max(0,min(flick.property('contentY')+y-120,flick.property('contentHeight')-flick.height())))
    QTest.qWait(100)
    assert row.width()>300 and combo.property('width')>150
    assert root.grabWindow().save('/private/tmp/boundary-edit-settings.png')
    for mode in ['manual','auto']:
        c.inputRoutingMode=mode;QTest.qWait(20);assert not row.isVisible()
    c.inputRoutingMode='manual_routed_edit';QTest.qWait(20)
    assert row.isVisible() and c.routedEditVariant==ROUTED_EDIT_BOUNDARY

@pytest.mark.parametrize('variant',[ROUTED_EDIT_BOUNDARY,ROUTED_EDIT_SIX_CLASS])
def test_reexecution_uses_original_variant_after_setting_changes(inline_ui,monkeypatch,variant):
    c,_,requests,_,event=inline_ui
    context={'status':'ok','context':'当时上文'}
    monkeypatch.setattr(c,'_inline_edit_options',lambda _:dict(routing_mode='manual_routed_edit',
        routed_edit_variant=variant,screen_context=context))
    first=_recording_test_begin_edit(c,event,requests)
    c._apply_text_processed(TextProcessingResult(first.request_id,1,'edit','扩写','结果',.1,True,
        routing_mode='manual_routed_edit',routed_edit_variant=variant,model_input={'screen_context':context}))
    source=c._inline_input._utterance_id
    c.routedEditVariant=ROUTED_EDIT_SIX_CLASS if variant==ROUTED_EDIT_BOUNDARY else ROUTED_EDIT_BOUNDARY
    monkeypatch.setattr(c,'_inline_edit_options',lambda _:pytest.fail('Retry used current selection'))
    event(phase='editing',ready=True,revision=4,edit_requested=True,retry_source_utterance_id=source)
    event('edit_requested',revision=4,instruction='扩写',original='原文',source_utterance_id=source,retry_edit=True)
    assert len(requests)==2
    retried=requests[-1]
    assert retried.routed_edit_variant==variant and retried.reexecuted
    assert replace(retried,request_id=first.request_id,screen_context=context,reexecuted=False)==first
