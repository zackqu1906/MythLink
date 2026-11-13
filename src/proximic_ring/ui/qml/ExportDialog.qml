import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaDialog {
    id: dialog
    required property var selection
    objectName: "exportApplicationDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(640, parent.width - 48)
    height: Math.min(selection.kind === "diagnostic" ? 520 : 680, parent.height - 48)
    modal: true
    focus: true
    z: 40
    popupType: Popup.Item
    closePolicy: Popup.CloseOnEscape
    title: selection.title
    readonly property bool compact: height < 600
    topPadding: compact ? 12 : 24
    bottomPadding: compact ? 16 : 24
    onAboutToHide: selection.cancel()
    FigmaTheme { id: theme }
    Connections {
        target: dialog.selection
        function onChanged() {
            if (dialog.selection.active) dialog.open()
            else if (!dialog.selection.active && dialog.visible) dialog.close()
        }
    }
    contentItem: ColumnLayout {
        spacing: dialog.compact ? 6 : 16
        Label {
            Layout.fillWidth: true
            text: dialog.selection.kind === "diagnostic"
                ? "按时间保留所有应用的运行日志，便于还原完整的问题经过。"
                : dialog.selection.kind === "voice" ? "选择本次导出的应用，保留完整语音记录及已有录音、截图。"
                : "选择本次导出的应用，保留手势、场景、动作及你的标注。"
            color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 8
            Label { text: "时间范围"; color: theme.text; font.weight: Font.Medium }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: [{key: "today", label: "今天"}, {key: "week", label: "近一周"}, {key: "all", label: "全部"}]
                    FigmaAction {
                        required property var modelData
                        objectName: "exportRange_" + modelData.key
                        Layout.fillWidth: true
                        text: modelData.label
                        checkable: true; checked: dialog.selection.period === modelData.key
                        primary: checked
                        onClicked: dialog.selection.setPeriod(modelData.key)
                    }
                }
            }
            Label {
                Layout.fillWidth: true; color: theme.muted; font.pixelSize: 12
                text: dialog.selection.period === "today" ? "从今天零点开始，按本机时间计算。"
                    : dialog.selection.period === "week" ? "包含今天及前六个自然日。" : "导出本地仍保留的全部记录。"
            }
        }
        ColumnLayout {
            visible: dialog.selection.kind !== "diagnostic"
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Label { text: "选择应用"; color: theme.text; font.weight: Font.Medium }
                Label { text: "已选 " + dialog.selection.selectedKeys.length + " 项"; color: theme.muted; font.pixelSize: 12 }
                Item { Layout.fillWidth: true }
                FigmaAction { objectName: "exportSelectAll"; text: "全选"; quiet: true; implicitHeight: 28; enabled: !dialog.selection.loading && dialog.selection.applications.length > 0; onClicked: dialog.selection.selectAll(true) }
                FigmaAction { text: "清空"; quiet: true; implicitHeight: 28; enabled: dialog.selection.selectedKeys.length > 0; onClicked: dialog.selection.selectAll(false) }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: dialog.compact ? 80 : 96
                radius: 10; color: theme.background; border.color: theme.line
                ListView {
                    id: applications
                    objectName: "exportApplicationList"
                    anchors.fill: parent; anchors.margins: 6
                    clip: true; spacing: 4
                    model: dialog.selection.applications
                    ScrollBar.vertical: ScrollBar { }
                    delegate: AbstractButton {
                        id: row
                        required property var modelData
                        required property int index
                        objectName: "exportApplication_" + index
                        width: applications.width; height: modelData.website ? 66 : 56
                        hoverEnabled: true
                        checked: dialog.selection.selectedKeys.indexOf(modelData.key) !== -1
                        Accessible.name: modelData.label
                        Accessible.role: Accessible.CheckBox
                        Accessible.checkable: true
                        Accessible.checked: checked
                        onClicked: dialog.selection.toggle(modelData.key)
                        background: Rectangle {
                            radius: 7
                            color: row.checked ? theme.subtle : row.hovered ? theme.subtle : "transparent"
                            border.width: row.visualFocus ? 2 : 0; border.color: theme.primary
                        }
                        contentItem: RowLayout {
                            spacing: 12
                            Item { Layout.preferredWidth: 0 }
                            ApplicationIcon {
                                objectName: "exportApplicationIcon_" + row.index
                                Layout.preferredWidth: 36; Layout.preferredHeight: 36
                                bundle: row.modelData.bundle; applicationPath: row.modelData.path; label: row.modelData.name
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Label { Layout.fillWidth: true; text: row.modelData.name; textFormat: Text.PlainText; elide: Text.ElideRight; color: theme.text; font.pixelSize: 14; font.weight: Font.Medium }
                                Label { Layout.fillWidth: true; visible: row.modelData.website.length > 0; text: row.modelData.website; textFormat: Text.PlainText; elide: Text.ElideRight; color: theme.muted; font.pixelSize: 12 }
                            }
                            Rectangle {
                                Layout.preferredWidth: 20; Layout.preferredHeight: 20; Layout.rightMargin: 12
                                radius: 5; color: row.checked ? theme.primary : theme.background
                                border.color: row.checked ? theme.primary : theme.line
                                Label { anchors.centerIn: parent; text: "✓"; visible: row.checked; color: "white"; font.pixelSize: 14 }
                            }
                        }
                    }
                }
                Label {
                    anchors.centerIn: parent; width: parent.width - 40
                    horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; color: theme.muted
                    visible: dialog.selection.loading || dialog.selection.applications.length === 0
                    text: dialog.selection.loading ? "正在读取可导出的应用…" : dialog.selection.error || "暂无可确认应用归属的记录"
                }
            }
        }
        Rectangle {
            visible: dialog.selection.kind === "diagnostic"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 100
            radius: 10; color: theme.subtle
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 16; spacing: 8
                Label { text: "诊断包包含"; color: theme.text; font.weight: Font.Medium }
                Label { Layout.fillWidth: true; text: "运行日志与错误堆栈、设备和服务状态、运行配置，以及已有的输入法诊断。"; color: theme.muted; wrapMode: Text.Wrap; font.pixelSize: 13 }
                Label { Layout.fillWidth: true; text: "语音录音、截图和手势记录请从各自的导出入口按应用选择。"; color: theme.muted; wrapMode: Text.Wrap; font.pixelSize: 13 }
                Item { Layout.fillHeight: true }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 8
            Label { text: "保存位置"; color: theme.text; font.weight: Font.Medium }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 40
                    radius: 8; color: theme.background; border.color: theme.line
                    Label {
                        objectName: "exportDestinationFolder"
                        anchors.fill: parent; anchors.margins: 12; verticalAlignment: Text.AlignVCenter
                        text: dialog.selection.directory; textFormat: Text.PlainText; elide: Text.ElideMiddle; color: theme.muted; font.pixelSize: 13
                    }
                    HoverHandler { id: folderHover }
                    ToolTip.visible: folderHover.hovered
                    ToolTip.text: dialog.selection.directory
                }
                FigmaAction { objectName: "chooseExportFolderButton"; text: "选择文件夹"; onClicked: dialog.selection.chooseDirectory() }
            }
        }
        Label {
            Layout.fillWidth: true; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
            text: dialog.selection.kind === "diagnostic"
                ? "日志可能含应用名称和文本片段，分享前请检查。仅保存到本机。"
                : "归属不明或涉及未选应用的记录会排除。选中记录的正文与附件保持原样，仅保存到本机。"
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Item { Layout.fillWidth: true }
            FigmaAction { objectName: "cancelExportButton"; text: "取消"; onClicked: dialog.selection.cancel() }
            FigmaAction {
                objectName: "confirmExportButton"; primary: true
                text: dialog.selection.kind === "diagnostic" ? "导出诊断包" : "导出完整记录"
                enabled: dialog.selection.canExport
                onClicked: dialog.selection.accept()
            }
        }
    }
}
