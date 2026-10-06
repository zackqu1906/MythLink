"""Shared native overlay policy without desktop input or application activation."""
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QObject, QCoreApplication, Signal
from proximic_ring.ui import overlay_stacking as module


def window(pid=5,layer=0,width=800,height=600,alpha=1,x=0,y=0):
    return dict(kCGWindowOwnerPID=pid,kCGWindowLayer=layer,kCGWindowAlpha=alpha,
                kCGWindowBounds=dict(X=x,Y=y,Width=width,Height=height))


def test_fullscreen_canvas_wins_over_toolbar_and_other_apps_on_second_monitor():
    canvas=window(layer=500,x=-1920,width=1920,height=1080)
    windows=[window(pid=99,layer=999), window(width=52,height=20),window(alpha=0),canvas,window()]
    assert module.content_window(windows,5) is canvas
    assert module.content_window(windows,12) is None
    assert module.content_window([window(width=20,height=20)],5) is None


@pytest.mark.parametrize('level,expected',[(0,102),(300,301),(1000,999)])
def test_native_panel_joins_fullscreen_without_activation_or_secure_window_level(level,expected):
    names=['CanJoinAllSpaces','MoveToActiveSpace','Managed','Transient','Stationary',
        'ParticipatesInCycle','IgnoresCycle','FullScreenPrimary','FullScreenAuxiliary',
        'FullScreenNone','FullScreenAllowsTiling','FullScreenDisallowsTiling','Primary',
        'Auxiliary','CanJoinAllApplications']
    flags={name:1<<i for i,name in enumerate(names)}
    kit=SimpleNamespace(**{'NSWindowCollectionBehavior'+n:v for n,v in flags.items()},
                        NSWindowStyleMaskNonactivatingPanel=128,NSPopUpMenuWindowLevel=101,NSScreenSaverWindowLevel=1000)
    calls=[]
    native=SimpleNamespace(collectionBehavior=lambda:sum(flags.values()),styleMask=lambda:14,
        setStyleMask_=lambda v:calls.append(('style',v)),setCollectionBehavior_=lambda v:calls.append(('behavior',v)),
        setLevel_=lambda v:calls.append(('level',v)),setHidesOnDeactivate_=lambda v:calls.append(('hides',v)),
        orderFrontRegardless=lambda:calls.append(('front',)),
        makeKeyAndOrderFront_=lambda _:pytest.fail('must not take focus'))
    module.configure_native_overlay(native,kit,content_level=level)
    assert calls[0]==('style',142)
    assert calls[1]==('behavior',sum(flags[n] for n in ['CanJoinAllSpaces','Transient','IgnoresCycle',
                                    'FullScreenAuxiliary','FullScreenDisallowsTiling','CanJoinAllApplications']))
    assert calls[2:]==[('level',expected),('hides',False),('front',)]


def test_visibility_guard_reorders_only_visible_windows_and_never_extends_lifetime(monkeypatch):
    app=QCoreApplication.instance() or QCoreApplication([])
    from proximic_ring.ui import notifications
    class Window(QObject):
        visibleChanged=Signal(bool)
        visible=False
        def isVisible(self):return self.visible
        def set_visible(self,value):self.visible=value;self.visibleChanged.emit(value)
        def show(self):pytest.fail('guard must not show or reactivate a window')
    window=Window();calls=[];errors=[]
    monkeypatch.setattr(module,'QGuiApplication',SimpleNamespace(platformName=lambda:'cocoa'))
    monkeypatch.setattr(notifications,'_show_on_macos_spaces',lambda w:calls.append(w))
    guard=module.OverlayVisibilityGuard(window,diagnostic=errors.append)
    guard.refresh();assert not calls and not guard._timer.isActive()
    window.set_visible(True);assert guard._timer.isActive()
    guard.refresh();assert calls==[window]
    def fail(_):raise RuntimeError('unavailable')
    monkeypatch.setattr(notifications,'_show_on_macos_spaces',fail)
    guard.refresh();guard.refresh();assert len(errors)==1
    window.set_visible(False);guard.refresh()
    assert not guard._timer.isActive() and len(calls)==1
    window.set_visible(True);guard.refresh();assert len(errors)==2
    window.set_visible(False)
