import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic

// Voice bindings are shared across scopes; show both saved functions with context.
Basic.Button {
    id: chip
    required property var definition
    required property string selectedGesture
    property string hint: ""
    property string mappingLabel: ""
    property string voiceMappingLabel: ""
    property bool voiceActive: false
    property bool voiceEditable: true
    readonly property bool hasVoice: voiceMappingLabel.length > 0
    readonly property bool dualMapping: hasVoice && mappingLabel.length > 0
    property bool doubleTrigger: false
    signal gestureChosen(string gesture)
    signal mappingChosen(string gesture, bool voice)
    FigmaTheme { id: theme }
    readonly property bool segmented: Boolean(definition.doubleKey)
    readonly property bool selected: selectedGesture === definition.key || (segmented && selectedGesture === definition.doubleKey)
    readonly property string currentGesture: segmented && doubleTrigger ? definition.doubleKey : definition.key
    onSelectedGestureChanged: if (selectedGesture === definition.key || selectedGesture === definition.doubleKey) doubleTrigger = selectedGesture === definition.doubleKey
    objectName: "gestureCard_" + definition.key
    width: definition.width; height: dualMapping ? 84 : 76
    padding: 0; hoverEnabled: true
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    Accessible.name: definition.title + (segmented ? (doubleTrigger ? "，双次" : "，单次") : "") + "，" + hint
    ToolTip.visible: hovered; ToolTip.delay: 450; ToolTip.text: hint
    function activateGesture() {
        if (voiceEditable && hasVoice && !dualMapping) mappingChosen(currentGesture, true)
        else gestureChosen(currentGesture)
    }
    onClicked: activateGesture()
    background: Rectangle {
        radius: 12
        color: chip.down ? theme.selection : chip.selected ? theme.selection : chip.hovered ? theme.subtle : chip.definition.touch ? theme.subtle : theme.surface
        border.color: chip.visualFocus || chip.selected ? theme.primary : chip.definition.outline || theme.line
        border.width: chip.visualFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: 90 } }
    }
    contentItem: Item {
        Item {
            x: 12; y: chip.dualMapping ? 10 : 16; width: 24; height: 24
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
            x: 44; y: chip.dualMapping ? 8 : 14; height: 28
            text: chip.definition.title; font.family: theme.family; font.pixelSize: 18; font.weight: Font.Medium
            color: theme.text; verticalAlignment: Text.AlignVCenter
        }
        Rectangle {
            visible: chip.segmented
            anchors.right: parent.right; anchors.rightMargin: 12; y: chip.dualMapping ? 6 : 12
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
                        onClicked: { chip.doubleTrigger = modelData; chip.activateGesture() }
                        background: Rectangle { radius: 4; color: trigger.down ? theme.selection : trigger.checked ? theme.surface : trigger.hovered ? theme.selection : "transparent"; border.width: trigger.visualFocus ? 1 : 0; border.color: theme.primary }
                        contentItem: Label { text: trigger.modelData ? "双" : "单"; font.family: theme.family; font.pixelSize: 14; font.weight: Font.Medium; color: trigger.checked ? theme.primary : theme.muted; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                }
            }
        }
        Label {
            objectName: "gestureVoiceContext_" + chip.definition.key
            visible: chip.hasVoice
            x: 12; y: chip.dualMapping ? 40 : 42; width: 36; height: chip.dualMapping ? 18 : 22
            text: chip.voiceActive ? "输入时" : "语音"
            font.family: theme.family; font.pixelSize: 12; color: theme.primary
            verticalAlignment: Text.AlignVCenter
            TapHandler { enabled: chip.voiceEditable; onTapped: chip.mappingChosen(chip.currentGesture, true) }
            HoverHandler { cursorShape: chip.voiceEditable ? Qt.PointingHandCursor : Qt.ArrowCursor }
        }
        Label {
            objectName: "gestureVoiceMappingLabel_" + chip.definition.key
            visible: chip.hasVoice
            x: 54; y: chip.dualMapping ? 40 : 42; width: parent.width - x - 12; height: chip.dualMapping ? 18 : 22
            text: chip.voiceMappingLabel
            font.family: theme.family; font.pixelSize: chip.dualMapping ? 13 : 14; color: theme.primary
            verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            TapHandler { enabled: chip.voiceEditable; onTapped: chip.mappingChosen(chip.currentGesture, true) }
            HoverHandler { cursorShape: chip.voiceEditable ? Qt.PointingHandCursor : Qt.ArrowCursor }
        }
        Label {
            objectName: "gestureRegularContext_" + chip.definition.key
            visible: chip.dualMapping
            x: 12; y: 58; width: 36; height: 18
            text: chip.voiceActive ? "其他时" : "常规"
            font.family: theme.family; font.pixelSize: 12; color: theme.muted
            verticalAlignment: Text.AlignVCenter
            TapHandler { onTapped: chip.mappingChosen(chip.currentGesture, false) }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }
        Label {
            objectName: "gestureMappingLabel_" + chip.definition.key
            visible: !chip.hasVoice || chip.dualMapping
            x: chip.dualMapping ? 54 : 44; y: chip.dualMapping ? 58 : 42
            width: parent.width - x - 12; height: chip.dualMapping ? 18 : 22
            text: chip.mappingLabel || "未配置"
            font.family: theme.family; font.pixelSize: chip.dualMapping ? 13 : 14; color: theme.muted
            verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            TapHandler { onTapped: chip.mappingChosen(chip.currentGesture, false) }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }
    }
}
