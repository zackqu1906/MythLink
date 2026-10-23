"""Keep Agent test evidence out of the user's persistent recording library."""
import pytest


@pytest.fixture(autouse=True)
def serialized_agent_native_focus(request):
    """Separate pytest jobs share one desktop; never compete for its key window."""
    import os
    import sys
    from pathlib import Path
    native_files = {'test_agent_dismissal_native.py', 'test_agent_focus_native.py',
                    'test_agent_ime_focus_native.py'}
    if (sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_AGENT_NATIVE') != '1'
            or os.environ.get('PROXIMIC_DISMISS_WORKER') == '1'
            or request.node.path.name not in native_files):
        yield
        return
    import fcntl
    # The parent owns the lock across its child process and cleanup. Workers
    # inherit the flag above, avoiding a parent/child lock deadlock.
    with (Path('/tmp') / f'mythlink-native-focus-{os.getuid()}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@pytest.fixture
def legacy_workflow_pipeline(monkeypatch):
    """Explicit opt-in for retained legacy algorithm/integration regressions."""
    from proximic_ring.agent_workflow import pipeline_policy
    monkeypatch.setattr(pipeline_policy, 'LEGACY_WORKFLOW_ENABLED', True)


@pytest.fixture(scope='session', autouse=True)
def agent_web_engine_runtime():
    # QML WebEngine requires shared contexts before the first QApplication.
    try:
        from proximic_ring.agent_workflow.ui.message_document import initialize_web_engine
    except ImportError:
        return
    initialize_web_engine()


@pytest.fixture(autouse=True)
def isolated_agent_experience(tmp_path, monkeypatch):
    from proximic_ring.agent_workflow.recording import recording, records

    root = tmp_path / 'agent-experience'
    # Worker tests must not publish busy signals to the real app or benchmarks.
    monkeypatch.setenv('PROXIMIC_EMBEDDING_ACTIVITY', str(tmp_path/'workflow-activity'))
    monkeypatch.setattr(recording, 'records_root', lambda: root)
    # records imports the function directly, so isolate its default as well.
    monkeypatch.setattr(records, 'records_root', lambda: root)
    # UI fixtures must not start a network-capable child process. Worker tests
    # exercise the queue/model boundary explicitly with injected model functions.
    from proximic_ring.ui.agent.workflow_service import WorkflowService
    monkeypatch.setattr(WorkflowService, 'send', lambda self, command: self.commands.put(command))
