"""First-use setup, download integrity and frozen worker routing; no paid calls."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from proximic_ring.agent_workflow.operations import embedding_install as installer
from proximic_ring.agent_workflow.operations import operation_embeddings as embeddings
from proximic_ring.agent_workflow.extraction.workflow_model import configuration_error
from proximic_ring.text_processing.model import LLMSettings
from test_inline_ui import inline_ui


@pytest.fixture
def model(tmp_path,monkeypatch):
    data=b'example-model-bytes'
    row=dict(path='weights.bin',size=len(data),sha256=hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(installer,'manifest',lambda:dict(files=[row]))
    monkeypatch.setenv('PROXIMIC_EMBEDDING_RUNTIME',str(tmp_path/'runtime'))
    return data,row,embeddings.model_directory()


class Response(io.BytesIO):
    def __init__(self,data,status=200,headers=None):
        super().__init__(data);self.status=status;self.headers=headers or {}
    def read(self,n=-1):return super().read(min(n,3))


def test_cancel_and_resume_are_hash_verified_before_ready(model):
    data,row,root=model;cancel=[False];calls=[]
    def opened(request,**kw):
        calls.append(request)
        start=int(request.headers.get('Range','bytes=0-')[6:-1])
        return Response(data[start:],206 if start else 200,
            {'Content-Range':f'bytes {start}-{len(data)-1}/{len(data)}'})
    def progress(label,n,total):
        if label=='正在下载召回模型' and n>=3:cancel[0]=True
    with pytest.raises(installer.InstallCancelled):
        installer.install(directory=root,progress=progress,cancelled=lambda:cancel[0],opener=opened)
    assert not installer.installed(root) and (root/'weights.bin.part').read_bytes()==data[:3]
    cancel[0]=False
    installer.install(directory=root,opener=opened)
    assert calls[-1].headers['Range']=='bytes=3-' and installer.installed(root)
    assert not (root/'weights.bin.part').exists()
    assert all(r.full_url.startswith(f'https://huggingface.co/{embeddings.MODEL}/resolve/{embeddings.REVISION}/') for r in calls)
    installer.install(directory=root,opener=lambda *a,**k:pytest.fail('valid model re-downloaded'))
    (root/'weights.bin').write_bytes(b'bad')
    assert not installer.installed(root)


def test_corrupt_download_and_bad_resume_range_never_become_ready(model):
    data,row,root=model
    with pytest.raises(ValueError,match='校验失败'):
        installer.install(directory=root,opener=lambda *a,**kw:Response(b'x'*len(data)))
    assert not installer.installed(root) and not (root/'weights.bin.part').exists()
    (root/'weights.bin.part').write_bytes(data[:3])
    with pytest.raises(ValueError,match='续传范围'):
        installer.install(directory=root,opener=lambda *a,**kw:Response(data,206,{'Content-Range':f'bytes 0-{len(data)-1}/{len(data)}'}))
    assert not installer.installed(root)


def test_local_import_and_disk_space_check(model,tmp_path,monkeypatch):
    data,row,root=model;source=tmp_path/'offline';source.mkdir();(source/'weights.bin').write_bytes(data)
    monkeypatch.setattr(installer.shutil,'disk_usage',lambda *_:SimpleNamespace(free=0))
    with pytest.raises(ValueError,match='磁盘空间'):
        installer.install(directory=root,local_source=source)
    monkeypatch.setattr(installer.shutil,'disk_usage',lambda *_:SimpleNamespace(free=10**10))
    installer.install(directory=root,local_source=source,opener=lambda *a,**kw:pytest.fail('offline import used network'))
    assert installer.installed(root) and (source/'weights.bin').read_bytes()==data


def test_missing_setup_defers_encoder_and_text_config_never_spends_tokens(tmp_path,monkeypatch):
    monkeypatch.setenv('PROXIMIC_EMBEDDING_RUNTIME',str(tmp_path/'empty'))
    with pytest.raises(embeddings.EmbeddingDeferred,match='设置 → Agent'):
        embeddings.run_encoder({'a':'text'},tmp_path)
    assert configuration_error(LLMSettings(provider='local'))
    assert configuration_error(LLMSettings(provider='volcengine',model='test',api_key_env=''))
    assert not configuration_error(LLMSettings(provider='volcengine',model='test',api_key='test-key'))


def test_packaged_entrypoint_uses_own_executable_without_python_or_ui(monkeypatch,tmp_path):
    from test_packaging_launcher import _load_launcher
    from proximic_ring.agent_workflow.operations import operation_embedding_worker as worker
    launcher=_load_launcher();calls=[]
    monkeypatch.setattr(launcher.sys,'argv',['/Applications/Mythlink.app/Contents/MacOS/Mythlink','--workflow-embedding-worker','in','out'])
    monkeypatch.setattr(launcher.multiprocessing,'freeze_support',lambda:None)
    monkeypatch.setattr(launcher,'run',lambda:pytest.fail('worker opened UI'))
    monkeypatch.setattr(worker,'main',lambda argv:calls.append(argv))
    assert launcher._entrypoint()==0 and calls==[['in','out']]
    monkeypatch.setenv('PROXIMIC_DATA_HOME',str(tmp_path))
    monkeypatch.delenv('PROXIMIC_EMBEDDING_RUNTIME',raising=False)
    assert embeddings.model_directory().is_relative_to(tmp_path)
    assert embeddings.runtime_python()==Path(sys.executable)


@pytest.mark.parametrize('size',[(1200,780),(940,700)])
def test_setup_ui_routes_and_large_download_progress(inline_ui,monkeypatch,tmp_path,size):
    from PySide6.QtCore import QObject,QMetaObject,QPointF
    from PySide6.QtTest import QTest
    from proximic_ring.agent_workflow.ui import agent_setup as setup_module
    owner,_,_,root,_=inline_ui;setup=owner.agent.setup
    monkeypatch.setattr(setup_module,'installed',lambda:False)
    monkeypatch.setenv('PROXIMIC_EMBEDDING_RUNTIME',str(tmp_path/'model'))
    setup.refresh()
    dialog=root.findChild(QObject,'runtimeSettingsDialog')
    root.resize(*size);root.show();QMetaObject.invokeMethod(dialog,'open');QTest.qWait(100)
    def item(name):
        value=root.findChild(QObject,name);assert value is not None,name
        return value
    group=item('settingsAgentGroup')
    rows=[obj.property('title') for obj in group.findChildren(QObject) if obj.property('title')]
    assert rows==['连接 Codex','语义召回模型','下一步推荐','自动整理任务']
    assert item('agentEmbeddingStatus').property('text')=='未下载'
    assert item('agentEmbeddingDownloadButton').property('text')=='下载模型'
    switch=item('agentWorkflowAutoSwitch')
    assert switch.property('checked') and owner.agent.workflowAuto
    QMetaObject.invokeMethod(switch,'click')
    assert not owner.agent.workflowAuto and owner._settings.value('agent/workflowAuto',type=bool) is False
    QMetaObject.invokeMethod(switch,'click')
    assert owner.agent.workflowAuto and owner._settings.value('agent/workflowAuto',type=bool) is True
    calls=[];monkeypatch.setattr(setup,'_start',lambda *args:calls.append(args))
    QMetaObject.invokeMethod(item('agentEmbeddingDownloadButton'),'click')
    assert calls==[()]
    setup._busy=True
    setup._progress.emit('下载',2_200_000_000,2_293_315_801);QTest.qWait(10)
    assert .95<setup.progress<1 and '96%' in setup.status
    assert item('agentEmbeddingStatus').property('text')=='处理中'
    assert item('agentEmbeddingDownloadButton').property('text')=='取消'
    assert item('agentEmbeddingProgress').property('visible')
    QMetaObject.invokeMethod(item('agentEmbeddingDownloadButton'),'click')
    assert setup._cancel.is_set() and calls==[()]
    setup._busy=False;setup._cancel.clear();setup._status=''
    monkeypatch.setattr(setup_module,'installed',lambda:True);setup.refresh()
    owner.agent._connection_state='ready';owner.agent.changed.emit();QTest.qWait(20)
    for name in ['agentConnectionStatus','agentEmbeddingStatus']:
        assert item(name).property('color').name()=='#16875c'
    assert item('agentEmbeddingStatus').property('text')=='已下载'
    assert item('agentEmbeddingDownloadButton').property('text')=='校验模型'
    assert not item('agentEmbeddingProgress').property('visible')
    import os
    shots=Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR',str(tmp_path)));shots.mkdir(parents=True,exist_ok=True)
    flick=item('runtimeSettingsScroll').property('contentItem')
    flick.setProperty('contentY',group.mapToItem(flick.property('contentItem'),QPointF()).y()-16)
    QTest.qWait(60);root.grabWindow().save(str(shots/f'agent-settings-{size[0]}.png'))
    for name in ['agentConnectButton','agentEmbeddingDownloadButton','agentWorkflowAutoSwitch']:
        button=item(name);right=button.mapToScene(QPointF(button.width(),0)).x()
        assert 0<right<=root.width()
    assert item('agentConnectButton').height()==item('agentEmbeddingDownloadButton').height()==42
    QMetaObject.invokeMethod(dialog,'close')
