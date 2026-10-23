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
    readonly property var applicationInfo: controller.historyApplication(String(entry.application || ""))
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
    readonly property bool polishEntry: !editEntry && entry.processingMode === "polish"
    readonly property bool nativeUndoSent: entry.outcome === "native_undo_sent"
    readonly property bool undoneEntry: entry.outcome === "undone" || nativeUndoSent
    readonly property bool appliedEntry: entry.outcome === "applied" || entry.outcome === "confirm"
    readonly property bool failedEntry: entry.outcome === "apply_failed" || entry.outcome === "abandoned"
    readonly property bool cancelledEntry: entry.outcome === "cancelled" || entry.outcome === "cancel"
    readonly property bool interruptedEntry: entry.outcome === "interrupted"
    readonly property bool unrecognizedEntry: entry.recognized === false && !cancelledEntry && !undoneEntry && !interruptedEntry
    readonly property string recordTime: {
        var full = String(entry.displayTime || "")
        var match = full.match(/(\d{2}:\d{2})(?::\d{2})?$/)
        return row.compact && match ? match[1] : full
    }
    readonly property bool resultAvailable: appliedEntry && (entry.candidateAvailable !== undefined
                                                            ? Boolean(entry.candidateAvailable) : Boolean(entry.candidateText))
    readonly property bool resultEmpty: Boolean(entry.candidateEmpty)
    readonly property string copyText: resultAvailable && !undoneEntry
                                       ? (resultEmpty ? "" : String(entry.candidateText || ""))
                                       : String(entry.text || "")
    readonly property string outcomeText: nativeUndoSent ? "已发送撤销" : undoneEntry ? "已撤回"
                                          : cancelledEntry ? "已取消" : interruptedEntry ? "已中断"
                                          : unrecognizedEntry ? "未识别" : failedEntry ? "未完成" : ""
    readonly property bool warningStatus: failedEntry || interruptedEntry || unrecognizedEntry
    readonly property string statusSymbol: warningStatus ? "warning" : "cancel"
    readonly property color statusColor: warningStatus ? theme.warning : theme.muted
    VoiceHistoryTheme { id: theme }
    objectName: "voiceHistoryEntry"
    implicitHeight: content.implicitHeight + theme.rowPadding * 2
    height: implicitHeight
    color: theme.surface; radius: 12; border.color: theme.line
    UiWindowDragArea { anchors.fill: parent; z: -1 }
    Rectangle {
        x: 0; y: theme.rowPadding; width: 3; height: 24; radius: 1.5
        visible: row.outcomeText.length > 0
        color: row.warningStatus ? theme.warning : theme.neutral
    }

    // Copying history never reuses the live dictation/clipboard pipeline.
    TextEdit { id: copyBuffer; visible: false; readOnly: true; textFormat: TextEdit.PlainText }
    Timer { id: copiedNotice; interval: 1600 }
    RowLayout {
        id: recordActions
        anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: theme.rowPadding; z: 1
        spacing: 8
        RowLayout {
            spacing: 5
            VoiceHistoryAction {
                objectName: "voiceHistoryCopyButton"
                asset: "copy"
                visible: !row.failedEntry && !row.cancelledEntry && !row.interruptedEntry && !row.unrecognizedEntry
                hint: copiedNotice.running ? "已复制" : row.resultAvailable && !row.undoneEntry ? "复制结果" : "复制原文"
                enabled: row.copyText.length > 0
                onClicked: {
                    copyBuffer.text = row.copyText
                    copyBuffer.selectAll()
                    copyBuffer.copy()
                    copyBuffer.deselect()
                    copiedNotice.restart()
                }
            }
            VoiceHistoryAction {
                objectName: "voiceHistoryDeleteButton"
                asset: "trash"; hint: "删除这条记录"
                visible: !row.compact
                onClicked: row.deleteRequested(row.entry)
            }
        }
        VoiceHistoryAction {
            id: moreButton
            objectName: "voiceHistoryMoreButton"
            asset: "more"; hint: "更多记录操作"; filled: true
            menuOpen: recordMenu.visible
            onClicked: recordMenu.open()
            UiMenu {
                id: recordMenu
                objectName: "voiceHistoryRecordMenu"
                UiMenuItem {
                    objectName: "voiceHistoryPlayButton"
                    text: Boolean(row.entry.audioPath) && row.controller.playingVoicePath === row.entry.audioPath ? "停止播放" : "播放录音"
                    enabled: Boolean(row.entry.audioPath)
                    onTriggered: row.controller.playVoiceHistory(row.entry.audioPath)
                }
                UiMenuItem {
                    objectName: "voiceHistoryOpenLocationButton"
                    text: "打开记录文件夹"
                    enabled: Boolean(row.entry.recordPath)
                    onTriggered: row.controller.openVoiceHistoryLocation(row.entry.recordPath)
                }
                UiMenuSeparator { }
                UiMenuItem {
                    objectName: "voiceHistoryDetailsButton"
                    text: row.detailsVisible ? "收起记录详情" : "查看记录详情"
                    onTriggered: row.detailsVisible = !row.detailsVisible
                }
            }
        }
    }
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: theme.rowPadding
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true; spacing: 8
            Label {
                id: primaryText
                objectName: "voiceHistoryPrimaryText"
                Layout.fillWidth: true; Layout.rightMargin: recordActions.width + 12
                text: row.undoneEntry
                      ? ((row.editEntry ? "原编辑指令：" : "原听写内容：") + (row.entry.text || ""))
                      : row.editEntry ? (row.entry.text || "（无识别文字）")
                      : row.resultAvailable ? (row.resultEmpty ? "（空文本）" : row.entry.candidateText)
                      : (row.entry.text || "（无识别文字）")
                color: theme.text; font.family: theme.family; font.pixelSize: theme.bodySize
                lineHeight: theme.bodyLineHeight; lineHeightMode: Text.FixedHeight
                textFormat: Text.PlainText; wrapMode: Text.Wrap
                maximumLineCount: row.compact ? 2 : row.textExpanded ? 2147483647 : 3
                elide: row.textExpanded && !row.compact ? Text.ElideNone : Text.ElideRight
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 0
                visible: !row.compact && row.editEntry && (row.appliedEntry || row.undoneEntry)
                Label {
                    id: originalText
                    objectName: "voiceHistoryOriginalText"
                    Layout.fillWidth: true
                    visible: Boolean(row.entry.editBeforeText)
                    text: "原文：" + (row.entry.editBeforeText || "")
                    color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                    lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                    maximumLineCount: row.textExpanded ? 2147483647 : 2
                    elide: row.textExpanded ? Text.ElideNone : Text.ElideRight
                }
                Label {
                    id: candidateText
                    objectName: "voiceHistoryCandidateText"
                    Layout.fillWidth: true
                    visible: !row.undoneEntry && row.resultAvailable
                    text: "改后：" + (row.resultEmpty ? "（空文本）" : row.entry.candidateText || "")
                    color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                    lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                    maximumLineCount: row.textExpanded ? 2147483647 : 3
                    elide: row.textExpanded ? Text.ElideNone : Text.ElideRight
                }
            }
            Label {
                id: sourceText
                objectName: "voiceHistorySourceText"
                Layout.fillWidth: true
                visible: !row.compact && row.detailsVisible && !row.editEntry && !row.undoneEntry && row.resultAvailable && !row.resultEmpty && row.entry.candidateText !== row.entry.text
                text: "识别原文：" + (row.entry.text || "")
                color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                wrapMode: Text.Wrap; textFormat: Text.PlainText
                maximumLineCount: row.textExpanded ? 2147483647 : 2
                elide: row.textExpanded ? Text.ElideNone : Text.ElideRight
            }
            Label {
                id: editSummary
                objectName: "voiceHistoryEditSummary"
                Layout.fillWidth: true
                visible: row.detailsVisible && Boolean(row.entry.editSummary)
                text: "修改摘要：" + (row.entry.editSummary || "")
                color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            Label {
                objectName: "voiceHistoryOutcomeExplanation"
                Layout.fillWidth: true; visible: row.undoneEntry
                text: row.nativeUndoSent ? "结果由目标应用处理，未回读确认" : row.editEntry ? "文本已恢复到编辑前状态" : "本次听写已从原文本框移除"
                color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            FigmaAction {
                objectName: "voiceHistoryExpandButton"
                Layout.alignment: Qt.AlignLeft
                implicitHeight: 26; leftPadding: 6; rightPadding: 6
                quiet: true; font.family: theme.family; font.pixelSize: theme.detailSize
                visible: !row.compact && (row.textExpanded || primaryText.truncated
                    || (sourceText.visible && sourceText.truncated)
                    || (originalText.visible && originalText.truncated)
                    || (candidateText.visible && candidateText.truncated))
                text: row.textExpanded ? "收起全文" : "展开全文"
                onClicked: row.textExpanded = !row.textExpanded
            }
            Label {
                objectName: "voiceHistoryTechnicalDetails"
                Layout.fillWidth: true; visible: row.detailsVisible
                text: [row.entry.durationLabel, row.entry.backend, row.entry.dataSummary].filter(function(s) { return Boolean(s) }).join("  ·  ")
                color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 12
            RowLayout {
                visible: row.outcomeText.length > 0
                spacing: 8
                Image {
                    objectName: "voiceHistoryOutcomeIcon"
                    width: 16; height: 16
                    source: "../assets/figma/history/" + row.statusSymbol + ".svg"
                    fillMode: Image.PreserveAspectFit
                }
                Label {
                    objectName: "voiceHistoryOutcomeStatus"
                    text: row.outcomeText; color: row.statusColor
                    font.family: theme.family; font.pixelSize: theme.detailSize; font.weight: Font.Medium
                    lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                }
            }
            Label {
                objectName: "voiceHistoryMetadata"
                text: row.recordTime
                color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                HoverHandler { id: timeHover }
                ToolTip.visible: timeHover.hovered
                ToolTip.text: row.entry.displayTime || ""
            }
            Label {
                objectName: "voiceHistoryModeLabel"
                leftPadding: 6; rightPadding: 6
                text: row.editEntry ? "命令" : row.polishEntry ? "润色" : "听写"
                background: Rectangle { radius: 10; color: row.editEntry ? "#1A6552A3" : "#1A027A88" }
                color: row.editEntry ? theme.edit : theme.primary
                font.family: theme.family; font.pixelSize: 12; font.weight: Font.Medium
                lineHeight: 20; lineHeightMode: Text.FixedHeight
            }
            RowLayout {
                objectName: "voiceHistoryApplicationGroup"
                Layout.minimumWidth: 0; Layout.maximumWidth: Math.max(80, Math.min(240, row.width * 0.4))
                Layout.bottomMargin: 2
                spacing: 8
                ApplicationIcon {
                    objectName: "voiceHistoryApplicationIcon"
                    Layout.preferredWidth: 22; Layout.preferredHeight: 22
                    Layout.alignment: Qt.AlignBottom
                    bundle: row.applicationInfo.bundle; applicationPath: row.applicationInfo.path
                    label: row.applicationInfo.name
                }
                Label {
                    objectName: "voiceHistoryApplication"
                    Layout.minimumWidth: 0; Layout.fillWidth: true
                    text: row.applicationInfo.name
                    color: theme.muted; font.family: theme.family; font.pixelSize: theme.detailSize
                    lineHeight: theme.detailLineHeight; lineHeightMode: Text.FixedHeight
                    elide: Text.ElideRight; textFormat: Text.PlainText
                }
            }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
        }
    }
}
