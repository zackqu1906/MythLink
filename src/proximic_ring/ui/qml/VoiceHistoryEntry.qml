import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: row
    required property var entry
    required property var controller
    property bool compact: false
    property bool detailsVisible: false
    property bool textExpanded: false
    property string displayedId: ""
    signal deleteRequested(var record)
    onEntryChanged: {
        var identity = String(entry.interactionId || entry.id || "")
        if (identity !== displayedId) {
            displayedId = identity
            textExpanded = false
            detailsVisible = false
        }
    }
    readonly property bool editEntry: entry.mode === "edit"
    readonly property bool nativeUndoSent: entry.outcome === "native_undo_sent"
    readonly property bool undoneEntry: entry.outcome === "undone" || nativeUndoSent
    readonly property bool appliedEntry: entry.outcome === "applied" || entry.outcome === "confirm"
    readonly property bool failedEntry: entry.outcome === "apply_failed" || entry.outcome === "abandoned"
    readonly property bool cancelledEntry: entry.outcome === "cancelled" || entry.outcome === "cancel"
    readonly property bool resultAvailable: appliedEntry && (entry.candidateAvailable !== undefined
                                                            ? Boolean(entry.candidateAvailable) : Boolean(entry.candidateText))
    readonly property bool resultEmpty: Boolean(entry.candidateEmpty)
    readonly property string copyText: resultAvailable && !undoneEntry
                                       ? (resultEmpty ? "" : String(entry.candidateText || ""))
                                       : String(entry.text || "")
    readonly property string outcomeText: nativeUndoSent ? "已发送撤销" : undoneEntry ? "已撤回"
                                          : failedEntry ? "未完成" : cancelledEntry ? "已取消" : ""
    UiTheme { id: theme }
    objectName: "voiceHistoryEntry"
    implicitHeight: content.implicitHeight + (compact ? 28 : 36)
    height: implicitHeight
    radius: 12
    color: theme.surface
    border.color: theme.line

    // Copying a record is a UI action; it does not reuse the live dictation/clipboard pipeline.
    TextEdit { id: copyBuffer; visible: false; readOnly: true; textFormat: TextEdit.PlainText }
    Timer { id: copiedNotice; interval: 1600 }
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: row.compact ? 14 : 18
        spacing: 10
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Label {
                objectName: "voiceHistoryMetadata"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                text: (row.entry.displayTime || "") + (row.entry.application ? "  ·  " + row.entry.application : "")
                color: theme.muted; font.pixelSize: 12; elide: Text.ElideRight
            }
            Rectangle {
                implicitWidth: modeLabel.implicitWidth + 18; implicitHeight: 24; radius: 6
                color: row.editEntry ? "#F0EBFD" : "#EAF5F8"
                Label {
                    id: modeLabel
                    objectName: "voiceHistoryModeLabel"
                    anchors.centerIn: parent
                    text: row.editEntry ? "编辑指令" : "语音输入"
                    color: row.editEntry ? "#7256A8" : "#28788B"; font.pixelSize: 11
                }
            }
            UiAction {
                objectName: "voiceHistoryCopyButton"
                symbol: copiedNotice.running ? "check" : "copy"
                text: ""; quiet: true
                implicitWidth: 34; implicitHeight: 32
                leftPadding: 8; rightPadding: 8
                enabled: row.copyText.length > 0
                Accessible.name: "复制文本"
                ToolTip.visible: hovered
                ToolTip.text: copiedNotice.running ? "已复制" : row.resultAvailable && !row.undoneEntry ? "复制结果" : "复制原文"
                onClicked: {
                    copyBuffer.text = row.copyText
                    copyBuffer.selectAll()
                    copyBuffer.copy()
                    copyBuffer.deselect()
                    copiedNotice.restart()
                }
            }
            UiAction {
                id: moreButton
                objectName: "voiceHistoryMoreButton"
                text: ""; symbol: "more"; quiet: true; implicitWidth: 34; implicitHeight: 32
                leftPadding: 6; rightPadding: 6
                Accessible.name: "更多记录操作"
                onClicked: recordMenu.popup()
                Menu {
                    id: recordMenu
                    MenuItem {
                        objectName: "voiceHistoryPlayButton"
                        text: row.controller.playingVoicePath === row.entry.audioPath ? "停止播放" : "播放录音"
                        enabled: Boolean(row.entry.audioPath)
                        onTriggered: row.controller.playVoiceHistory(row.entry.audioPath)
                    }
                    MenuItem {
                        objectName: "voiceHistoryOpenLocationButton"
                        text: "打开记录文件夹"
                        enabled: Boolean(row.entry.recordPath)
                        onTriggered: row.controller.openVoiceHistoryLocation(row.entry.recordPath)
                    }
                    MenuSeparator { }
                    MenuItem {
                        objectName: "voiceHistoryDetailsButton"
                        text: row.detailsVisible ? "收起记录详情" : "查看记录详情"
                        onTriggered: row.detailsVisible = !row.detailsVisible
                    }
                    MenuSeparator { visible: !row.compact }
                    MenuItem {
                        objectName: "voiceHistoryDeleteButton"
                        visible: !row.compact
                        text: "删除这条记录"
                        onTriggered: row.deleteRequested(row.entry)
                    }
                }
            }
        }
        Label {
            id: primaryText
            objectName: "voiceHistoryPrimaryText"
            Layout.fillWidth: true
            text: row.undoneEntry
                  ? ((row.editEntry ? "原编辑指令：" : "原听写内容：") + (row.entry.text || ""))
                  : row.editEntry ? (row.entry.text || "（无识别文字）")
                  : row.resultAvailable ? (row.resultEmpty ? "（空文本）" : row.entry.candidateText)
                  : (row.entry.text || "（无识别文字）")
            color: theme.text; font.pixelSize: 14; font.weight: row.editEntry ? Font.DemiBold : Font.Normal
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: row.compact ? 2 : row.textExpanded ? 2147483647 : 3
            elide: row.textExpanded && !row.compact ? Text.ElideNone : Text.ElideRight
        }
        Label {
            id: sourceText
            objectName: "voiceHistorySourceText"
            Layout.fillWidth: true
            visible: !row.compact && !row.editEntry && !row.undoneEntry && row.resultAvailable && !row.resultEmpty && row.entry.candidateText !== row.entry.text
            text: "识别原文：" + (row.entry.text || "")
            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap; textFormat: Text.PlainText
            maximumLineCount: row.textExpanded ? 2147483647 : 2
            elide: row.textExpanded ? Text.ElideNone : Text.ElideRight
        }
        Rectangle {
            Layout.fillWidth: true
            visible: row.editEntry && !row.compact && (row.appliedEntry || row.undoneEntry)
                     && (Boolean(row.entry.editSummary) || (!row.undoneEntry && row.resultAvailable))
            implicitHeight: resultContent.implicitHeight + 24
            radius: 8; color: theme.subtle
            ColumnLayout {
                id: resultContent
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                spacing: 6
                Label {
                    id: editSummary
                    objectName: "voiceHistoryEditSummary"
                    Layout.fillWidth: true
                    text: (row.nativeUndoSent ? "原修改摘要：" : row.undoneEntry ? "已撤回的修改：" : "修改摘要：")
                          + (row.entry.editSummary || "修改已应用")
                    color: "#7256A8"; font.pixelSize: 12; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    maximumLineCount: row.textExpanded ? 2147483647 : 2
                    elide: row.textExpanded ? Text.ElideNone : Text.ElideRight
                }
                Label {
                    id: candidateText
                    objectName: "voiceHistoryCandidateText"
                    Layout.fillWidth: true
                    visible: !row.undoneEntry && row.resultAvailable
                    text: row.resultEmpty ? "修改结果：已清空文本" : "修改结果：" + (row.entry.candidateText || "")
                    color: theme.text; font.pixelSize: 13; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    maximumLineCount: row.textExpanded ? 2147483647 : 3
                    elide: row.textExpanded ? Text.ElideNone : Text.ElideRight
                }
            }
        }
        UiAction {
            objectName: "voiceHistoryExpandButton"
            Layout.alignment: Qt.AlignLeft
            implicitHeight: 30; leftPadding: 0; rightPadding: 6
            quiet: true
            visible: !row.compact && (row.textExpanded || primaryText.truncated
                || (sourceText.visible && sourceText.truncated)
                || (editSummary.visible && editSummary.truncated)
                || (candidateText.visible && candidateText.truncated))
            text: row.textExpanded ? "收起全文  ⌃" : "展开全文  ⌄"
            onClicked: row.textExpanded = !row.textExpanded
        }
        UiNotice {
            objectName: "voiceHistoryOutcomeStatus"
            warning: !row.failedEntry
            Layout.fillWidth: true
            visible: row.outcomeText.length > 0
            text: row.outcomeText + (row.nativeUndoSent ? " · 结果由目标应用处理，未回读确认" : row.undoneEntry
                                    ? row.editEntry ? " · 文本已恢复到编辑前状态" : " · 本次听写已从原文本框移除" : "")
            color: row.failedEntry ? "#B54756" : theme.warning; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        Label {
            objectName: "voiceHistoryTechnicalDetails"
            Layout.fillWidth: true
            visible: row.detailsVisible
            text: [row.entry.durationLabel, row.entry.backend, row.entry.dataSummary].filter(function(s) { return Boolean(s) }).join("  ·  ")
            color: theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
    }
}
