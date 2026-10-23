import QtQuick
import QtQuick.Controls

// The outer ScrollView owns the scrollbar and layout; the browser viewport is
// pinned inside it. Their scroll positions stay in sync, so selection and wheel
// scrolling work without rendering a browser surface as tall as the document.
Item {
    id: message
    property string text: ""
    property bool markdown: true
    property var viewport
    readonly property bool rich: markdown && /[#*_`~\[\]<>|$\\]|https?:\/\/|www\.|(^|\n)\s*(?:[-+]|\d+[.)])\s|(^|\n)\s*-{3,}\s*(\n|$)/.test(text)
    readonly property bool rendered: rich && browser.status === Loader.Ready && messageRenderer.ready
    implicitHeight: rendered ? messageRenderer.documentHeight : fallback.implicitHeight
    height: implicitHeight
    onTextChanged: messageRenderer.setSource(text)
    Component.onCompleted: messageRenderer.setSource(text)

    TextEdit {
        id: fallback
        anchors.left: parent.left; anchors.right: parent.right
        visible: !message.rendered
        text: message.text
        color: "#E4E4E4"; font.pixelSize: 13
        wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
        readOnly: true; selectByMouse: true; persistentSelection: true
        selectionColor: "#526579"; selectedTextColor: "white"
        padding: 0
    }
    Loader {
        id: browser
        objectName: "agentRichMessageLoader"
        active: message.rich
        source: "AgentMessageWeb.qml"
        width: parent.width
        height: Math.min(message.implicitHeight, message.viewport.availableHeight)
        y: Math.max(0, message.viewport.contentItem.contentY)
        visible: message.rendered
        onLoaded: item.scrollTo(message.viewport.contentItem.contentY)
    }
    Connections {
        target: message.viewport.contentItem
        function onContentYChanged() {
            if (browser.item) browser.item.scrollTo(message.viewport.contentItem.contentY)
        }
    }
    Connections {
        target: messageRenderer
        function onScrolled(position) {
            if (message.rendered && Math.abs(message.viewport.contentItem.contentY-position)>1)
                message.viewport.contentItem.contentY = position
        }
    }
}
