"""Compatibility path; implementation: proximic_ring.agent_workflow.ui.agent_overlay."""
import sys
from proximic_ring.agent_workflow.ui import agent_overlay as _implementation
sys.modules[__name__] = _implementation
