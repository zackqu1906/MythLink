"""The SDK example keeps the Mac callback contract when adding Windows output."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from ring_python_sdk.touchpad import TouchpadMove, TouchpadClick, TouchpadContact


@pytest.mark.parametrize('system', ['darwin', 'win32'])
def test_example_routes_mouse_events_by_platform(system, monkeypatch):
    path = Path(__file__).resolve().parents[1]/'examples/ring_touchpad.py'
    spec = importlib.util.spec_from_file_location('platform_example', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform=system))
    seen, starts = [], []
    class Mouse:
        def permitted(self): return True
        def enable(self): pass
        def disable(self): pass
        def handle(self, event): seen.append(event.kind)
    import ring_python_sdk.touchpad.macos as mac
    import ring_python_sdk.touchpad.windows as win
    monkeypatch.setattr(mac, 'MacSystemMouse', Mouse)
    monkeypatch.setattr(win, 'WindowsSystemMouse', Mouse)
    class Session:
        def __init__(self, *args): pass
        async def connect_target(self, target): return True
        async def touchpad_on(self, **options):
            starts.append(options)
            movement = TouchpadMove(1, 2, .9, 1, 0.)
            if 'on_pointer_move' in options: options['on_pointer_move'](movement)
            for event in (movement, TouchpadClick(2, 0.), TouchpadContact('reset', 0, 0.)):
                options['on_event'](event)
            options['on_stopped'](None)
        async def disconnect(self): pass
    monkeypatch.setattr(module, 'RingSession', Session)
    asyncio.run(module.run(SimpleNamespace(mouse=True, device='fake', connect_timeout=1., seconds=1.)))
    assert ('on_pointer_move' in starts[0]) == (system == 'win32')
    assert seen == (['move', 'click'] if system == 'win32' else ['move', 'click', 'contact'])
