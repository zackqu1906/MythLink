import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ScrollView {
    id: page
    required property var controller
    readonly property var historyModel: controller.filteredVoiceHistoryModel
    readonly property bool historyFiltered: historySearch.text.trim().length > 0
        || historyModel.modeFilter !== "all" || historyModel.outcomeFilter !== "all"
    signal homeRequested()
    signal inputSetupRequested()
    UiTheme { id: theme }
    objectName: "mainPageScroll"
    clip: true
    contentWidth: availableWidth
    contentHeight: pageContent.height
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AlwaysOff
    function resetHistoryFilters() {
        searchDebounce.stop()
        historySearch.clear()
        historyModel.searchText = ""
        historyModel.modeFilter = "all"
        historyModel.outcomeFilter = "all"
    }
    function requestHistoryDelete(record) {
        page.controller.clearHistoryActionMessage()
        deleteHistoryDialog.interactionId = String(record.interactionId || record.id || "")
        deleteHistoryDialog.previewText = String(record.text || "（无识别文字）").slice(0, 120)
        deleteHistoryDialog.recordTime = String(record.displayTime || "")
        deleteHistoryDialog.open()
    }
    Timer {
        id: searchDebounce
        interval: 160
        onTriggered: page.historyModel.searchText = historySearch.text
    }
    function scrollToGestures() {
        page.contentItem.contentY = Math.min(voiceGestureCard.y, Math.max(0, page.contentHeight - page.availableHeight))
    }
    function scrollToHistory() {
        Qt.callLater(function() {
            page.contentItem.contentY = Math.min(voiceHistoryCard.y, Math.max(0, page.contentHeight - page.availableHeight))
        })
    }
    function statusColor(): color {
        if (!controller.connected) return "#919BB0"
        if (controller.statusKind === "error") return "#C84657"
        if (controller.statusKind === "starting" || controller.statusKind === "stopping") return "#AC781B"
        return controller.recognitionEnabled ? theme.success : theme.primary
    }
    Column {
        id: pageContent
        width: page.availableWidth
        spacing: 18
        Rectangle {
            id: voiceInputCard
            objectName: "voiceInputCard"
            width: parent.width
            height: content.implicitHeight + 44
            radius: 16; color: theme.surface; border.color: theme.line
            ColumnLayout {
                id: content
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 22; spacing: 16
                RowLayout {
                    Layout.fillWidth: true; spacing: 16
                    Rectangle {
                        width: 58; height: 58; radius: 16
                        color: page.controller.connected ? "#EDF2FF" : theme.subtle
                        UiIcon { anchors.centerIn: parent; width: 29; height: 29; symbol: "mic"; ink: page.statusColor() }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                        Label {
                            objectName: "voiceStatusTitle"
                            Layout.fillWidth: true
                            text: page.controller.connected ? page.controller.statusTitle : "请先在首页连接 Ring"
                            color: theme.text; font.pixelSize: 20; font.weight: Font.DemiBold; wrapMode: Text.Wrap
                        }
                        Label {
                            objectName: "voiceStatusDetail"
                            Layout.fillWidth: true
                            text: page.controller.connected
                                  ? page.controller.statusDetail
                                    + (page.controller.textProcessing && page.controller.interactionState === "processing"
                                       && page.controller.transcriptText.indexOf("正在处理文本") === 0 ? " · 大模型处理中" : "")
                                  : "设备连接在首页管理，连接后即可使用语音输入与编辑。"
                            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                        }
                    }
                    UiAction {
                        objectName: "voiceRecognitionButton"
                        Layout.preferredWidth: 142; Layout.preferredHeight: 44
                        visible: page.controller.connected
                        enabled: page.controller.connected && !page.controller.busy && !page.controller.scanBusy
                        text: page.controller.recognitionEnabled ? "暂停语音识别" : "开启语音识别"
                        primary: !page.controller.recognitionEnabled
                        onClicked: { if (enabled) page.controller.toggleRecognition() }
                    }
                    UiAction {
                        objectName: "voiceHomeButton"
                        Layout.preferredWidth: 118; Layout.preferredHeight: 44
                        visible: !page.controller.connected
                        text: "返回首页"; primary: true
                        onClicked: page.homeRequested()
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: theme.line }
                RowLayout {
                    Layout.fillWidth: true
                    visible: page.controller.inlineInput.enabled
                    UiNotice {
                        objectName: "inputMethodConnectionStatus"
                        warning: !page.controller.inlineInput.ready
                        Layout.fillWidth: true
                        text: page.controller.inlineInput.connectionStatus
                        color: warning ? theme.warning : theme.success
                        font.pixelSize: 12; wrapMode: Text.Wrap
                    }
                    UiAction { objectName: "openVoiceGestureSettingsButton"; text: "语音手势"; quiet: true; onClicked: page.scrollToGestures() }
                    UiAction { objectName: "inputMethodSetupButton"; text: "输入法设置"; quiet: true; onClicked: page.inputSetupRequested() }
                }
                PermissionNotice {
                    objectName: "accessibilityPermissionWarning"
                    Layout.fillWidth: true
                    message: page.controller.inlineInput.permissions.accessibilityWarningText
                    onActivated: page.controller.inlineInput.permissions.openSettings()
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: !page.controller.inlineInput.enabled && page.controller.inputRoutingMode === "manual"
                    Button {
                        objectName: "dictationModeButton"
                        text: "输入到光标"; checkable: true; autoExclusive: true
                        checked: page.controller.inputMode === "dictation"
                        onClicked: page.controller.inputMode = "dictation"
                    }
                    Button {
                        objectName: "editModeButton"
                        text: "修改当前文本"; checkable: true; autoExclusive: true
                        checked: page.controller.inputMode === "edit"
                        onClicked: page.controller.inputMode = "edit"
                    }
                    Item { Layout.fillWidth: true }
                }
                Label {
                    Layout.fillWidth: true
                    text: page.controller.inlineInput.enabled
                          ? "点入文本框后，使用语音手势开始输入。默认实时听写，转换为编辑会结束本句并修改原文。"
                          : page.controller.inputRoutingMode === "auto"
                          ? "自动判断听写或编辑指令；处理期间可取消，应用后可在文本框旁撤销。"
                          : page.controller.inputMode === "edit" ? "把光标留在目标文本框；修改会直接应用，随后可在文本框旁撤销。"
                          : "语音会输入到目标文本框；应用后可在文本框旁撤销。"
                    color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
                }
            }
        }
        VoiceGestureSettingsCard {
            id: voiceGestureCard
            objectName: "voiceGestureSettingsCard"
            width: parent.width
            height: implicitHeight
            controller: page.controller
        }
        Rectangle {
            id: voiceHistoryCard
            objectName: "voiceHistoryCard"
            width: parent.width
            height: Math.max(380, page.availableHeight - voiceInputCard.height - voiceGestureCard.height - 2 * pageContent.spacing)
            radius: 16; color: theme.surface; border.color: theme.line
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 22; spacing: 14
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 4
                        Label { Layout.fillWidth: true; text: "语音输入历史"; color: theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
                        Label {
                            objectName: "voiceHistoryCount"
                            Layout.fillWidth: true
                            text: page.historyFiltered ? "找到 " + voiceHistoryList.count + " 条 · 共 " + page.controller.voiceHistoryEntries.length + " 条"
                                : "听写与编辑记录 · 共 " + page.controller.voiceHistoryEntries.length + " 条"
                            color: theme.muted; font.pixelSize: 12
                        }
                    }
                    UiAction {
                        id: historyOptionsButton
                        objectName: "voiceHistoryOptionsButton"
                        text: "更多 ···"; quiet: true
                        onClicked: historyOptions.popup()
                        Menu {
                            id: historyOptions
                            MenuItem { objectName: "openDataDirectoryButton"; text: "打开数据文件夹"; onTriggered: page.controller.openDataDirectory() }
                            MenuItem { objectName: "clearVoiceHistoryButton"; text: "清空全部语音记录"; enabled: page.controller.voiceHistoryEntries.length > 0; onTriggered: clearHistoryDialog.open() }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    UiTextField {
                        id: historySearch
                        objectName: "voiceHistorySearch"
                        Layout.fillWidth: true; Layout.minimumWidth: 100
                        Layout.preferredHeight: 42
                        leftPadding: 36; rightPadding: 10
                        hintText: "搜索文字或应用"
                        Accessible.name: "搜索语音历史"
                        selectByMouse: true
                        font.pixelSize: 13
                        background: Rectangle { color: theme.subtle; radius: 8; border.color: historySearch.activeFocus ? theme.primary : theme.line }
                        UiIcon { x: 11; anchors.verticalCenter: parent.verticalCenter; width: 17; height: 17; symbol: "search" }
                        onTextEdited: searchDebounce.restart()
                        onAccepted: { searchDebounce.stop(); page.historyModel.searchText = text }
                    }
                    SettingsSelect {
                        objectName: "voiceHistoryModeFilter"
                        Layout.preferredWidth: 122; Layout.preferredHeight: 42
                        Accessible.name: "记录类型"
                        model: ["全部类型", "语音输入", "编辑指令"]
                        currentIndex: ["all", "dictation", "edit"].indexOf(page.historyModel.modeFilter)
                        onActivated: page.historyModel.modeFilter = ["all", "dictation", "edit"][currentIndex]
                    }
                    SettingsSelect {
                        objectName: "voiceHistoryOutcomeFilter"
                        Layout.preferredWidth: 118; Layout.preferredHeight: 42
                        Accessible.name: "记录状态"
                        model: ["全部状态", "已完成", "已撤回", "未完成", "已取消"]
                        currentIndex: ["all", "completed", "undone", "failed", "cancelled"].indexOf(page.historyModel.outcomeFilter)
                        onActivated: page.historyModel.outcomeFilter = ["all", "completed", "undone", "failed", "cancelled"][currentIndex]
                    }
                    UiAction {
                        objectName: "voiceHistoryResetFilters"
                        text: "重置"; quiet: true
                        enabled: page.historyFiltered
                        onClicked: page.resetHistoryFilters()
                    }
                }
                Label {
                    objectName: "voiceHistoryActionMessage"
                    Layout.fillWidth: true
                    visible: text.length > 0 && !deleteHistoryDialog.visible
                    text: page.controller.historyActionMessage
                    color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
                }
                ListView {
                    id: voiceHistoryList
                    objectName: "voiceHistoryList"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true; spacing: 10
                    model: page.historyModel
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }
                    delegate: VoiceHistoryEntry {
                        width: voiceHistoryList.width
                        controller: page.controller
                        onDeleteRequested: function(record) { page.requestHistoryDelete(record) }
                    }
                    ColumnLayout {
                        anchors.centerIn: parent; width: parent.width - 24; spacing: 9
                        visible: voiceHistoryList.count === 0
                        UiIcon { Layout.alignment: Qt.AlignHCenter; symbol: "history"; ink: "#9AA5BD"; width: 30; height: 30 }
                        Label {
                            objectName: "voiceHistoryEmptyTitle"
                            Layout.alignment: Qt.AlignHCenter
                            text: page.controller.voiceHistoryEntries.length > 0 ? "没有匹配的记录" : "还没有语音记录"
                            color: theme.text; font.pixelSize: 15
                        }
                        Label {
                            Layout.fillWidth: true
                            text: page.controller.voiceHistoryEntries.length > 0 ? "换个关键词，或重置筛选条件。" : "完成一次听写或编辑后，文字与录音会保存在这里。"
                            color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Switch {
                        objectName: "smartAssociationSwitch"
                        Layout.fillWidth: true
                        text: "智能关联推荐"
                        checked: page.controller.smartAssociationEnabled
                        onToggled: page.controller.smartAssociationEnabled = checked
                    }
                    UiAction { objectName: "openAssociationCenterButton"; text: "数据关联中心"; quiet: true; onClicked: page.controller.performAssociationAction("center.open", "") }
                }
            }
        }
    }
    Dialog {
        id: deleteHistoryDialog
        objectName: "deleteHistoryDialog"
        property string interactionId: ""
        property string previewText: ""
        property string recordTime: ""
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(480, parent.width - 48)
        modal: true
        popupType: Popup.Item
        title: "删除这条语音记录？"
        closePolicy: Popup.CloseOnEscape
        padding: 24
        background: Rectangle { color: theme.surface; radius: 16; border.color: theme.line }
        header: Label {
            text: deleteHistoryDialog.title
            leftPadding: 24; rightPadding: 24; topPadding: 24; bottomPadding: 4
            color: theme.text; font.pixelSize: 22; font.weight: Font.DemiBold
        }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
        contentItem: ColumnLayout {
            spacing: 14
            Label { text: deleteHistoryDialog.recordTime; color: theme.muted; font.pixelSize: 12 }
            Label {
                Layout.fillWidth: true
                text: deleteHistoryDialog.previewText
                textFormat: Text.PlainText; wrapMode: Text.Wrap
                maximumLineCount: 3; elide: Text.ElideRight
                color: theme.text; font.pixelSize: 14
            }
            Label {
                Layout.fillWidth: true
                text: "将删除这条保存的记录、录音及相关数据关联，无法撤销。已输入到应用的文字不受影响。"
                wrapMode: Text.Wrap; color: theme.muted; font.pixelSize: 13
            }
            Label {
                objectName: "deleteHistoryError"
                Layout.fillWidth: true
                visible: text.length > 0
                text: page.controller.historyActionMessage
                color: "#B54756"; wrapMode: Text.Wrap; font.pixelSize: 12
            }
        }
        footer: DialogButtonBox {
            leftPadding: 24; rightPadding: 24; topPadding: 0; bottomPadding: 20
            spacing: 10
            UiAction {
                objectName: "cancelDeleteHistoryButton"
                text: "取消"
                onClicked: deleteHistoryDialog.reject()
            }
            UiAction {
                objectName: "confirmDeleteHistoryButton"
                text: "删除记录"; primary: true
                onClicked: {
                    if (page.controller.deleteVoiceHistory(deleteHistoryDialog.interactionId)) deleteHistoryDialog.close()
                }
            }
        }
    }
    Dialog {
        id: clearHistoryDialog
        objectName: "clearHistoryDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(440, parent.width - 48)
        modal: true
        title: "清空全部语音记录？"
        standardButtons: Dialog.Cancel | Dialog.Ok
        onAccepted: page.controller.clearVoiceHistory()
        contentItem: Label {
            text: "将删除已保存的语音记录、录音和关联数据，无法撤销。"
            wrapMode: Text.Wrap; color: theme.muted
        }
    }
}
