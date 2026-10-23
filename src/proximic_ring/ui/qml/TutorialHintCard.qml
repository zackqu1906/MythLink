import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    required property var hint
    // ASR partials within the same guidance state must not restart the animation.
    readonly property string hintSignature: JSON.stringify([hint.id, hint.title, hint.text, hint.gestures, hint.functionAsset, hint.tone])
    property bool initialized: false
    onHintSignatureChanged: if (initialized && visible) hintTransition.restart()
    Component.onCompleted: initialized = true
    implicitHeight: Math.max(200, contents.implicitHeight + 32)
    radius: 12; color: theme.surface; border.color: theme.line
    Behavior on implicitHeight { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
    FigmaTheme { id: theme }
    ColumnLayout {
        id: contents
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
        spacing: 16
        Label { text: "当前操作提示"; color: theme.muted; font.pixelSize: 14 }
        ColumnLayout {
            id: hintContent
            objectName: "tutorialHintContent"
            Layout.fillWidth: true; spacing: 12
            transform: Translate { id: hintOffset }
            VoiceGesturePreview {
                objectName: "tutorialCurrentGesture"
                Layout.alignment: Qt.AlignHCenter
                visible: gestures.length > 0
                gestures: card.hint.gestures || []
                functionAsset: card.hint.functionAsset || ""
            }
            Label {
                objectName: "tutorialHintTitle"
                Layout.fillWidth: true
                text: card.hint.title || ""
                font.pixelSize: 20; font.weight: Font.Medium; wrapMode: Text.Wrap
                color: card.hint.tone === "success" ? theme.success : theme.text
            }
            Label {
                objectName: "tutorialLiveHint"
                Layout.fillWidth: true
                text: card.hint.text || ""
                color: theme.text; font.pixelSize: 16; wrapMode: Text.Wrap
            }
        }
    }
    ParallelAnimation {
        id: hintTransition
        objectName: "tutorialHintTransition"
        NumberAnimation { target: hintContent; property: "opacity"; from: 0.55; to: 1; duration: 120; easing.type: Easing.OutCubic }
        NumberAnimation { target: hintOffset; property: "y"; from: 5; to: 0; duration: 120; easing.type: Easing.OutCubic }
    }
}
