"""Stable gesture identity for Mythlink, including an unbundled Python launch."""
from pathlib import Path
import sys

HOST_BUNDLE = "com.proximic.voice"
HOST_LABEL = "Mythlink"
# Qt tool windows expose AXSystemDialog even when they are ordinary workspaces.
# Only these owned workspaces qualify; request/permission dialogs do not.
HOST_WORKSPACE_TITLES = frozenset({HOST_LABEL, "Mythlink Agent · 任务"})


def application_identity(app, host_pid=0):
    pid = int(app.processIdentifier())
    return (HOST_BUNDLE if host_pid and pid == host_pid else str(app.bundleIdentifier() or ""), pid)


def host_candidate():
    path = next((str(p) for p in Path(sys.executable).parents if p.suffix == ".app"), "") if getattr(sys, "frozen", False) else ""
    return dict(value=HOST_BUNDLE, label=HOST_LABEL, path=path, running=True,
                search=f"{HOST_LABEL} {HOST_BUNDLE}".casefold())
