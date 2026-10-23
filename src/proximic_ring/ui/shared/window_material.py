"""A native backdrop behind Qt's transparent content, without replacing QNSView."""


class WindowMaterial:
    def __init__(self, window):
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
        effect.setAppearance_(A.NSAppearance.appearanceNamed_(A.NSAppearanceNameDarkAqua))
        effect.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
        # Sibling below Qt, not a subview covering the cards and their hit areas.
        parent.addSubview_positioned_relativeTo_(effect, A.NSWindowBelow, content)
        native.setOpaque_(False)
        native.setBackgroundColor_(A.NSColor.clearColor())
        self.effect, self.content = effect, content

    def resize(self):
        self.effect.setFrame_(self.content.frame())

    def close(self):
        self.effect.removeFromSuperview()
