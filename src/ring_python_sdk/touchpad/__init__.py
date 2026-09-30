"""Optional host touchpad inference. Firmware gesture recognition is separate.

MNN is loaded only when constructing the backbone/processor or starting a stream.
"""
from .events import TouchpadClick, TouchpadEvent, TouchpadMove, TouchpadStats
from .model import TouchpadBackbone, default_model_path
from .processor import TouchpadProcessor

__all__ = ['TouchpadClick', 'TouchpadEvent', 'TouchpadMove', 'TouchpadStats',
           'TouchpadBackbone', 'TouchpadProcessor', 'default_model_path']
