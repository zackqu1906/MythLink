import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: button
    required property string description
    implicitHeight: 76
    leftPadding: 18; rightPadding: 18; topPadding: 12; bottomPadding: 12
    background: Rectangle {
        radius: 12
        color: button.down ? "#E3E9FB" : button.hovered ? "#EBEFFB" : "#F5F7FD"
        border.color: button.activeFocus ? "#082ACB" : "#E1E5F0"
        border.width: 1
    }
    contentItem: RowLayout {
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true; spacing: 5
            Label { text: button.text; color: "#171C28"; font.pixelSize: 15; font.bold: true }
            Label {
                Layout.fillWidth: true
                text: button.description; color: "#687286"; font.pixelSize: 12
                wrapMode: Text.Wrap
            }
        }
        Label { text: "›"; color: "#687286"; font.pixelSize: 24 }
    }
}
