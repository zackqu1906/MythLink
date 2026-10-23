"""Compatibility import for the standalone experiment."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"src"))
from proximic_ring.agent_workflow.agent import requests_ui as _implementation
sys.modules[__name__] = _implementation
