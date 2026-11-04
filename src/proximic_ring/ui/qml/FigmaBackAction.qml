import QtQuick
import QtQuick.Controls

FigmaAction {
    text: "返回"
    symbol: "chevron-left"
    implicitWidth: 80
    implicitHeight: 36
    leftPadding: 10
    rightPadding: 14
    Accessible.name: "返回"
    ToolTip.visible: (enabled && hovered)
    ToolTip.delay: 500
    ToolTip.text: "返回上一页"
}
