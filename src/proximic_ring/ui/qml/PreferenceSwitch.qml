import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.Switch {
    id: control
    implicitWidth: 44; implicitHeight: 24; padding: 0; hoverEnabled: true
    readonly property var assets: ({
        "off": "redesign-257-3465-checked-false-state-default.svg",
        "off-hover": "redesign-257-3465-checked-false-state-hover.svg",
        "off-focus": "redesign-257-3465-checked-false-state-focus.svg",
        "off-disabled": "redesign-257-3465-checked-false-state-disabled.svg",
        "on": "redesign-257-3465-checked-true-state-default.svg",
        "on-focus": "redesign-257-3465-checked-true-state-focus.svg",
        "on-disabled": "redesign-257-3465-checked-true-state-disabled.svg"
    })
    readonly property string stateAsset: (checked ? "on" : "off")
        + (!enabled ? "-disabled" : visualFocus ? "-focus" : !checked && hovered && !down ? "-hover" : "")
    indicator: Image { x: (control.width - width) / 2; y: (control.height - height) / 2; source: "../assets/figma/" + control.assets[control.stateAsset] }
    contentItem: Item { }
}
