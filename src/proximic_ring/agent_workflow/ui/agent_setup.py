"""First-use readiness and explicit model installation, independent of Agent tasks."""
from __future__ import annotations

from pathlib import Path
import threading
import time
from urllib.error import URLError

from PySide6.QtCore import QObject,Property,Signal,Slot,QUrl,Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from .. import pipeline_policy


def installed():
    from ..operations.embedding_install import installed as check
    return check()


def model_directory():
    from ..operations.operation_embeddings import model_directory as directory
    return directory()


class AgentSetup(QObject):
    changed=Signal()
    _progress=Signal(str,object,object)
    _finished=Signal(str)

    def __init__(self,agent):
        super().__init__(agent);self.agent=agent;self._busy=False;self._closed=False
        self._cancel=threading.Event();self._ready=False;self._status='';self._fraction=0.
        self._text_problem='';self._progress.connect(self._on_progress,Qt.QueuedConnection)
        self._finished.connect(self._on_finished,Qt.QueuedConnection)
        self.refresh()

    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(bool,notify=changed)
    def ready(self):return self._ready
    @Property(float,notify=changed)
    def progress(self):return self._fraction
    @Property(str,notify=changed)
    def status(self):
        if not pipeline_policy.legacy_workflow_enabled():return '下一步推荐使用 Computer History，无需本地召回模型。'
        return self._status or ('本地召回模型已校验，可离线使用。' if self._ready else
            'BGE-M3，用于 Workflow 语义召回；首次下载约 2.3 GB，下载后离线运行。')
    @Property(str,notify=changed)
    def actionLabel(self):
        if not pipeline_policy.legacy_workflow_enabled():return ''
        if self._ready:return '校验模型'
        if (model_directory()/'pytorch_model.bin').exists():return '修复模型'
        if (model_directory()/'pytorch_model.bin.part').exists():return '继续下载'
        return '下载模型'
    @Property(bool,notify=changed)
    def textReady(self):return not self._text_problem
    @Property(str,notify=changed)
    def textStatus(self):
        if not pipeline_policy.legacy_workflow_enabled():return '旧 Workflow 提取与归并已停用。'
        return self._text_problem or '已填写火山方舟配置，提取与归并开启思考；服务可用性以实际请求为准。'
    @Property(str,notify=changed)
    def directory(self):return str(model_directory()) if pipeline_policy.legacy_workflow_enabled() else ''

    @Slot()
    def refresh(self):
        if self._closed:return
        if not pipeline_policy.legacy_workflow_enabled():
            self._ready=False;self._text_problem='';self.changed.emit()
            return
        from ..extraction.workflow_model import configuration_error
        self._ready=installed()
        settings=self.agent.owner._llm_settings() if hasattr(self.agent.owner,'_llm_settings') else None
        self._text_problem=configuration_error(settings)
        self.changed.emit()

    @Slot()
    def download(self):self._start()

    @Slot()
    def importModel(self):
        if self._busy or not pipeline_policy.legacy_workflow_enabled():return
        folder=QFileDialog.getExistingDirectory(None,'选择已下载的 BGE-M3 模型文件夹',str(Path.home()))
        if folder:self._start(folder)

    def _start(self,folder=None):
        if self._busy or self._closed or not pipeline_policy.legacy_workflow_enabled():return
        self._busy=True;self._cancel.clear();self._fraction=0.;self._status='正在检查已有文件和磁盘空间…'
        self.changed.emit()
        def work():
            from ..operations.embedding_install import install,InstallCancelled
            from ..operations.operation_embeddings import EmbeddingDeferred
            last=[0.]
            def progress(label,current,total):
                if time.monotonic()-last[0]>.15 or current==total:
                    last[0]=time.monotonic()
                    if not self._closed:self._progress.emit(label,current,total)
            error=''
            try:install(local_source=folder,progress=progress,cancelled=self._cancel.is_set)
            except InstallCancelled:error='已取消，下载进度保留；可稍后继续。'
            except EmbeddingDeferred:error='召回模型正在计算，请稍后重试。'
            except ValueError as exc:error=str(exc)
            except (URLError,TimeoutError):error='模型下载连接失败，进度已保留；请检查网络后重试。'
            except OSError:error='模型安装失败，请检查磁盘空间、目录权限或网络后重试。'
            except Exception:error='模型安装未完成，请重试；现有任务记录已保留。'
            if not self._closed:self._finished.emit(error)
        threading.Thread(target=work,name='WorkflowModelSetup',daemon=True).start()

    @Slot()
    def cancel(self):
        if self._busy:self._cancel.set();self._status='正在取消，保留已下载文件…';self.changed.emit()

    @Slot(str,object,object)
    def _on_progress(self,label,current,total):
        if self._closed or self._cancel.is_set():return
        self._fraction=current/total if total else 0.
        self._status=f'{label} · {self._fraction:.0%}';self.changed.emit()

    @Slot(str)
    def _on_finished(self,error):
        if self._closed:return
        self._busy=False;self._status=error;self.refresh()
        self.agent._configure_workflows()

    @Slot()
    def openFolder(self):
        if not pipeline_policy.legacy_workflow_enabled():return
        path=model_directory();path.mkdir(parents=True,exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot()
    def openComputerHelp(self):
        QDesktopServices.openUrl(QUrl('https://learn.chatgpt.com/docs/computer-use'))

    def close(self):self._closed=True;self._cancel.set()
