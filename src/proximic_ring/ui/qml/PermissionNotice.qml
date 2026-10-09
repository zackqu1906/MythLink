import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Control {
    id: notice
    property string message: ""
    property string actionText: "去开启"
    property color ink: theme.warning
    property color noticeBackground: theme.warningSurface
    property color noticeBorder: theme.warningBorder
    signal activated()
    UiTheme { id: theme }
    visible: message.length > 0
    padding: 12
    topPadding: 8; bottomPadding: 8
    implicitHeight: implicitContentHeight + topPadding + bottomPadding
    implicitWidth: implicitContentWidth + leftPadding + rightPadding
    background: Rectangle {
        radius: 10
        color: notice.noticeBackground
        border.color: notice.noticeBorder
    }
    contentItem: RowLayout {
        spacing: 12
        UiNotice {
            objectName: notice.objectName + "Text"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: notice.message
            color: notice.ink
            padding: 0; leftPadding: 26; rightPadding: 0
            // The permission notice supplies the shared background and padding.
            background: Item { }
            Accessible.name: notice.message
        }
        UiAction {
            objectName: notice.objectName + "Button"
            visible: notice.actionText.length > 0
            implicitHeight: 32
            topPadding: 6; bottomPadding: 6
            text: notice.actionText; quiet: true
            onClicked: notice.activated()
        }
    }
}
