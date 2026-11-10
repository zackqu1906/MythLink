"""Release and runtime contracts for macOS 13; no user desktop data is read."""
import importlib.util
from pathlib import Path
import plistlib
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from proximic_ring.platforms.macos.macho import inspect_macho, version_tuple
from proximic_ring.platforms.macos import screen_capture


def binary(minimum=(13,0,0), *, legacy=False, cpu=0x100000c, dependency=None):
    version=(minimum[0]<<16)|(minimum[1]<<8)|minimum[2]
    commands=struct.pack('<IIII',0x24,16,version,version) if legacy else struct.pack('<IIIIII',0x32,24,1,version,version,0)
    count=1
    if dependency:
        name=dependency.encode()+b'\0';length=(24+len(name)+7)//8*8
        commands+=struct.pack('<IIIIII',0xc,length,24,0,0,0)+name+b'\0'*(length-24-len(name))
        count+=1
    return struct.pack('<IIIIIIII',0xfeedfacf,cpu,0,2,count,len(commands),0,0)+commands


@pytest.mark.parametrize('legacy',[True,False])
def test_reads_both_deployment_commands_and_normalizes_versions(tmp_path,legacy):
    path=tmp_path/'binary';path.write_bytes(binary((13,1,2),legacy=legacy,dependency='@rpath/libx.dylib'))
    record,=inspect_macho(path)
    assert record['minimum_macos']=='13.1.2'
    assert record['dependencies']==['@rpath/libx.dylib']
    assert version_tuple('13')==version_tuple('13.0')==version_tuple('13.0.0')


def test_universal_binary_checks_each_architecture(tmp_path):
    intel=binary((14,0,0),cpu=0x1000007);arm=binary()
    header=struct.pack('>II',0xcafebabe,2)
    header+=struct.pack('>IIIII',0x1000007,0,48,len(intel),0)
    header+=struct.pack('>IIIII',0x100000c,0,48+len(intel),len(arm),0)
    path=tmp_path/'fat';path.write_bytes(header+intel+arm)
    assert [(r['architecture'],r['minimum_macos']) for r in inspect_macho(path)]==[
        ('x86_64','14.0.0'),('arm64','13.0.0')]
    path.write_bytes(header+intel+arm[:-3])
    with pytest.raises(ValueError,match='Truncated'):inspect_macho(path)


