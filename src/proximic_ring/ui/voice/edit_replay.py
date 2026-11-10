"""In-memory copies of the inputs of successful native edit requests."""
from copy import deepcopy
from dataclasses import replace

from ...text_processing.context_preparation import PreparedContext
from ...text_processing.model import INPUT_ROUTING_MANUAL


def frozen_edit_request(request, result):
    if result.error:
        return None
    context = None
    if request.routing_mode != INPUT_ROUTING_MANUAL:
        if isinstance(result.model_input, dict) and "screen_context" in result.model_input:
            value = result.model_input["screen_context"]
        elif isinstance(request.screen_context, PreparedContext) and request.screen_context.done():
            value = request.screen_context.result()
        else:
            # Never capture a pending OCR future or substitute today's context.
            return None
        context = PreparedContext()
        context.set_result(deepcopy(value))
    return replace(request, settings=deepcopy(request.settings), screen_context=context,
                   edit_plan=deepcopy(request.edit_plan))


def replay_context(request):
    if request.screen_context is None:
        return None
    result = PreparedContext()
    result.set_result(deepcopy(request.screen_context.result()))
    return result
