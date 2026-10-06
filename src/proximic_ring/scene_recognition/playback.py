"""Shared playback-control evidence for native windows and web players."""
from dataclasses import dataclass
import re


@dataclass
class PlaybackControls:
    play: bool = False
    seek: bool = False
    structural_seek: bool = False

    def observe(self, node, read, *, use_title=True, web_structure=False):
        role = read(node, 'AXRole')
        if web_structure and role in {'AXGroup', 'AXUnknown'}:
            if read(node, 'AXEnabled') is False or read(node, 'AXHidden'):
                return
            # Custom HTML players often expose a div-based timeline rather than
            # AXSlider. Only called within an identified player container; the
            # caller still requires a real audio/video element and Play control.
            classes = read(node, 'AXDOMClassList') or []
            classes = classes.split() if isinstance(classes, str) else classes
            identifiers = [str(read(node, 'AXDOMIdentifier') or '')] + [str(value) for value in classes]
            timeline = any(
                re.search(r'(?:^|[-_])(?:progress|timeline|seekbar|seek|scrubber)(?:$|[-_])', value.casefold())
                and not re.search(r'(?:^|[-_])(?:volume|loading|spinner)(?:$|[-_])', value.casefold())
                for value in identifiers)
            self.structural_seek |= timeline
            self.seek |= timeline
            return
        if role not in {'AXButton', 'AXCheckBox', 'AXSlider', 'AXValueIndicator'}:
            return
        if read(node, 'AXEnabled') is not True or read(node, 'AXHidden'):
            return
        labels = ' '.join(str(read(node, key) or '') for key in ('AXIdentifier', 'AXDescription', 'AXSubrole')).casefold()
        if use_title:
            labels += ' ' + str(read(node, 'AXTitle') or '').casefold()
        self.play |= role in {'AXButton', 'AXCheckBox'} and bool(re.search(r'\b(?:play|pause)\b|播放|暂停|暫停', labels))
        self.seek |= role in {'AXSlider', 'AXValueIndicator'} and bool(re.search(
            r'\b(?:seek|scrub\w*|timeline|playback|progress|time)\b|进度|進度|时间|時間', labels))

    @property
    def loaded(self):
        return self.play and self.seek
