import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic

Basic.Switch {
    id: control
    implicitWidth: 40; implicitHeight: 24
    padding: 0
    hoverEnabled: true
    readonly property string stateAsset: !enabled ? "disabled"
        : (checked ? "on" : "off") + (visualFocus ? "-focus" : down ? "-pressed" : hovered ? "-hover" : "")
    indicator: Image {
        x: (control.width - width) / 2; y: (control.height - height) / 2
        source: "../assets/figma/redesign-381-4535-state-" + control.stateAsset + ".svg"
    }
    contentItem: Item { }
}
