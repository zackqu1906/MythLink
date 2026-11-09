"""Three mode boundaries; worker owns scheduling/cancellation, not policy."""
from dataclasses import dataclass

from .edit_tool import EDIT_MODE_RACE
from .model import INPUT_MODE_EDIT, INPUT_ROUTING_ROUTED_EDIT, TEXT_MODE_POLISH, normalize_input_mode
from .context_preparation import resolve_model_context
from .task_routing import EDIT, EditPlan


@dataclass
class ProcessingOutput:
    text: str
    raw: str = ""
    model_input: dict | None = None
    branches: tuple = ()
    winner: str = ""
    task_type: str = ""
    edit_plan: EditPlan | None = None
    screen_context: object = None


def process_manual(processor, request, on_trace):
    """Original explicit dictation/edit/polish and its candidate trace."""
    if request.mode == TEXT_MODE_POLISH:
        text, raw = processor.polish_with_trace(request.raw_text, request.settings)
        return ProcessingOutput(text, raw)
    args = (request.raw_text, request.mode, request.settings, request.target_text,
            EDIT_MODE_RACE if normalize_input_mode(request.mode) == INPUT_MODE_EDIT else "")
    collection = getattr(processor, "process_with_collection_trace", None)
    if callable(collection):
        text, raw, branches, winner = collection(*args, on_collection_complete=on_trace)
        return ProcessingOutput(text, raw, branches=tuple(branches), winner=winner)
    traced = getattr(processor, "process_with_trace", None)
    if callable(traced):
        text, raw = traced(*args)
        return ProcessingOutput(text, raw)
    return ProcessingOutput(processor.process(*args))


def process_automatic(processor, request, cancelled=lambda: False):
    """Clef has already provided the task and plan; generate exactly once."""
    args = (request.raw_text, request.task_type, request.settings, request.target_text,
            resolve_model_context(request.screen_context, cancelled=cancelled))
    if cancelled():
        raise RuntimeError("文本处理已取消")
    traced = getattr(processor, "process_task_with_input_trace", None)
    if callable(traced):
        text, raw, model_input = traced(*args, edit_plan=request.edit_plan)
    else:
        text, raw = processor.process_task_with_trace(*args, edit_plan=request.edit_plan)
        model_input = None
    return ProcessingOutput(text, raw, model_input, task_type=request.task_type, edit_plan=request.edit_plan)


def process_routed_edit(processor, request, cancelled=lambda: False):
    """Manual edit intent: resolve the same OCR frame, then one LLM call."""
    context = resolve_model_context(request.screen_context, cancelled=cancelled)
    if cancelled():
        raise RuntimeError("编辑已取消")
    text, raw, model_input, plan = processor.process_routed_edit_with_trace(
        request.raw_text, request.settings, request.target_text, context)
    return ProcessingOutput(text, raw, model_input, task_type=EDIT, edit_plan=plan, screen_context=context)


def process_text_request(processor, request, *, on_trace, cancelled=lambda: False):
    if request.routing_mode == INPUT_ROUTING_ROUTED_EDIT and request.mode == INPUT_MODE_EDIT:
        return process_routed_edit(processor, request, cancelled)
    if request.task_type:
        return process_automatic(processor, request, cancelled)
    return process_manual(processor, request, on_trace)
