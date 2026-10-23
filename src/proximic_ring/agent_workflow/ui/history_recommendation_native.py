"""Native nonactivating voice-palette-sized surface; never hosts a QNSView.

Moving a Qt top-level content view into NSPanel lets Qt's Cocoa state restoration
maximize the panel. Native controls keep size and focus under one window owner.
"""
import time

import AppKit as A
from Foundation import NSObject
from objc import super as objc_super
from Quartz import CGColorCreateGenericRGB

from ..history.history_source import HUD_TITLE
from proximic_ring.ui.shared.overlay_stacking import configure_native_overlay


class HistoryPanel(A.NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False

    def sendEvent_(self, event):
        if (getattr(self, "recommendation", None) is not None and self.isVisible()
                and event.type() in (
                    A.NSEventTypeMouseEntered, A.NSEventTypeMouseMoved,
                    A.NSEventTypeLeftMouseDown, A.NSEventTypeLeftMouseUp,
                    A.NSEventTypeRightMouseDown, A.NSEventTypeRightMouseUp,
                    A.NSEventTypeOtherMouseDown, A.NSEventTypeOtherMouseUp,
                    A.NSEventTypeLeftMouseDragged, A.NSEventTypeRightMouseDragged,
                    A.NSEventTypeOtherMouseDragged, A.NSEventTypeScrollWheel)
                and A.NSPointInRect(event.locationInWindow(), self.contentView().bounds())):
            self.recommendation.interact()
        objc_super(HistoryPanel, self).sendEvent_(event)

    def scrollWheel_(self, event):
        now = time.monotonic()
        if abs(event.scrollingDeltaY()) > 0 and now - self.lastScroll > 0.18:
            self.lastScroll = now
            self.recommendation.step(1 if event.scrollingDeltaY() < 0 else -1)


class HistoryActions(NSObject):
    def execute_(self, sender):
        self.recommendation.execute()

    def dismiss_(self, sender):
        self.recommendation.dismiss()


def label(surface, frame, size, *, color=None, bold=False):
    field = A.NSTextField.labelWithString_("")
    field.setFrame_(A.NSMakeRect(*frame))
    field.setFont_(A.NSFont.systemFontOfSize_weight_(size, A.NSFontWeightSemibold if bold else A.NSFontWeightRegular))
    field.setTextColor_(color or A.NSColor.secondaryLabelColor())
    field.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
    field.setFocusRingType_(A.NSFocusRingTypeNone)
    surface.addSubview_(field)
    return field


class NativeHistoryRecommendation:
    def __init__(self, controller):
        self.controller = controller
        panel = HistoryPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(0, 0, 300, 140), A.NSWindowStyleMaskNonactivatingPanel,
            A.NSBackingStoreBuffered, False)
        panel.setReleasedWhenClosed_(False)
        panel.setTitle_(HUD_TITLE)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(A.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setAcceptsMouseMovedEvents_(True)
        panel.setMinSize_(A.NSMakeSize(300, 140))
        panel.setMaxSize_(A.NSMakeSize(300, 140))
        panel.recommendation, panel.lastScroll = controller, 0
        self.panel = panel
        self.actions = HistoryActions.alloc().init()
        self.actions.recommendation = controller
        surface = A.NSVisualEffectView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 300, 140))
        surface.setMaterial_(A.NSVisualEffectMaterialHUDWindow)
        surface.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
        surface.setState_(A.NSVisualEffectStateActive)
        surface.setAppearance_(A.NSAppearance.appearanceNamed_(A.NSAppearanceNameDarkAqua))
        surface.setWantsLayer_(True)
        surface.setFocusRingType_(A.NSFocusRingTypeNone)
        surface.layer().setCornerRadius_(10)
        surface.layer().setMasksToBounds_(True)
        surface.layer().setBorderWidth_(0)
        panel.setContentView_(surface)
        self.surface = surface
        # Track pointer movement even though this panel never becomes key.
        self.tracking = A.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            surface.bounds(), A.NSTrackingMouseMoved | A.NSTrackingMouseEnteredAndExited
            | A.NSTrackingActiveAlways | A.NSTrackingInVisibleRect, panel, None)
        surface.addTrackingArea_(self.tracking)
        self.heading = label(surface, (12, 111, 73, 17), 11)
        self.heading.setStringValue_("猜你想做")
        self.age = label(surface, (86, 111, 108, 17), 10)
        self.counter = label(surface, (218, 111, 42, 17), 11)
        self.title = label(surface, (12, 84, 276, 22), 14, color=A.NSColor.labelColor(), bold=True)
        self.plan = label(surface, (12, 43, 276, 36), 11)
        self.plan.setMaximumNumberOfLines_(2)
        self.plan.setUsesSingleLineMode_(False)
        self.plan.cell().setWraps_(True)
        self.footer = label(surface, (12, 13, 175, 18), 10)
        self.dismiss_button = A.NSButton.buttonWithTitle_target_action_("×", self.actions, "dismiss:")
        self.dismiss_button.setFrame_(A.NSMakeRect(264, 109, 25, 23))
        self.dismiss_button.setBordered_(False)
        self.dismiss_button.setFont_(A.NSFont.systemFontOfSize_(18))
        self.dismiss_button.setAccessibilityLabel_("关闭推荐")
        self.dismiss_button.setRefusesFirstResponder_(True)
        self.dismiss_button.setFocusRingType_(A.NSFocusRingTypeNone)
        surface.addSubview_(self.dismiss_button)
        self.execute_button = A.NSButton.buttonWithTitle_target_action_("交给 Agent", self.actions, "execute:")
        self.execute_button.setFrame_(A.NSMakeRect(194, 9, 96, 28))
        self.execute_button.setBordered_(False)
        self.execute_button.setWantsLayer_(True)
        self.execute_button.layer().setBackgroundColor_(CGColorCreateGenericRGB(0.14, 0.34, 0.77, 1))
        self.execute_button.layer().setCornerRadius_(7)
        self.execute_button.setContentTintColor_(A.NSColor.whiteColor())
        self.execute_button.setFont_(A.NSFont.systemFontOfSize_(12))
        self.execute_button.setRefusesFirstResponder_(True)
        self.execute_button.setFocusRingType_(A.NSFocusRingTypeNone)
        self.execute_button.setAccessibilityLabel_("交给 Agent 执行当前推荐")
        surface.addSubview_(self.execute_button)
        controller.changed.connect(self.update)
        self.update()

    def update(self):
        controller = self.controller
        self.age.setStringValue_(controller.age)
        self.counter.setStringValue_(f"{controller.position} / {controller.count}" if controller.count else "")
        self.title.setStringValue_(controller.title)
        self.plan.setStringValue_(controller.plan)
        self.plan.setToolTip_(controller.plan if controller.count else None)
        self.footer.setStringValue_("后台更新中…" if controller.refreshing else controller.interactionHint)
        self.execute_button.setTitle_(controller.actionLabel)
        self.execute_button.setToolTip_(controller.threadTitle or None)
        self.execute_button.setAccessibilityLabel_(controller.actionLabel + "：" + controller.title)
        self.execute_button.setEnabled_(controller.count > 0)

    def show(self, geometry):
        height = A.NSScreen.screens()[0].frame().size.height
        frame = A.NSMakeRect(geometry.x(), height - geometry.y() - 140, 300, 140)
        self.panel.setFrame_display_(frame, True)
        self.update()
        configure_native_overlay(self.panel, A)

    def hide(self):
        self.panel.orderOut_(None)

    def close(self):
        self.controller.changed.disconnect(self.update)
        self.hide()
        self.surface.removeTrackingArea_(self.tracking)
        self.panel.close()
        self.panel.recommendation = None
        self.actions.recommendation = None
