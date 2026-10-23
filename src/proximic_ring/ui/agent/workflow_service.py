"""Compatibility path; implementation: proximic_ring.agent_workflow.ui.workflow_service."""
import sys
from proximic_ring.agent_workflow.ui import workflow_service as _implementation
sys.modules[__name__] = _implementation
