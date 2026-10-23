"""One full-resolution ScreenCaptureKit frame per display, in memory only.

Runs off the Qt/BLE threads. Excludes the Agent and capture windows by native
window ID, so showing an interactive loading cover does not contaminate the
snapshot or require an unowned focus gap. Only confirmed crops are saved.
Apple API: https://developer.apple.com/documentation/screencapturekit/scscreenshotmanager/captureimage(contentfilter:configuration:completionhandler:)
"""
import threading
import time
from PySide6.QtGui import QImage


class CapturePermissionError(PermissionError):pass


def _await(start,cancelled,*,timeout=10):
    done=threading.Event();result=[]
    def complete(value,error):result.extend((value,error));done.set()
    deadline=time.monotonic()+timeout
    if timeout<=0:raise TimeoutError('屏幕截取超时，请重试')
    start(complete)
    while True:
        if cancelled.is_set():raise InterruptedError('截图已取消')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('屏幕截取超时，请重试')
        if done.wait(min(.05,remaining)):break
    if result[1] is not None:raise RuntimeError('系统未能截取屏幕，请检查屏幕录制权限')
    if result[0] is None:raise RuntimeError('系统返回了空截图')
    return result[0]


def capture_screens(specs,excluded_ids,cancelled):
    import objc
    import ScreenCaptureKit as SC
    import Quartz as CG
    from Foundation import NSMutableData
    if not CG.CGPreflightScreenCaptureAccess():raise CapturePermissionError('需要屏幕录制权限才能截图')
    with objc.autorelease_pool():
        content=_await(lambda cb:SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(False,True,cb),cancelled)
        excluded=[w for w in content.windows() if int(w.windowID()) in excluded_ids]
        images=[]
        for spec in specs:
            x,y,w,h=spec['geometry'];target=None
            for display in content.displays():
                rect=display.frame();frame=(rect.origin.x,rect.origin.y,rect.size.width,rect.size.height)
                if all(abs(a-b)<2 for a,b in zip(frame,(x,y,w,h))):target=display;break
            if target is None:raise RuntimeError('显示器布局已变化，请重新截图')
            config=SC.SCStreamConfiguration.alloc().init()
            width,height=round(w*spec['scale']),round(h*spec['scale'])
            config.setWidth_(width);config.setHeight_(height)
            config.setShowsCursor_(False);config.setCapturesAudio_(False)
            config.setPixelFormat_(CG.kCVPixelFormatType_32BGRA)
            screen_filter=SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(target,excluded)
            cg_image=_await(lambda cb:SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(screen_filter,config,cb),cancelled)
            data=NSMutableData.data()
            destination=CG.CGImageDestinationCreateWithData(data,'public.png',1,None)
            CG.CGImageDestinationAddImage(destination,cg_image,None)
            if not CG.CGImageDestinationFinalize(destination):raise RuntimeError('无法转换屏幕图像')
            image=QImage.fromData(bytes(data),'PNG')
            if image.isNull():raise RuntimeError('无法读取屏幕图像')
            images.append(image)
        return images
