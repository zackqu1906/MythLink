"""Route Dock/Finder reopen commands while forwarding Qt's other app callbacks."""
import objc
from AppKit import NSApplication, NSObject


class MythlinkMainWindowDelegate(NSObject):
    def applicationShouldHandleReopen_hasVisibleWindows_(self, application, visible):
        self.show_main()
        # We selected the main window ourselves. AppKit's default selection can
        # choose an already visible Agent panel instead.
        return False

    def respondsToSelector_(self, selector):
        original = getattr(self, 'original', None)
        return (objc.super(MythlinkMainWindowDelegate, self).respondsToSelector_(selector)
                or bool(original and original.respondsToSelector_(selector)))

    def forwardingTargetForSelector_(self, selector):
        original = getattr(self, 'original', None)
        if original and original.respondsToSelector_(selector):
            return original
        return objc.super(MythlinkMainWindowDelegate, self).forwardingTargetForSelector_(selector)


def install_reopen_delegate(show_main):
    application = NSApplication.sharedApplication()
    delegate = MythlinkMainWindowDelegate.alloc().init()
    delegate.original = application.delegate()
    delegate.show_main = show_main
    application.setDelegate_(delegate)
    return delegate


def remove_reopen_delegate(delegate):
    application = NSApplication.sharedApplication()
    if application.delegate() == delegate:
        application.setDelegate_(delegate.original)
    delegate.show_main = None
