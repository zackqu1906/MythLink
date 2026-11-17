import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaDialog {
    id: dialog
    required property var selection
    objectName: "exportApplicationDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(selection.kind === "diagnostic" ? 640 : 1040, parent.width - 48)
    height: Math.min(selection.kind === "diagnostic" ? 520 : 820, parent.height - 48)
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
        spacing: dialog.compact ? 6 : dialog.selection.kind === "diagnostic" ? 16 : 12
        Label {
            Layout.fillWidth: true
            text: dialog.selection.kind === "diagnostic"
                ? "按时间保留所有应用的运行日志，便于还原完整的问题经过。"
                : dialog.selection.kind === "voice" ? "选择本次导出的应用，保留完整语音记录及已有录音、截图。"
                : "选择本次导出的应用，保留手势、场景、动作及你的标注。"
            color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
        }
        GridLayout {
            id: timeRangeLayout
            Layout.fillWidth: true
            columns: dialog.selection.kind === "diagnostic" ? 1 : 2
            columnSpacing: 12; rowSpacing: 8
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
                Layout.columnSpan: timeRangeLayout.columns
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
                GridView {
                    id: applications
                    objectName: "exportApplicationList"
                    anchors.fill: parent; anchors.margins: 8
                    anchors.rightMargin: 16
                    clip: true
                    cellWidth: width / Math.max(1, Math.floor(width / 200))
                    cellHeight: 64
                    model: dialog.selection.applications
                    ScrollBar.vertical: ScrollBar { }
                    delegate: AbstractButton {
                        id: tile
                        required property var modelData
                        required property int index
                        objectName: "exportApplication_" + index
                        width: applications.cellWidth - 8; height: applications.cellHeight - 8
                        padding: 10
                        hoverEnabled: true
                        checked: dialog.selection.selectedKeys.indexOf(modelData.key) !== -1
                        Accessible.name: modelData.label
                        Accessible.role: Accessible.CheckBox
                        Accessible.checkable: true
                        Accessible.checked: checked
                        onClicked: dialog.selection.toggle(modelData.key)
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: modelData.label
                        background: Rectangle {
                            radius: 8
                            color: tile.checked ? theme.subtle : tile.hovered ? theme.subtle : theme.surface
                            border.width: tile.visualFocus ? 2 : 1
                            border.color: tile.checked || tile.visualFocus ? theme.primary : theme.line
                        }
                        contentItem: RowLayout {
                            spacing: 8
                            ApplicationIcon {
                                objectName: "exportApplicationIcon_" + tile.index
                                Layout.preferredWidth: 30; Layout.preferredHeight: 30
                                bundle: tile.modelData.bundle; applicationPath: tile.modelData.path; label: tile.modelData.name
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 2
                                Label { Layout.fillWidth: true; text: tile.modelData.name; textFormat: Text.PlainText; elide: Text.ElideRight; color: theme.text; font.pixelSize: 13; font.weight: Font.Medium }
                                Label { Layout.fillWidth: true; visible: tile.modelData.website.length > 0; text: tile.modelData.website; textFormat: Text.PlainText; elide: Text.ElideRight; color: theme.muted; font.pixelSize: 11 }
                            }
                            Rectangle {
                                Layout.preferredWidth: 18; Layout.preferredHeight: 18
                                radius: 4; color: tile.checked ? theme.primary : theme.background
                                border.color: tile.checked ? theme.primary : theme.line
                                Label { anchors.centerIn: parent; text: "✓"; visible: tile.checked; color: "white"; font.pixelSize: 12 }
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
        GridLayout {
            Layout.fillWidth: true
            columns: dialog.selection.kind === "diagnostic" ? 1 : 2
            columnSpacing: 12; rowSpacing: 8
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
