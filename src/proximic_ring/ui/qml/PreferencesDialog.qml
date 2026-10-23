import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Figma 259:7478 / 259:7570 / 259:7610 share the same window geometry.
FigmaDialog {
    id: dialog
    property int section: 0
    property bool showBack: false
    property string backObjectName: ""
    property string closeObjectName: "preferencesCloseButton"
    signal accountRequested()
    signal settingsRequested()
    signal backRequested()
    signal closeRequested()
    onCloseRequested: close()
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(1080, parent.width - 48)
    height: Math.min(720, parent.height - 48)
    modal: true; popupType: Popup.Item
    leftPadding: 256; rightPadding: 32; topPadding: 88; bottomPadding: 36
    header: null; footer: null
    FigmaTheme { id: theme }
    background: Rectangle {
        radius: 25; color: theme.background; border.color: theme.line
        UiWindowDragArea { anchors.fill: parent }
        Rectangle {
            width: 224; height: parent.height; radius: 25; color: theme.subtle
            Rectangle { anchors.right: parent.right; width: 25; height: parent.height; color: theme.subtle }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 20; anchors.topMargin: 32; spacing: 16
                Label { text: "偏好设置"; Layout.preferredHeight: 32; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium; verticalAlignment: Text.AlignVCenter }
                Repeater {
                    model: [{text: "账户", icon: "account"}, {text: "设置", icon: "preference-settings"}, {text: "帮助", icon: "preference-help"}]
                    AbstractButton {
                        required property int index
                        required property var modelData
                        objectName: "preferencesNav" + index
                        Layout.fillWidth: true; implicitHeight: 44
                        leftPadding: 12; rightPadding: 12
                        hoverEnabled: true; checked: dialog.section === index
                        Accessible.name: modelData.text
                        background: Rectangle {
                            radius: 8
                            color: parent.checked || parent.down ? theme.selection : parent.hovered ? theme.subtle : "transparent"
                            border.width: parent.visualFocus ? 2 : 0; border.color: theme.primary
                        }
                        contentItem: RowLayout {
                            spacing: 12
                            FigmaIcon { symbol: modelData.icon; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                            Label { Layout.fillWidth: true; text: modelData.text; color: theme.text; font.pixelSize: 16; font.weight: Font.Medium }
                        }
                        onClicked: {
                            if (index === 0) dialog.accountRequested()
                            else if (index === 1) dialog.settingsRequested()
                            else dialog.helpRequested()
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }
        RowLayout {
            x: 256; y: 32; width: parent.width - 288; height: 36; spacing: 8
            FigmaAction {
                visible: dialog.showBack; objectName: dialog.backObjectName
                quiet: true; symbol: "chevron-left"; Layout.preferredWidth: 28; Layout.preferredHeight: 28
                Accessible.name: "返回"; onClicked: dialog.backRequested()
            }
            Label { Layout.fillWidth: true; text: dialog.title; color: theme.text; font.pixelSize: 28; font.weight: Font.Bold; elide: Text.ElideRight }
            FigmaAction {
                objectName: dialog.closeObjectName
                Layout.alignment: Qt.AlignTop; Layout.preferredWidth: 20; Layout.preferredHeight: 20
                quiet: true; symbol: "preference-close"; Accessible.name: "关闭"
                onClicked: dialog.closeRequested()
            }
        }
    }
}
