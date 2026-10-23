import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic

// Gesture Matrix v6: original exported glyph in its 24 px illustration slot.
Basic.Button {
    id: chip
    required property var definition
    required property string selectedGesture
    property string hint: ""
    property bool doubleTrigger: false
    signal gestureChosen(string gesture)
    FigmaTheme { id: theme }
    readonly property bool segmented: Boolean(definition.doubleKey)
    readonly property bool selected: selectedGesture === definition.key || (segmented && selectedGesture === definition.doubleKey)
    readonly property string currentGesture: segmented && doubleTrigger ? definition.doubleKey : definition.key
    onSelectedGestureChanged: if (selected) doubleTrigger = selectedGesture === definition.doubleKey
    objectName: "gestureCard_" + definition.key
    width: definition.width; height: 44
    padding: 0; hoverEnabled: true
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    Accessible.name: definition.title + (segmented ? (doubleTrigger ? "，双次" : "，单次") : "") + "，" + hint
    ToolTip.visible: hovered; ToolTip.delay: 450; ToolTip.text: hint
    onClicked: gestureChosen(currentGesture)
    background: Rectangle {
        radius: 12
        color: chip.down ? theme.selection : chip.selected ? theme.selection : chip.hovered ? theme.subtle : chip.definition.touch ? theme.subtle : theme.surface
        border.color: chip.visualFocus || chip.selected ? theme.primary : chip.definition.outline || theme.line
        border.width: chip.visualFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: 90 } }
    }
    contentItem: Item {
        Item {
            x: 12; width: 24; height: 24; anchors.verticalCenter: parent.verticalCenter
            Image {
                objectName: "gestureGlyph_" + chip.definition.key
                anchors.centerIn: parent
                anchors.horizontalCenterOffset: chip.definition.dx || 0
                anchors.verticalCenterOffset: chip.definition.dy || 0
                width: chip.definition.iw; height: chip.definition.ih
                source: "../assets/figma/redesign-567-33097-" + chip.definition.asset + ".svg"
                smooth: true
            }
        }
        Label {
            x: 44; height: 28; anchors.verticalCenter: parent.verticalCenter
            text: chip.definition.title; font.family: theme.family; font.pixelSize: 18; font.weight: Font.Medium
            color: theme.text; verticalAlignment: Text.AlignVCenter
        }
        Rectangle {
            visible: chip.segmented
            anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
            width: chip.definition.key === "tap" ? 56 : 59; height: 32; radius: 8; color: theme.sidebar
            Row {
                anchors.fill: parent; anchors.margins: 4
                Repeater {
                    model: [false, true]
                    Basic.Button {
                        id: trigger
                        required property bool modelData
                        objectName: modelData ? "gestureCard_" + chip.definition.doubleKey : "gestureSingle_" + chip.definition.key
                        width: (parent.width) / 2; height: 24; padding: 0
                        topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0; hoverEnabled: true
                        checked: chip.doubleTrigger === modelData
                        Accessible.name: chip.definition.title + (modelData ? "，双次" : "，单次")
                        onClicked: { chip.doubleTrigger = modelData; chip.gestureChosen(chip.currentGesture) }
                        background: Rectangle { radius: 4; color: trigger.down ? theme.selection : trigger.checked ? theme.surface : trigger.hovered ? theme.selection : "transparent"; border.width: trigger.visualFocus ? 1 : 0; border.color: theme.primary }
                        contentItem: Label { text: trigger.modelData ? "双" : "单"; font.family: theme.family; font.pixelSize: 14; font.weight: Font.Medium; color: trigger.checked ? theme.primary : theme.muted; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                }
            }
        }
    }
}
