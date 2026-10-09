import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    implicitWidth: 382; implicitHeight: content.implicitHeight + 40
    width: implicitWidth; height: implicitHeight
    radius: 16; color: theme.surface; border.color: theme.line
    UiTheme { id: theme }
    readonly property var app: onboarding.offer
    readonly property var purposes: onboarding.options
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 20; spacing: 12
        RowLayout {
            ApplicationIcon { Layout.preferredWidth: 38; Layout.preferredHeight: 38; bundle: card.app.value || ""; applicationPath: card.app.path || ""; label: card.app.label || "" }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 3
                Label { text: "为这个应用配置手势？"; color: theme.text; font.pixelSize: 17; font.weight: Font.DemiBold }
                Label { Layout.fillWidth: true; text: card.app.label || ""; color: theme.muted; font.pixelSize: 12; elide: Text.ElideRight }
            }
        }
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.muted; font.pixelSize: 12
            text: card.purposes.length ? "推荐用途：" + (card.purposes.filter(function(s) { return s.value === card.app.primaryScene })[0] || {}).label + "。确认后配置以下手势。"
                : onboarding.preview.length ? "按网页状态使用对应手势，确认后可在「场景与手势」中调整。" : "添加后读取应用公开的快捷键，再选择要绑定的动作。"
        }
        Flow {
            Layout.fillWidth: true; spacing: 6; visible: card.purposes.length > 1
            Repeater {
                model: card.purposes
                UiAction {
                    required property var modelData
                    text: modelData.label; primary: modelData.value === card.app.primaryScene
                    implicitHeight: 30; focusPolicy: Qt.NoFocus
                    onClicked: onboarding.chooseScene(modelData.value)
                }
            }
        }
        ScrollView {
            ScrollBar.vertical.policy: ScrollBar.AlwaysOff
            Layout.fillWidth: true; Layout.preferredHeight: Math.min(180, previewColumn.implicitHeight)
            visible: onboarding.preview.length > 0; clip: true
            contentWidth: availableWidth
            ColumnLayout {
                id: previewColumn
                width: parent.width; spacing: 8
                Repeater {
                    model: onboarding.preview
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: 8
                        Label { text: modelData.gesture; color: theme.muted; font.pixelSize: 12 }
                        Label { Layout.fillWidth: true; text: modelData.action; color: theme.text; font.pixelSize: 12; elide: Text.ElideRight }
                        Label { text: modelData.occupied ? "全局占用" : modelData.shortcut; color: modelData.pending || modelData.occupied ? theme.warning : theme.primary; font.pixelSize: 11 }
                    }
                }
            }
        }
        Label { Layout.fillWidth: true; text: "仅在对应场景和此应用前台生效。缺少的快捷键将等待菜单读取，不会自动试按。"; wrapMode: Text.Wrap; color: theme.muted; font.pixelSize: 11; visible: onboarding.preview.length > 0 }
        RowLayout {
            Layout.fillWidth: true
            UiAction { objectName: "dismissApplicationSuggestion"; text: "暂不配置"; focusPolicy: Qt.NoFocus; onClicked: onboarding.dismiss() }
            Item { Layout.fillWidth: true }
            UiAction { objectName: "acceptApplicationSuggestion"; text: onboarding.preview.length ? "配置手势" : "添加应用"; primary: true; focusPolicy: Qt.NoFocus; onClicked: onboarding.accept() }
        }
        Label { text: "可随时在「场景与手势」中添加或修改"; color: theme.muted; font.pixelSize: 10 }
    }
}
