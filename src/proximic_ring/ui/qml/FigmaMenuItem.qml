import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.MenuItem {
    id: item
    property bool destructive: false
    FigmaTheme { id: theme }
    implicitWidth: Math.max(168, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: 40
    leftPadding: 10; rightPadding: 10; topPadding: 6; bottomPadding: 6
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    font.family: theme.family
    font.pixelSize: 14
    font.weight: Font.Normal
    hoverEnabled: true
    contentItem: Text {
        text: item.text
        font: item.font
        color: !item.enabled ? theme.placeholder : item.destructive ? "#B44852" : theme.text
        textFormat: Text.PlainText
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 8
        color: !item.enabled ? "transparent" : item.down ? theme.selection
             : item.highlighted || item.hovered ? (item.destructive ? "#FFF2F2" : theme.subtle) : "transparent"
    }
}
