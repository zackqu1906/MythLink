"""Exercise the real UI/record readers with retired release modules absent."""
import os
from pathlib import Path
import subprocess
import sys


def test_history_release_without_legacy_modules(tmp_path):
    root = Path(__file__).parents[1]
    script = r'''
import importlib.abc, runpy, sys, time
from pathlib import Path
from unittest.mock import patch
source = Path.cwd() / 'src'
policy = runpy.run_path('packaging/legacy_workflows.py')
excluded = policy['excluded_modules'](source)
class Omitted(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if any(name == item or name.startswith(item + '.') for item in excluded):
            raise ModuleNotFoundError('release excludes ' + name, name=name)
sys.meta_path.insert(0, Omitted())
from proximic_ring.agent_workflow.ui.message_document import initialize_web_engine
initialize_web_engine()
from PySide6.QtCore import QSettings, QUrl, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QQmlApplicationEngine
app = QApplication([])
from proximic_ring.agent_workflow.history.package_check import verify_runtime
verify_runtime(frozen=True)
from proximic_ring.agent_workflow.agent.native_flow import NativeFlow
import proximic_ring.ui.controller as controller_module
settings = QSettings(str(Path(sys.argv[1]) / 'settings.ini'), QSettings.IniFormat)
with patch.object(controller_module, 'QSettings', lambda *args: settings):
    owner = controller_module.AppController(inline_input_enabled=False)
owner._text_processing_worker.close(wait=True)
engine = QQmlApplicationEngine()
from proximic_ring.ui.desktop.application_icons import install_application_icons
install_application_icons(engine)
engine.rootContext().setContextProperty('appController', owner)
engine.load(QUrl.fromLocalFile(str(source / 'proximic_ring/ui/qml/Main.qml')))
assert engine.rootObjects()
from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
owner.agent._visible = True
overlay = AgentOverlay(owner.agent, owner)
overlay.refresh()
assert overlay.windows['task'].rootObject() is not None
overlay.close()
# A normal native turn must finish and detach without importing old workflows.
class Adapter:
    def close(self): pass
flow = NativeFlow(with_ring=False, runtime_dir=Path(sys.argv[1]) / 'native-runtime')
def setup():
    flow.rpc = Adapter()
    flow.tid, flow.turn = 'thread', 'turn'
    flow.handle({'method': 'turn/completed', 'params': {'threadId': 'thread',
                 'turn': {'id': 'turn', 'status': 'completed'}}})
    assert flow.state['taskOutcome'] == 'completed'
    flow.stop.set()
flow.setup = setup
flow.run()
assert owner.agent.historyRecommendations.enabled
assert not owner.agent.workflowPipelineEnabled
assert owner.agent.recommendations.input is None
assert not owner.agent.recommendations.owns_input_focus()
# Probe the shared endpoint with the actual omitted release modules. A QML
# startup alone previously missed the first-gesture None input crash.
from types import SimpleNamespace
for _ in range(3):
    for name in ('tap', 'double-tap'):
        assert owner.ringGestures.filter(SimpleNamespace(name=name), True,
                                         owner._disconnect_event)
assert not owner._disconnect_event.is_set()
assert not owner.agent.setup.directory and not owner.agent.setup.ready
owner.agent.setup.download()  # Hidden legacy actions are inert even if called directly.
owner.agent.recommendations.open()
with patch.object(QDesktopServices, 'openUrl', lambda url: True):
    owner.agent.openRecords()
    deadline = time.monotonic() + 5
    while owner.agent._exporting_records and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
assert not owner.agent._exporting_records
server = owner.agent._records_server
assert server is not None, owner.agent.feedback
assert server.status()['pipeline_enabled'] is False
assert server.action('api/refresh', {})['message']
from proximic_ring.agent_workflow.recording.workbench_view import export_workbench
assert export_workbench(server.root).is_file()
try:
    server.action('api/latest', {})
except ValueError:
    pass
else:
    raise AssertionError('legacy pipeline unexpectedly enabled')
assert not any(name in sys.modules for name in excluded)
# A retired command in a frozen executable must fail before importing its worker.
launcher = runpy.run_path('packaging/launcher.py')
with patch.object(sys, 'frozen', True, create=True):
    for flag in ('--workflow-worker', '--workflow-embedding-worker'):
        with patch.object(sys, 'argv', ['Mythlink', flag]):
            assert launcher['_entrypoint']() == 2
owner.proximity.close()
owner._close_voice_history()
engine.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
print('history-only UI, records and worker rejection passed')
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=root,
        env={**os.environ, 'PYTHONPATH': str(root / 'src'), 'QT_QPA_PLATFORM': 'offscreen',
             'QT_QUICK_CONTROLS_STYLE': 'Basic', 'PROXIMIC_DATA_HOME': str(tmp_path)},
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'history-only UI, records and worker rejection passed' in result.stdout
