import QtQuick
import QtQuick.Window

// Pending stroke symbols are primary; the surface is supporting chrome.
// Reuse FigmaTheme colors/family and 4/8 spacing, 20px type, 8px radius.
// Display only: keep the editor focused and the candidate grid stationary.
Window {
    id: sequence
    objectName: "strokeSequencePopup"
    required property var touchpad
    readonly property var caret: touchpad.externalStrokeSession.caret
    readonly property rect bounds: touchpad.externalStrokeSession.strokeSequenceBounds(caret)
    readonly property rect placement: touchpad.externalStrokeSession.strokeSequenceGeometry(caret, width, height)
    readonly property string symbols: touchpad.strokeCode.split("𠃍").join("乛")
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
           | Qt.WindowTransparentForInput | Qt.WindowStaysOnTopHint
    transientParent: null
    color: "transparent"
    visible: bounds.width > 0 && symbols.length > 0
    x: placement.x
    y: placement.y
    width: Math.max(0, Math.min(480, bounds.width, Math.ceil(measure.advanceWidth) + 16))
    height: Math.min(bounds.height, Math.ceil(label.implicitHeight) + 8)
    FigmaTheme { id: theme }
    TextMetrics { id: measure; text: sequence.symbols; font: label.font }
    Rectangle {
        anchors.fill: parent
        color: theme.surface
        border.color: theme.line
        radius: 8
        clip: true
        Text {
            id: label
            objectName: "externalStrokeSequenceLabel"
            x: 8; y: 4
            width: Math.max(0, sequence.width - 16)
            text: sequence.symbols
            font.family: theme.family
            font.pixelSize: 20
            font.weight: Font.Medium
            color: theme.primary
            wrapMode: Text.Wrap
            Accessible.name: "已输入笔画 " + text
        }
    }
}
