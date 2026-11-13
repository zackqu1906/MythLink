import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Controls.Material
import QtQuick.Layouts

UiScrollView {
    id: page
    required property var controller
    property bool associationUiVisible: false
    property bool historyOnly: false
    property bool hotwordsExpanded: false
    readonly property var historyModel: controller.filteredVoiceHistoryModel
    readonly property bool historyFiltered: historySearch.text.trim().length > 0
        || historyModel.modeFilter !== "all" || historyModel.outcomeFilter !== "all"
        || historyModel.applicationFilter !== "all" || historyModel.timeFilter !== "all"
    onVisibleChanged: if (!visible && controller) { controller.voicePractice.cancel(); voiceGestureDialog.close() }
    onHistoryOnlyChanged: if (historyOnly && controller) { controller.voicePractice.cancel(); voiceGestureDialog.close() }
    signal homeRequested()
    signal historyRequested()
    signal tutorialRequested()
    FigmaTheme { id: theme }
    VoiceHistoryTheme { id: historyTheme }
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
        historyModel.applicationFilter = "all"
        historyModel.timeFilter = "all"
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
    function configureGestures() { voiceGestureDialog.open() }
    FigmaDialog {
        id: voiceGestureDialog
        objectName: "voiceGestureBindingDialog"
        title: "配置语音手势"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(960, parent.width - 48)
        height: Math.min(680, parent.height - 48)
        modal: true; popupType: Popup.Item
        contentItem: Loader {
            active: voiceGestureDialog.visible
            sourceComponent: VoiceGestureSettingsCard { controller: page.controller }
        }
    }
    function scrollToHistory() { historyRequested() }
    function toggleHotwords() {
        hotwordsExpanded = !hotwordsExpanded
        if (hotwordsExpanded) hotwordDialog.open()
        else { hotwordLibrary.cancelDrag(); hotwordDialog.close() }
    }
    readonly property var currentVoiceBindings: {
        var rows = controller.voiceGestureConfiguration
        var combined = rows.some(function(row) { return row.voiceAction === "start_end" && row.gesture.length > 0 })
        var order = ["start_end", "confirm", "end", "send", "polish", "undo", "switch_mode"]
        return rows.filter(function(row) {
            return combined ? row.voiceAction !== "confirm" && row.voiceAction !== "end" : row.voiceAction !== "start_end"
        }).sort(function(a, b) { return order.indexOf(a.voiceAction) - order.indexOf(b.voiceAction) })
    }
    function statusColor(): color {
        if (!controller.connected) return "#919BB0"
        if (controller.statusKind === "error") return "#C84657"
        if (controller.statusKind === "starting" || controller.statusKind === "stopping") return "#AC781B"
        return controller.recognitionEnabled ? theme.success : theme.primary
    }
    component WorkbenchNavigation: FigmaAction {
        navigation: true
        contentColor: enabled && (hovered || down) ? theme.primary : theme.muted
        trailingAsset: enabled && (hovered || down) ? "../assets/figma/redesign-246-3060-navigation-active.svg" : ""
    }
    Column {
        id: pageContent
        width: page.availableWidth
        spacing: 16
        Rectangle {
            id: voiceInputCard
            objectName: "voiceInputCard"
            visible: !page.historyOnly
            width: parent.width; height: voiceStatus.implicitHeight + 40
            radius: 12; color: theme.surface; border.color: theme.line
            ColumnLayout {
                id: voiceStatus
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true; Layout.preferredHeight: 40; spacing: 12
                    Label { Layout.preferredHeight: 36; Layout.alignment: Qt.AlignTop; verticalAlignment: Text.AlignVCenter; text: page.controller.touchpad.blocksVoice ? "语音识别已暂停" : "语音输入"; objectName: "voiceStatusTitle"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                    VoiceRecognitionControl {
                        objectName: "voiceRecognitionButton"
                        Layout.alignment: Qt.AlignTop; Layout.topMargin: 3
                        controller: page.controller
                    }
                    Item { Layout.fillWidth: true }
                    WorkbenchNavigation { objectName: "voiceHotwordLibraryButton"; Layout.alignment: Qt.AlignTop; text: "热词库"; onClicked: page.toggleHotwords() }
                    WorkbenchNavigation { objectName: "openVoiceGestureSettingsButton"; Layout.alignment: Qt.AlignTop; text: "配置手势"; onClicked: page.configureGestures() }
                }
                Label {
                    objectName: "voiceStatusDetail"; Layout.fillWidth: true; wrapMode: Text.Wrap
                    visible: page.controller.touchpad.blocksVoice
                    text: "笔画输入期间语音识别已暂停，结束书写后恢复。"
                    color: theme.muted; font.pixelSize: 13
                }
                Flow {
                    id: gestureBindings
                    objectName: "voiceCurrentBindings"
                    Layout.fillWidth: true
                    spacing: 12
                    Repeater {
                        model: page.currentVoiceBindings
                        Item {
                            id: bindingItem
                            required property var modelData
                            objectName: "voiceCurrentBinding_" + modelData.voiceAction
                            width: bindingContent.implicitWidth + 16
                            height: 44
                            Row {
                                id: bindingContent
                                anchors.left: parent.left; anchors.leftMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 12
                                Item {
                                    id: gestureIcon
                                    objectName: "voiceCurrentPreview_" + bindingItem.modelData.voiceAction
                                    readonly property var gestures: bindingItem.modelData.gesture ? [bindingItem.modelData.gesture] : []
                                    readonly property string gesture: bindingItem.modelData.gesture.replace(/^double-/, "")
                                    readonly property var originalAssets: ({tap: "icon3", "swipe-up": "up", "swipe-down": "down", "swipe-left": "left", "swipe-right": "right"})
                                    width: 20; height: 20
                                    anchors.verticalCenter: parent.verticalCenter
                                    Image {
                                        objectName: "voicePreviewGesture_" + bindingItem.modelData.gesture
                                        anchors.centerIn: parent
                                        // Original exported SVG geometry inside the Figma 20px slot.
                                        source: !gestureIcon.gesture ? "" : gestureIcon.originalAssets[gestureIcon.gesture]
                                            ? "../assets/figma/redesign-567-32820-" + gestureIcon.originalAssets[gestureIcon.gesture] + ".svg"
                                            : "../assets/figma/voice-hud/gesture-" + gestureIcon.gesture + ".svg"
                                    }
                                    Text {
                                        objectName: "voicePreviewCount_" + bindingItem.modelData.gesture
                                        visible: bindingItem.modelData.gesture.indexOf("double-") === 0
                                        anchors.right: parent.right; anchors.bottom: parent.bottom
                                        text: "2"; font.pixelSize: 10; font.bold: true; color: theme.muted
                                    }
                                    Text { anchors.centerIn: parent; visible: !gestureIcon.gesture; text: "—"; color: theme.muted; font.pixelSize: 16 }
                                }
                                Label {
                                    objectName: "voiceCurrentGesture_" + bindingItem.modelData.voiceAction
                                    text: bindingItem.modelData.gestureLabel
                                    font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium
                                    color: theme.text; lineHeight: 24; lineHeightMode: Text.FixedHeight
                                }
                                Label {
                                    objectName: "voiceCurrentFunction_" + bindingItem.modelData.voiceAction
                                    text: bindingItem.modelData.label
                                    font.family: theme.family; font.pixelSize: 16
                                    color: theme.muted; lineHeight: 24; lineHeightMode: Text.FixedHeight
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    visible: !page.controller.connected || page.controller.voiceRuntimeError.length > 0 || page.controller.statusKind === "error" || !page.controller.inlineInput.ready
                    Layout.fillWidth: true
                    Label { objectName: "voiceStatusTitle"; text: !page.controller.connected ? "请先在首页连接 Ring" : page.controller.voiceRuntimeError.length > 0 ? "语音暂不可用" : page.controller.statusTitle; color: theme.muted; font.pixelSize: 14; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    FigmaAction { objectName: "voiceHomeButton"; visible: !page.controller.connected; text: "返回首页"; onClicked: page.homeRequested() }
                }
                Label { objectName: "voiceStatusDetail"; Layout.fillWidth: true; visible: page.controller.voiceRuntimeError.length > 0 || page.controller.statusKind === "error"; text: page.controller.voiceRuntimeError.length > 0 ? page.controller.voiceRuntimeError + "；检查模型配置后再次开启语音，Ring 保持连接。" : page.controller.statusDetail; font.pixelSize: 14; color: theme.warning; wrapMode: Text.Wrap }
                PermissionNotice { objectName: "accessibilityPermissionWarning"; Layout.fillWidth: true; actionText: ""; message: page.controller.inlineInput.permissions.accessibilityWarningText ? page.controller.inlineInput.permissions.accessibilityWarningText + " 请在设置的“系统与权限”中管理。" : "" }
            }
        }
        Rectangle {
            objectName: "voiceTrialCard"
            visible: !page.historyOnly
            width: parent.width; height: Math.max(voiceTrialContent.implicitHeight + 40, page.availableHeight - voiceInputCard.height - pageContent.spacing)
            radius: 12; color: theme.surface; border.color: theme.line
            ColumnLayout {
                id: voiceTrialContent
                anchors.fill: parent; anchors.margins: 20; spacing: 12
                RowLayout {
                    Layout.fillWidth: true; Layout.preferredHeight: 36
                    Label { Layout.fillWidth: true; Layout.preferredHeight: 32; Layout.alignment: Qt.AlignTop; verticalAlignment: Text.AlignVCenter; text: "输入模拟"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                    WorkbenchNavigation { objectName: "voiceTutorialButton"; text: "功能教学"; onClicked: page.tutorialRequested() }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 4
                    Label { Layout.preferredHeight: 24; verticalAlignment: Text.AlignVCenter; text: "输入源"; color: theme.text; font.pixelSize: 16 }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        AudioInputSelect {
                            objectName: "voiceAudioSourceCombo"
                            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1
                            leftPadding: 16; font.pixelSize: 16; font.weight: Font.Medium
                            controller: page.controller
                        }
                        VoiceAudioTest {
                            id: voiceSoundCheck
                            objectName: "voiceCompactAudioTest"
                            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1
                            compact: true
                            practice: page.controller.voicePractice
                        }
                    }
                    Label {
                        objectName: "voiceCompactAudioStatus"
                        Layout.fillWidth: true
                        visible: voiceSoundCheck.attempted
                        text: voiceSoundCheck.statusText
                        color: voiceSoundCheck.statusColor
                        font.pixelSize: 12; wrapMode: Text.Wrap
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
                    Label { Layout.preferredHeight: 24; verticalAlignment: Text.AlignVCenter; text: "虚拟文本框"; color: theme.text; font.pixelSize: 16 }
                    VoicePracticeEditor {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        controller: page.controller
                        practice: page.controller.voicePractice
                    }
                }
            }
        }
        Rectangle {
            id: voiceHistoryCard
            objectName: "voiceHistoryCard"
            visible: page.historyOnly
            width: parent.width
            height: Math.max(560, page.availableHeight)
            color: "transparent"; radius: 12
            Rectangle { x: 0; y: filterPanel.height + 20; width: parent.width; height: parent.height - y; radius: 12; color: theme.surface; border.color: theme.line }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 0; spacing: 16
                Rectangle {
                    Layout.fillWidth: true
                    id: filterPanel
                    objectName: "voiceHistoryFilterPanel"
                    implicitHeight: historyFilters.implicitHeight + 28
                    radius: 12; color: theme.surface; border.color: historyTheme.line
                    GridLayout {
                        id: historyFilters
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 14
                        columns: width >= 776 ? 5 : 3
                        columnSpacing: 8; rowSpacing: 12
                        component FilterField: ColumnLayout {
                            property string title: ""
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            Layout.preferredWidth: 112
                            Layout.alignment: Qt.AlignBottom
                            spacing: 6
                            Label {
                                Layout.fillWidth: true
                                visible: false; text: parent.title; color: historyTheme.muted
                                font.family: historyTheme.family; font.pixelSize: historyTheme.detailSize
                                lineHeight: historyTheme.detailLineHeight; lineHeightMode: Text.FixedHeight
                            }
                        }
                        FilterField {
                            title: "搜索记录"; Layout.preferredWidth: 310
                            FigmaTextField {
                                id: historySearch
                                objectName: "voiceHistorySearch"
                                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: historyTheme.fieldHeight + 2
                                leftPadding: 12; rightPadding: 12; topPadding: 0; bottomPadding: 0
                                hintText: "输入关键词检索"
                                Accessible.name: "搜索语音历史"
                                selectByMouse: true
                                font.family: historyTheme.family; font.pixelSize: historyTheme.detailSize
                                color: historyTheme.text
                                placeholderTextColor: historyTheme.text
                                background: Rectangle { color: historyTheme.surface; radius: 8; border.color: historySearch.activeFocus ? historyTheme.primary : historyTheme.line }
                                onTextEdited: searchDebounce.restart()
                                onAccepted: { searchDebounce.stop(); page.historyModel.searchText = text }
                            }
                        }
                        FilterField {
                            title: "记录类型"
                            VoiceHistorySelect {
                                objectName: "voiceHistoryModeFilter"
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                Accessible.name: "记录类型"
                                model: ["全部类型", "听写", "润色", "指令"]
                                currentIndex: ["all", "dictation", "polish", "edit"].indexOf(page.historyModel.modeFilter)
                                onActivated: page.historyModel.modeFilter = ["all", "dictation", "polish", "edit"][currentIndex]
                            }
                        }
                        FilterField {
                            title: "应用"
                            VoiceHistorySelect {
                                objectName: "voiceHistoryApplicationFilter"
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                Accessible.name: "应用"
                                model: page.historyModel.applicationOptions
                                textRole: "label"; valueRole: "value"
                                currentIndex: page.historyModel.applicationOptions.map(function(item) { return item.value }).indexOf(page.historyModel.applicationFilter)
                                onActivated: page.historyModel.applicationFilter = currentValue
                            }
                        }
                        FilterField {
                            title: "时间"
                            VoiceHistorySelect {
                                objectName: "voiceHistoryTimeFilter"
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                Accessible.name: "时间"
                                model: ["全部时间", "今天", "近 7 天", "近 30 天"]
                                currentIndex: ["all", "today", "week", "month"].indexOf(page.historyModel.timeFilter)
                                onActivated: page.historyModel.timeFilter = ["all", "today", "week", "month"][currentIndex]
                            }
                        }
                        FilterField {
                            title: "结果"
                            VoiceHistorySelect {
                                objectName: "voiceHistoryOutcomeFilter"
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                Accessible.name: "记录结果"
                                model: ["全部结果", "已完成", "已撤回", "未完成", "已取消", "未识别", "已中断"]
                                currentIndex: ["all", "completed", "undone", "failed", "cancelled", "unrecognized", "interrupted"].indexOf(page.historyModel.outcomeFilter)
                                onActivated: page.historyModel.outcomeFilter = ["all", "completed", "undone", "failed", "cancelled", "unrecognized", "interrupted"][currentIndex]
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.topMargin: 24; spacing: 12
                    Label {
                        Layout.fillWidth: true; text: "最近记录"
                        color: historyTheme.text; font.family: historyTheme.family
                        font.pixelSize: historyTheme.titleSize; font.weight: Font.Medium
                        lineHeight: 26; lineHeightMode: Text.FixedHeight
                    }
                    FigmaAction {
                        objectName: "voiceHistoryResetFilters"
                        text: "重置筛选"; quiet: true
                        visible: page.historyFiltered
                        enabled: page.historyFiltered
                        font.family: historyTheme.family
                        font.pixelSize: historyTheme.detailSize
                        implicitHeight: 26
                        onClicked: page.resetHistoryFilters()
                    }
                    VoiceHistoryAction {
                        id: historyOptionsButton
                        objectName: "voiceHistoryOptionsButton"
                        asset: "more"; filled: true; hint: "更多历史操作"
                        menuOpen: historyOptions.visible
                        onClicked: historyOptions.open()
                        UiMenu {
                            id: historyOptions
                            objectName: "voiceHistoryOptionsMenu"
                            UiMenuItem {
                                objectName: "exportAllVoiceHistoryButton"
                                text: page.controller.voiceHistoryExport.busy ? "正在导出…" : "筛选应用并导出语音记录"
                                enabled: !page.controller.voiceHistoryExport.busy
                                onTriggered: page.controller.voiceHistoryExport.export()
                            }
                            UiMenuItem { objectName: "openDataDirectoryButton"; text: "打开数据文件夹"; onTriggered: page.controller.openDataDirectory() }
                            UiMenuItem { objectName: "clearVoiceHistoryButton"; destructive: true; text: "清空全部语音记录"; enabled: page.controller.voiceHistoryEntries.length > 0; onTriggered: clearHistoryDialog.open() }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20; visible: page.historyFiltered; spacing: 12
                    Label {
                        text: "最近记录"; color: historyTheme.text
                        font.family: historyTheme.family; font.pixelSize: historyTheme.sectionSize; font.weight: Font.Medium
                        lineHeight: 22; lineHeightMode: Text.FixedHeight
                    }
                    Label {
                        objectName: "voiceHistoryCount"
                        Layout.fillWidth: true
                        text: page.historyFiltered ? "找到 " + voiceHistoryList.count + " 条 · 共 " + page.controller.voiceHistoryEntries.length + " 条" : ""
                        color: historyTheme.muted; font.family: historyTheme.family; font.pixelSize: historyTheme.detailSize
                    }
                }
                Label {
                    objectName: "voiceHistoryActionMessage"
                    Layout.fillWidth: true
                    visible: text.length > 0 && !deleteHistoryDialog.visible
                    text: page.controller.historyActionMessage
                    color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
                }
                UiNotice {
                    objectName: "voiceHistoryPageExportStatus"
                    Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                    text: page.controller.voiceHistoryExport.message
                    warning: page.controller.voiceHistoryExport.error
                }
                ListView {
                    id: voiceHistoryList
                    objectName: "voiceHistoryList"
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.bottomMargin: 20
                    clip: true; spacing: 12
                    model: page.historyModel
                    ScrollBar.vertical: PreferenceScrollBar { }
                    delegate: VoiceHistoryEntry {
                        width: voiceHistoryList.width
                        controller: page.controller
                        onDeleteRequested: function(record) { page.requestHistoryDelete(record) }
                    }
                    ColumnLayout {
                        anchors.centerIn: parent; width: parent.width - 24; spacing: 9
                        visible: voiceHistoryList.count === 0
                        FigmaIcon { Layout.alignment: Qt.AlignHCenter; symbol: "history"; width: 30; height: 30 }
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
                    visible: page.associationUiVisible
                    Layout.fillWidth: true; spacing: 8
                    Switch {
                        objectName: "smartAssociationSwitch"
                        Layout.fillWidth: true
                        text: "智能关联推荐"
                        checked: page.controller.smartAssociationEnabled
                        onToggled: page.controller.smartAssociationEnabled = checked
                    }
                    FigmaAction { objectName: "openAssociationCenterButton"; text: "数据关联中心"; quiet: true; onClicked: page.controller.performAssociationAction("center.open", "") }
                }
            }
        }
    }
    FigmaDialog {
        id: hotwordDialog
        objectName: "hotwordDialog"
        parent: Overlay.overlay; popupType: Popup.Item
        x: Math.max(16, parent.width - width - 24); y: 104
        width: Math.min(600, parent.width - 48); height: Math.min(hotwordLibrary.implicitHeight, parent.height - 128)
        padding: 0; topInset: 0; bottomInset: 0; modal: true; header: null; footer: null
        onClosed: { page.hotwordsExpanded = false; hotwordLibrary.cancelDrag() }
        background: Rectangle { color: theme.surface; radius: 12; border.color: theme.line }
        contentItem: UiScrollView {
            clip: true; contentWidth: availableWidth
            HotwordSettings { id: hotwordLibrary; objectName: "personalHotwordSettings"; width: parent.width; height: implicitHeight; service: page.controller.hotwords; onCloseRequested: hotwordDialog.close() }
        }
    }
    FigmaDialog {
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
            FigmaAction {
                objectName: "cancelDeleteHistoryButton"
                text: "取消"
                onClicked: deleteHistoryDialog.reject()
            }
            FigmaAction {
                objectName: "confirmDeleteHistoryButton"
                text: "删除记录"; primary: true
                onClicked: {
                    if (page.controller.deleteVoiceHistory(deleteHistoryDialog.interactionId)) deleteHistoryDialog.close()
                }
            }
        }
    }
    FigmaDialog {
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
