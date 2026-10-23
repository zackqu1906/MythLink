"""Canonical Agent modules, public CLI scripts and packaged UI resources."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('old,new', [
    ('ui.agent.agent_controller', 'ui.agent_controller'),
    ('ui.agent.workflow_service', 'ui.workflow_service'),
])
def test_compatibility_import_shares_module_state(old, new, monkeypatch):
    legacy = importlib.import_module('proximic_ring.' + old)
    canonical = importlib.import_module('proximic_ring.agent_workflow.' + new)
    assert legacy is canonical
    assert Path(canonical.__file__).is_relative_to(ROOT/'src/proximic_ring/agent_workflow')
    monkeypatch.setattr(legacy, '_layout_probe', object(), raising=False)
    assert canonical._layout_probe is legacy._layout_probe


@pytest.mark.parametrize('script,module', [
    ('run-workflows.sh', 'extraction.workflows'),
    ('run-workflow-recommendations.sh', 'operations.operation_cli'),
    ('run-workflow-trials.sh', 'execution.workflow_trial_cli'),
])
def test_public_scripts_use_canonical_cli(script, module):
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'RING_AGENT_PYTHON':sys.executable}
    direct=subprocess.run([sys.executable,'-m','proximic_ring.agent_workflow.'+module,'--help'],
        capture_output=True,text=True,env=env,timeout=15,check=True)
    command=subprocess.run(['bash',str(ROOT/'scripts'/script),'--help'],
        capture_output=True,text=True,env=env,timeout=15,check=True)
    assert command.stdout==direct.stdout
    assert not command.stderr and not direct.stderr


def test_retired_package_is_absent_and_production_entries_do_not_reference_it():
    assert not (ROOT/'src/proximic_ring/desktop_agent').exists()
    assert importlib.util.find_spec('proximic_ring.desktop_agent') is None
    paths=list((ROOT/'src').rglob('*.py'))+list((ROOT/'packaging').rglob('*.py'))+list((ROOT/'scripts').glob('*.sh'))
    for path in paths:
        assert 'proximic_ring.desktop_agent' not in path.read_text(), path


def test_source_paths_packaging_and_documented_file_inventory():
    from proximic_ring.agent_workflow.execution import workflow_trial_cli
    assert workflow_trial_cli.REPO == ROOT
    assert workflow_trial_cli.TASKS == ROOT/'experiments/workflow_trials/tasks'
    data = tomllib.loads((ROOT/'pyproject.toml').read_text())['tool']['setuptools']['package-data']['proximic_ring']
    assert 'agent_workflow/ui/qml/*.qml' in data
    guide = (ROOT/'docs/AGENT_WORKFLOW_PIPELINE.md').read_text()
    for path in (ROOT/'src/proximic_ring/agent_workflow').rglob('*.py'):
        if path.name != '__init__.py': assert path.name in guide, path.name


def test_old_qml_entry_is_still_loadable(inline_ui):
    from PySide6.QtCore import QUrl
    from PySide6.QtQuick import QQuickView
    from test_agent_ui import finish_fixture_voice
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    owner=inline_ui[0];finish_fixture_voice(owner)
    overlay=AgentOverlay(owner.agent,owner)
    try:
        window=overlay._create('task')
        window.setSource(QUrl.fromLocalFile(str(ROOT/'src/proximic_ring/ui/qml/AgentCard.qml')))
        assert window.status()==QQuickView.Ready
        assert window.rootObject() is not None
    finally:overlay.close()


from test_inline_ui import inline_ui
