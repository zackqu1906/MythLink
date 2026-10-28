"""Shared test defaults: headless Qt and platform markers.

Qt reads these variables when the first QGuiApplication is created, so they
are set here, before any test module imports PySide6. Either can still be
overridden from the environment (for example QT_QPA_PLATFORM=windows to watch
the windows while debugging).
"""
import os
import sys

import pytest

# The native Windows/macOS styles reject the customized controls in UiAction.qml
# with a QML warning, which the UI fixtures treat as a failure.
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
# Offscreen windows never compete for focus, so UI test files can run in parallel.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # Environments without the UI extra still run the non-Qt tests.
    QApplication = None
else:
    # Qt allows one application object per process, and the first test to run
    # used to decide its type. A bare QCoreApplication lacks GUI signals such as
    # screenAdded, so later UI tests in the same (xdist) process failed in setup.
    _application = QApplication.instance() or QApplication([])
    # Keep PySide's offscreen screen wrappers alive across QML engine teardown.
    # Otherwise repeated overlay tests can invalidate the primary screen before
    # the next QWindow is constructed (QWindowPrivate::init crashes).
    _screens = _application.screens()

IS_MAC = sys.platform == "darwin"
IS_POSIX = os.name == "posix"

# These modules import POSIX-only libraries (fcntl) at import time, so a
# skip marker inside them would never be reached on Windows.
collect_ignore = [] if IS_POSIX else [
    "test_workflow_pipeline_disabled.py",
    "test_workflow_trial_cli.py",
]


def pytest_collection_modifyitems(config, items):
    mac = pytest.mark.skip(reason="requires macOS (AppKit, Accessibility, IMK or macOS paths)")
    posix = pytest.mark.skip(reason="requires POSIX APIs (fcntl, getuid, file modes, pass_fds or Unix sockets)")
    for item in items:
        if not IS_MAC and item.get_closest_marker("mac_only"):
            item.add_marker(mac)
        elif not IS_POSIX and item.get_closest_marker("posix_only"):
            item.add_marker(posix)
