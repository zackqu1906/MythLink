"""A native backdrop behind Qt's transparent content, without replacing QNSView."""


class WindowMaterial:
    def __init__(self, window, *, light=False, insets=(0, 0, 0, 0), corner_radius=0):
        import ctypes
        import objc
        import AppKit as A

        view = objc.objc_object(c_void_p=ctypes.c_void_p(int(window.winId())))
        native = view.window()
        content = native.contentView()
        parent = content.superview()
        if parent is None:
            raise RuntimeError("Native window has no backdrop container")
        effect = A.NSVisualEffectView.alloc().initWithFrame_(content.frame())
        effect.setMaterial_(A.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(A.NSVisualEffectStateActive)
        appearance = A.NSAppearanceNameAqua if light else A.NSAppearanceNameDarkAqua
        effect.setAppearance_(A.NSAppearance.appearanceNamed_(appearance))
        if corner_radius:
            effect.setWantsLayer_(True)
            effect.layer().setCornerRadius_(corner_radius)
            effect.layer().setMasksToBounds_(True)
        effect.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
        # Sibling below Qt, not a subview covering the cards and their hit areas.
        parent.addSubview_positioned_relativeTo_(effect, A.NSWindowBelow, content)
        native.setOpaque_(False)
        native.setBackgroundColor_(A.NSColor.clearColor())
        self.effect, self.content = effect, content
        self.insets = insets
        self.resize(corner_radius=corner_radius)

    def resize(self, *, corner_radius=None):
        from Foundation import NSMakeRect
        frame = self.content.frame()
        left, top, right, bottom = self.insets
        self.effect.setFrame_(NSMakeRect(frame.origin.x + left, frame.origin.y + bottom,
                                        max(0, frame.size.width - left - right),
                                        max(0, frame.size.height - top - bottom)))
        if corner_radius is not None and self.effect.layer() is not None:
            self.effect.layer().setCornerRadius_(corner_radius)

    def close(self):
        self.effect.removeFromSuperview()
