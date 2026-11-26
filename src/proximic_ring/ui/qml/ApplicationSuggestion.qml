import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    implicitWidth: 360
    implicitHeight: actions.implicitHeight + 20
    radius: 12
    color: theme.surface
    border.color: theme.line
    UiTheme { id: theme }

    RowLayout {
        id: actions
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 10
        spacing: 6

        Label {
            objectName: "applicationSuggestionQuestion"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: onboarding.offer.label ? "为「" + onboarding.offer.label + "」配置手势？" : "为此应用配置手势？"
            color: theme.text
            font.pixelSize: 14
            textFormat: Text.PlainText
            elide: Text.ElideMiddle
        }
        UiAction {
            objectName: "dismissApplicationSuggestion"
            text: "暂不"
            quiet: true
            Layout.minimumWidth: implicitWidth
            implicitHeight: 32
            focusPolicy: Qt.NoFocus
            onClicked: onboarding.dismiss()
        }
        UiAction {
            objectName: "acceptApplicationSuggestion"
            text: "配置"
            primary: true
            Layout.minimumWidth: implicitWidth
            implicitHeight: 32
            focusPolicy: Qt.NoFocus
            onClicked: onboarding.accept()
        }
    }
}
