from dataclasses import replace
import pytest

pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from PySide6.QtWidgets import QApplication
from test_app_gestures import route
from test_ring_gestures import request
from test_gesture_ownership import outside
from test_application_menus import configure,BUNDLE,ACTION
from proximic_ring.gestures.gesture_settings import GlobalGestureBindings,GLOBAL_BINDINGS_KEY


@pytest.fixture(scope='session',autouse=True)
def widget_application():
    app=QApplication.instance() or QApplication(['recommendation-gestures','-platform','offscreen'])
    yield app


def test_default_recommendation_gesture_uses_normal_global_route(route,monkeypatch):
    owner,service,inline,backend,sent,_,_=route
    outside(backend);calls=[]
    monkeypatch.setattr(owner.agent.recommendations,'open',calls.append)
    request(owner,'double-index-pinch')
    assert calls==['gesture'] and not sent


def test_app_override_and_global_rebinding_keep_existing_priority(route,monkeypatch):
    owner,service,inline,backend,sent,_,_=route
    calls=[];monkeypatch.setattr(owner.agent.recommendations,'open',calls.append)
    catalog=configure(service,monkeypatch)
    assert catalog.setBinding(BUNDLE,'double-index-pinch',ACTION['id'])
    outside(backend);request(owner,'double-index-pinch')
    assert not calls and sent
    assert owner.ringGestures.setGlobalBinding('workflow_recommendations','snap')
    assert GlobalGestureBindings.from_json(owner._settings.value(GLOBAL_BINDINGS_KEY)).workflow_recommendations=='snap'
    request(owner,'snap')
    assert calls==['gesture']
