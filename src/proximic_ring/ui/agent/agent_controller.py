"""Compatibility path; implementation: proximic_ring.agent_workflow.ui.agent_controller."""
import sys
from proximic_ring.agent_workflow.ui import agent_controller as _implementation
sys.modules[__name__] = _implementation
