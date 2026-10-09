import QtQuick
import QtQuick.Controls

Label {
    id: notice
    property bool warning: true
    property color noticeBackground: theme.warningSurface
    property color noticeBorder: theme.warningBorder
    UiTheme { id: theme }
    FontMetrics { id: metrics; font: notice.font }

    visible: text.length > 0
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    font.pixelSize: 13
    lineHeight: 1.2
    color: warning ? theme.warning : theme.muted
    padding: warning ? 10 : 0
    leftPadding: warning ? 38 : 0
    rightPadding: warning ? 12 : 0
    background: Rectangle {
        visible: notice.warning
        radius: 10
        color: notice.noticeBackground
        border.color: notice.noticeBorder
    }
    Rectangle {
        visible: notice.warning
        x: notice.leftPadding - 26
        // Follow the first text baseline even when the message wraps.
        y: notice.baselineOffset - metrics.ascent + (metrics.height - height) / 2
        width: 16; height: 16; radius: 8
        color: "transparent"
        border.color: notice.color
        Accessible.ignored: true
        Label {
            anchors.centerIn: parent
            text: "!"
            color: notice.color
            font.pixelSize: 11; font.bold: true
            Accessible.ignored: true
        }
    }
}
