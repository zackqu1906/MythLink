import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaDialog {
    id: dialog
    property string subtitle: ""
    property string symbol: "settings"
    property bool showBack: false
    property string backObjectName: ""
    property string doneObjectName: ""
    property string doneText: "完成"
    property string footerHint: "设置自动保存"
    signal backRequested()
    signal doneRequested()
    FigmaTheme { id: theme }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(980, parent.width - 48)
    height: Math.min(780, parent.height - 48)
    modal: true
    popupType: Popup.Item
    padding: 0
    spacing: 0
    font.family: theme.family
    Overlay.modal: Rectangle { color: "#660F172A" }
    background: Rectangle {
        radius: 20
        color: theme.background
        border.color: theme.line
        UiWindowDragArea { anchors.fill: parent }
    }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }

    header: Item {
        implicitHeight: heading.implicitHeight + 48
        Rectangle {
            anchors.fill: parent
            radius: 20; color: theme.surface
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 20; color: parent.color }
        }
        RowLayout {
            id: heading
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 28; rightMargin: 28 }
            spacing: 14
            FigmaAction {
                objectName: dialog.backObjectName
                visible: dialog.showBack
                quiet: true
                Layout.preferredWidth: 36; Layout.preferredHeight: 36
                leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                contentItem: Item {
                    FigmaIcon {
                        anchors.centerIn: parent
                        width: 20; height: 20
                        symbol: "chevron-left"
                    }
                }
                Accessible.name: "返回"
                ToolTip.visible: hovered; ToolTip.text: "返回"
                onClicked: dialog.backRequested()
            }
            Rectangle {
                Layout.preferredWidth: 44; Layout.preferredHeight: 44
                radius: 12; color: "#EDF1FF"
                FigmaIcon { anchors.centerIn: parent; width: 23; height: 23; symbol: dialog.symbol === "voice" || dialog.symbol === "mic" ? "mic" : "settings" }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0
                spacing: 5
                Label {
                    Layout.fillWidth: true
                    text: dialog.title
                    color: theme.text; font.pixelSize: 28; font.weight: Font.DemiBold; wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: dialog.subtitle
                    color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
                }
            }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.line }
    }
    footer: Item {
        implicitHeight: 76
        Rectangle {
            anchors.fill: parent
            radius: 20; color: theme.surface
            Rectangle { anchors.top: parent.top; width: parent.width; height: 20; color: parent.color }
        }
        Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: theme.line }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 28; anchors.rightMargin: 28
            spacing: 16
            Label {
                Layout.fillWidth: true
                text: dialog.footerHint
                color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
            }
            FigmaAction {
                objectName: dialog.doneObjectName
                Layout.preferredWidth: 96; Layout.preferredHeight: 40
                topPadding: 10; bottomPadding: 10
                text: dialog.doneText; primary: true
                onClicked: dialog.doneRequested()
            }
        }
    }
}