def verifier():
    path=Path(__file__).resolve().parents[1]/'tools/verify_macos_bundle.py'
    spec=importlib.util.spec_from_file_location('compatibility_verifier',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_packaging_uses_selected_standalone_python_for_the_target(tmp_path, monkeypatch):
    tools_path = Path(__file__).resolve().parents[1] / 'tools'
    monkeypatch.syspath_prepend(str(tools_path))
    import verify_macos_python as preflight

    python = tmp_path / 'python.exe'
    python.touch(mode=0o755)
    monkeypatch.setattr(preflight.sys, '_base_executable', str(python))
    monkeypatch.setattr(preflight.sys, 'version_info', (3, 11))
    config = {'LDLIBRARY': 'libpython3.11.dylib', 'LIBDIR': str(tmp_path)}
    monkeypatch.setattr(preflight.sysconfig, 'get_config_var', config.get)
    library = tmp_path / config['LDLIBRARY']
    library.write_bytes(binary((11, 0, 0)))
    preflight.verify_python(str(python), '13.3')

    other = tmp_path / 'other-python.exe'
    other.touch(mode=0o755)
    with pytest.raises(SystemExit, match='Recreate'):
        preflight.verify_python(str(other), '13.3')
    library.write_bytes(binary((15, 0, 0)))
    with pytest.raises(SystemExit, match='macOS 13.3'):
        preflight.verify_python(str(python), '13.3')
    config['LDLIBRARY'] = 'Python.framework/Versions/3.11/Python'
    with pytest.raises(SystemExit, match='standalone CPython'):
        preflight.verify_python(str(python), '13.3')


def test_release_gate_rejects_nested_bundle_old_style_load_command_and_foreign_library(tmp_path):
    app=tmp_path/'Mythlink.app';contents=app/'Contents'
    (contents/'MacOS').mkdir(parents=True);(contents/'Resources').mkdir()
    (contents/'Info.plist').write_bytes(plistlib.dumps({'LSUIElement':True,'LSMinimumSystemVersion':'13.0'}))
    (contents/'MacOS/Mythlink').write_bytes(binary())
    assert verifier().verify_bundle(app,'13.0')==[]
    helper=contents/'Helpers/Future.app/Contents';helper.mkdir(parents=True)
    (helper/'Info.plist').write_bytes(plistlib.dumps({'LSMinimumSystemVersion':'15.0'}))
    (contents/'Resources/late.dylib').write_bytes(binary((13,1,0),legacy=True,dependency='/opt/homebrew/lib/libx.dylib'))
    errors=verifier().verify_bundle(app,'13.0')
    assert any('bundle requires macOS 15.0' in e for e in errors)
    assert any('requires macOS 13.1.0' in e for e in errors)
    assert any('/opt/homebrew/' in e for e in errors)


@pytest.mark.parametrize('available', [(),('setPreservesAspectRatio_',),
    ('setPreservesAspectRatio_','setIgnoreShadowsSingleWindow_','setIncludeChildWindows_','setCaptureMicrophone_')])
def test_preview_works_when_newer_selectors_do_not_exist(available):
    calls=[]
    config=SimpleNamespace(setCapturesAudio_=lambda value:calls.append(('audio',value)))
    for name in available:setattr(config,name,lambda value,name=name:calls.append((name,value)))
    screen_capture.configure_window_preview(config)
    assert calls[0]==('audio',False)
    assert len(calls)==1+len(available)
    assert all(not value for name,value in calls if name in ('audio','setCaptureMicrophone_','setIncludeChildWindows_'))
    assert screen_capture.screenshot_manager(SimpleNamespace()) is None


def test_legacy_screen_excludes_agent_and_retains_window_order():
    calls=[]
    cg=SimpleNamespace(kCGWindowListOptionOnScreenOnly=1,kCGWindowNumber='id',kCGWindowImageBestResolution=8,
        CGWindowListCopyWindowInfo=lambda *_:[{'id':9},{'id':4},{'id':2}],
        CGRectMake=lambda *args:args,
        CGWindowListCreateImageFromArray=lambda *args:calls.append(args) or 'image')
    assert screen_capture.legacy_screen_image(cg,(-800,0,800,600),{9})=='image'
    assert calls==[((-800,0,800,600),[4,2],8)]


def test_cancelled_or_late_capture_cannot_be_returned():
    cancel=threading.Event();cancel.set()
    with pytest.raises(InterruptedError):screen_capture.check_capture(cancel,time.monotonic()+10)
    cancel.clear()
    with pytest.raises(TimeoutError):screen_capture.check_capture(cancel,time.monotonic()-1)


def test_node_selection_prefers_compatible_bundled_runtime(tmp_path,monkeypatch):
    import platform
    from proximic_ring import runtime_paths
    from proximic_ring.agent_workflow.agent.background_service import node_path
    monkeypatch.setattr(runtime_paths,'resource_root',lambda:tmp_path)
    monkeypatch.setattr(runtime_paths,'is_frozen',lambda:True)
    monkeypatch.setattr(platform,'system',lambda:'Darwin')
    monkeypatch.setattr(platform,'machine',lambda:'arm64')
    monkeypatch.setattr(platform,'mac_ver',lambda:('13.0',(),''))
    external=tmp_path/'ChatGPT.app/Contents/Resources/cua_node/bin/node'
    external.parent.mkdir(parents=True);external.write_bytes(binary((13,5,0)))
    codex=str(tmp_path/'ChatGPT.app/Contents/Resources/codex')
    with pytest.raises(RuntimeError,match='兼容当前系统'):node_path(codex)
    bundled=tmp_path/'node/bin/node';bundled.parent.mkdir(parents=True);bundled.write_bytes(binary((11,0,0)))
    assert node_path(codex)==bundled
