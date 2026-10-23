import QtQuick
import QtWebEngine
import QtWebChannel

WebEngineView {
    id: web
    objectName: "agentRichMessageWeb"
    url: messageRenderer.pageUrl
    profile: messageRenderer.profile
    backgroundColor: "transparent"
    audioMuted: true
    settings.focusOnNavigationEnabled: false
    settings.localContentCanAccessFileUrls: true
    settings.localContentCanAccessRemoteUrls: true
    settings.javascriptCanOpenWindows: false
    settings.javascriptCanAccessClipboard: false
    settings.fullScreenSupportEnabled: false
    settings.pdfViewerEnabled: false
    webChannel: WebChannel {
        registeredObjects: [bridge]
        propertyUpdateInterval: 30
    }
    QtObject {
        id: bridge
        WebChannel.id: "messageBridge"
        readonly property string source: messageRenderer.source
        readonly property int revision: messageRenderer.revision
        readonly property string baseUrl: messageRenderer.baseUrl
        function measure(revision, height) { messageRenderer.measure(revision, height) }
        function scroll(revision, position) { messageRenderer.scroll(revision, position) }
        function copy(text) { messageRenderer.copy(text) }
        function openLink(url) { messageRenderer.openLink(url) }
    }
    function scrollTo(y) {
        runJavaScript("window.scrollTo(0," + Math.max(0, y) + ")")
    }
    onLoadingChanged: function(info) {
        if (info.status === WebEngineView.LoadStartedStatus || info.status === WebEngineView.LoadFailedStatus)
            messageRenderer.invalidate()
    }
    onRenderProcessTerminated: messageRenderer.invalidate()
    Connections {
        target: messageRenderer
        function onLinkFailed(message) {
            web.runJavaScript("window.AgentMessage?.notify(" + JSON.stringify(message) + ")")
        }
    }
    onNavigationRequested: function(request) {
        if (request.url.toString() !== messageRenderer.pageUrl.toString()) {
            request.action = WebEngineNavigationRequest.IgnoreRequest
            if (request.isMainFrame && request.navigationType === WebEngineNavigationRequest.LinkClickedNavigation)
                messageRenderer.openLink(request.url.toString())
        }
    }
    onNewWindowRequested: function(request) { if (request.userInitiated) messageRenderer.openLink(request.requestedUrl.toString()) }
    onPermissionRequested: function(permission) { permission.deny() }
    onContextMenuRequested: function(request) { request.accepted = true }
}
