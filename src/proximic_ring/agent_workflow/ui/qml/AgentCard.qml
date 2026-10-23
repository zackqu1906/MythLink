import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    property var cardInfo: ({key:"task", kind:"task", options:[]})
    readonly property string cardKey: cardInfo.key || "task"
    readonly property bool task: cardInfo.kind === "task"
    readonly property var requestInfo: cardInfo.request || ({})
    readonly property bool question: task && !!requestInfo.token
    readonly property string inputKey: agent.inputKey
    readonly property bool sending: question && !!requestInfo.submitting
    readonly property bool compact: !agent.workspaceActive
    readonly property bool selected: agent.focused && agent.focusKey === cardKey
    readonly property bool completed: task && !question && agent.taskCompleted
    readonly property bool dismissible: task
    readonly property bool choosingConversation: task && agent.conversationPicker.opened
    readonly property string statusTitle: question ? (sending ? "正在发送回答…" : "等待你的回答")
        : !task ? (cardInfo.title || "等待授权")
        : completed ? "任务已完成" : agent.busy ? agent.status
        : agent.feedback.length ? agent.status : "Agent"
    readonly property var attachments: task && !question ? agent.inputAttachments : []
    property bool syncing: false
    property string editorKey: ""
    property bool motionEnabled: true
    readonly property int buttonFontSize: 12
    readonly property int compactControlSize: 24
    readonly property int maximumExpandedHeight: task ? 420 : 288
    readonly property int compactWidth: 344
    readonly property int expandedWidth: 440
    // The native frame is the animation clock. Stable content widths prevent
    // text reflow from changing the target height on every resize frame.
    readonly property real expansion: Math.max(0, Math.min(1,
        (width - compactWidth) / (expandedWidth - compactWidth)))
    function smoothProgress(value) {
        const t = Math.max(0, Math.min(1, value))
        return t * t * (3 - 2 * t)
    }
    implicitWidth: compact ? compactWidth : expandedWidth
    implicitHeight: compact ? Math.ceil(Math.max(task ? 84 : 72, compactContent.implicitHeight + 24))
        : Math.ceil(Math.min(maximumExpandedHeight, fullContent.implicitHeight + 32))
    color: Qt.rgba((39 + 4 * expansion) / 255, (40 + 4 * expansion) / 255,
                   (41 + 5 * expansion) / 255, 1)
    radius: 24 + 2 * expansion
    border.width: 1
    border.color: selected ? "#64666A" : "#484A4D"
    clip: true
    Behavior on border.color { ColorAnimation { duration: card.motionEnabled ? 180 : 0 } }
    Accessible.role: Accessible.Pane
    Accessible.name: task ? "Agent 任务" : cardInfo.title || "Agent 请求"

    // Keep pointer classification inside QML and never expose browser/Control
    // implementation children to Python. Only these declared regions participate.
    function containsPoint(item, x, y) {
        if (!item || !item.visible || item.opacity <= 0) return false
        if (!item.contains(item.mapFromItem(card, x, y))) return false
        for (let ancestor = item.parent; ancestor; ancestor = ancestor.parent) {
            if (!ancestor.visible || ancestor.opacity <= 0) return false
            if (ancestor.clip && !ancestor.contains(ancestor.mapFromItem(card, x, y))) return false
            if (ancestor === card) break
        }
        return true
    }
    function dragHitTest(x: real, y: real): int {
        // 0: control/content; 1: draggable background; 2: close without focusing.
        if (!card.enabled || !card.visible || !card.contains(Qt.point(x, y))) return 0
        if (conversationPicker.visible) return 0
        if (containsPoint(compactClose, x, y) || containsPoint(expandedClose, x, y)) return 2
        const regions = card.compact ? [compactSwitcher, compactNewTask, compactLink]
            : [expandedSwitcher, newTaskAction, linkAction, messageViewport,
               questionOptions, optionViewport, attachmentList, editorViewport,
               contextActions, composerActions, fullBody.ScrollBar.vertical]
        for (const region of regions) {
            if (containsPoint(region, x, y)) return 0
        }
        return 1
    }

    function refresh() {
        if (task && !editor.preeditText.length) {
            syncing = true
            const replaced = editorKey !== inputKey
            editorKey = inputKey
            if (editor.text !== agent.draft(editorKey)) editor.text = agent.draft(editorKey)
            if (replaced) editor.cursorPosition = editor.length
            syncing = false
        }
    }
    function focusEditor() {
        if (choosingConversation) return
        if (task && editor.enabled) editor.forceActiveFocus()
        else card.forceActiveFocus()
    }
    function restoreEditorFocus() {
        // A reply temporarily disables this editor. Restore only our active
        // window, never steal focus from an application or permission card.
        if (task && selected && !compact && !choosingConversation && card.Window.active && editor.enabled)
            editor.forceActiveFocus()
        syncInputFocus()
    }
    function syncInputFocus() {
        if (task) agent.editorFocused(editorKey, editor.activeFocus && selected && !compact && !choosingConversation)
    }
    function resetReadingPosition() {
        // The task and its questions share a view, but never a reading offset.
        // Height is derived from this card's content, not the previous card.
        fullBody.contentY = 0
        messageViewport.contentItem.contentY = 0
        questionOptions.contentItem.contentY = 0
        optionViewport.contentItem.contentY = 0
    }
    onCardInfoChanged: refresh()
    onCardKeyChanged: Qt.callLater(resetReadingPosition)
    onInputKeyChanged: {
        refresh()
        syncInputFocus()
        Qt.callLater(resetReadingPosition)
        Qt.callLater(restoreEditorFocus)
        if (task && !compact && motionEnabled) conversationTransition.restart()
    }
    ParallelAnimation {
        id: conversationTransition
        NumberAnimation { target: fullContent; property: "opacity"; from: 0.4; to: 1; duration: 180; easing.type: Easing.OutCubic }
        NumberAnimation { target: conversationShift; property: "y"; from: 6; to: 0; duration: 220; easing.type: Easing.OutCubic }
    }
    onSelectedChanged: {
        if (!selected && task) agent.conversationPicker.close()
        syncInputFocus(); Qt.callLater(restoreEditorFocus)
    }
    onCompactChanged: {
        if (compact && task) agent.conversationPicker.close()
        syncInputFocus()
    }
    onChoosingConversationChanged: { syncInputFocus(); if (!choosingConversation) Qt.callLater(restoreEditorFocus) }
    Component.onCompleted: refresh()

    AgentConversationPicker {
        id: conversationPicker
        objectName: "agentConversationPicker"
        parent: linkAction
        visible: card.choosingConversation && !card.compact
        width: Math.min(256, card.width - 32)
        x: linkAction.width - width
        y: linkAction.height + 6
        maximumHeight: Math.max(48, card.height - fullBody.y - heading.y - heading.height - 14)
    }

    component TaskSwitcher: Button {
        visible: card.task && agent.workspaceCount > 1
        focusPolicy: Qt.NoFocus
        implicitWidth: 40; implicitHeight: 24
        padding: 0; leftInset: 0; rightInset: 0; topInset: 0; bottomInset: 0
        leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
        contentItem: Text {
            text: agent.workspacePosition + "/" + agent.workspaceCount
            color: "#BFC1C5"; font.pixelSize: 11
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle { radius: 12; color: parent.hovered ? "#484B50" : "#37393D" }
        Accessible.name: "切换到下一个任务"
        ToolTip.visible: hovered; ToolTip.text: "切换到下一个任务"
        onClicked: agent.nextTask()
    }

    component RoundAction: Button {
        property string hint: ""
        property string actionKind: "cancel"
        implicitWidth: 24; implicitHeight: 24
        focusPolicy: Qt.NoFocus
        padding: 0; leftInset: 0; rightInset: 0; topInset: 0; bottomInset: 0
        leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
        background: Rectangle { radius: 12; color: parent.hovered ? "#505050" : "transparent" }
        contentItem: Item {
            AgentActionIcon {
                anchors.centerIn: parent; width: 18; height: 18
                action: parent.parent.actionKind; ink: "#CFCFCF"
            }
        }
        Accessible.name: hint
        ToolTip.visible: hovered; ToolTip.text: hint; ToolTip.delay: 650
    }

    component StatusMark: Rectangle {
        id: statusMark
        readonly property bool spinning: agent.busy && !card.question && card.task && !card.completed
        width: 28; height: 28; radius: width / 2
        color: card.completed ? "transparent" : card.question || !card.task ? "#3D362A" : "#363739"
        Accessible.role: Accessible.Indicator
        Accessible.name: card.statusTitle
        AgentActionIcon {
            objectName: "agentStaticStatusIcon"
            visible: !statusMark.spinning
            anchors.centerIn: parent; width: card.completed ? Math.min(24, parent.width) : 18; height: width
            action: card.completed ? "completed" : card.question || !card.task ? "waiting"
                : "idle"
            ink: card.completed ? "#A3D9B0" : card.question || !card.task ? "#E2C28E" : "#D3D4D5"
        }
        // Keep rotation on a dedicated spinner. Stopping a RotationAnimator
        // retains its angle, which must never carry over to the completion mark.
        AgentActionIcon {
            objectName: "agentRunningStatusIcon"
            anchors.centerIn: parent; width: 18; height: 18
            visible: statusMark.spinning
            action: "running"; ink: "#D3D4D5"
            RotationAnimator on rotation {
                from: 0; to: 360; duration: 1400; loops: Animation.Infinite
                running: card.motionEnabled && visible
            }
        }
    }

    // Compact cards remain visible while focus belongs to an application.
    ColumnLayout {
        id: compactContent
        z: 1
        opacity: 1 - card.smoothProgress(card.expansion / 0.65)
        visible: opacity > 0
        enabled: card.compact
        width: card.compactWidth - 32
        anchors.left: parent.left; anchors.top: parent.top
        anchors.leftMargin: 16; anchors.topMargin: 12
        spacing: 6
        RowLayout {
            id: compactHeading
            Layout.fillWidth: true
            Layout.preferredHeight: card.compactControlSize
            spacing: 6
            RoundAction {
                id: compactClose
                objectName: "agentCompactCloseButton"
                visible: card.dismissible
                enabled: agent.canDismiss
                Layout.preferredWidth: card.compactControlSize
                Layout.preferredHeight: card.compactControlSize
                Layout.alignment: Qt.AlignVCenter
                actionKind: "close"; hint: "隐藏当前对话"
                onClicked: agent.dismiss()
                background: Rectangle {
                    radius: width / 2; color: parent.hovered ? "#505256" : "#363739"
                    border.width: 1; border.color: "#515357"
                }
            }
            StatusMark {
                objectName: "agentCompactStatus"
                Layout.preferredWidth: card.compactControlSize
                Layout.preferredHeight: card.compactControlSize
                Layout.alignment: Qt.AlignVCenter
            }
            Text {
                objectName: "agentCompactTitle"
                Layout.fillWidth: true
                text: card.statusTitle
                color: card.completed ? "#B8DEC1" : "#E4E5E6"; font.pixelSize: 12; font.weight: Font.Medium
                elide: Text.ElideRight; textFormat: Text.PlainText
            }
            TaskSwitcher {
                id: compactSwitcher; objectName: "agentCompactTaskSwitcher"
                Layout.preferredWidth: card.compactControlSize
                Layout.preferredHeight: card.compactControlSize
                Layout.alignment: Qt.AlignVCenter
                contentItem: Text {
                    text: agent.workspacePosition + "/" + agent.workspaceCount
                    color: "#BFC1C5"; font.pixelSize: 10; minimumPixelSize: 7
                    fontSizeMode: Text.Fit
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                }
            }
            RoundAction {
                id: compactNewTask
                objectName: "agentCompactNewTaskButton"
                Layout.preferredWidth: card.compactControlSize
                Layout.preferredHeight: card.compactControlSize
                Layout.alignment: Qt.AlignVCenter
                visible: card.task
                enabled: agent.canAddTask
                actionKind: "attach"; hint: "新建对话"
                onClicked: agent.addTask()
                z: 3
            }
            RoundAction {
                id: compactLink
                objectName: "agentCompactLinkButton"
                Layout.preferredWidth: card.compactControlSize
                Layout.preferredHeight: card.compactControlSize
                Layout.alignment: Qt.AlignVCenter
                visible: card.task; enabled: agent.canAddTask
                actionKind: "link"; hint: "连接到最近对话"
                onClicked: agent.conversationPicker.open()
            }
        }
        Text {
            objectName: "agentCompactSummary"
            Layout.fillWidth: true
            text: card.question ? requestInfo.message : card.task ? (agent.feedback || agent.taskText || "双捏中指，开始对话") : cardInfo.message || cardInfo.title || ""
            color: "#AAACAF"; font.pixelSize: 12; maximumLineCount: 2
            wrapMode: Text.Wrap; elide: Text.ElideRight; textFormat: Text.PlainText
        }
    }
    MouseArea {
        anchors.fill: parent; enabled: card.compact
        onClicked: { agent.windowFocused(card.cardKey, true); agent.focusCard(card.cardKey) }
    }

    Flickable {
        id: fullBody
        objectName: "agentCardBody"
        opacity: card.smoothProgress((card.expansion - 0.15) / 0.85)
        visible: opacity > 0
        enabled: !card.compact
        x: 16; y: 16
        width: card.expandedWidth - 32
        height: Math.max(0, card.height - 32)
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        contentWidth: width
        contentHeight: Math.max(height,fullContent.implicitHeight)
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
            // Material's transparent scrollbar still intercepts clicks at the
            // right edge. Only expose it when there is actual overflow.
            visible: fullBody.contentHeight > fullBody.height + 1
        }
    ColumnLayout {
        id: fullContent
        transform: Translate { id: conversationShift }
        width: fullBody.width; height: implicitHeight
        spacing: 10
        Label {
            objectName: "agentRecordingWarning"
            visible: card.task && agent.recordingWarning.length > 0
            Layout.fillWidth: true
            text: agent.recordingWarning; color: "#E2C28E"; font.pixelSize: 11
            wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
        RowLayout {
            id: heading
            Layout.fillWidth: true; Layout.preferredHeight: 28; spacing: 8
            RoundAction {
                id: expandedClose
                objectName: "agentCloseButton"
                visible: card.dismissible; enabled: agent.canDismiss
                actionKind: "close"; hint: "隐藏当前对话"
                onClicked: agent.dismiss()
                background: Rectangle {
                    radius: 12; color: parent.hovered ? "#505256" : "#363739"
                    border.width: 1; border.color: "#515357"
                }
            }
            StatusMark { objectName: "agentExpandedStatus" }
            Label {
                Layout.fillWidth: true
                text: card.statusTitle
                color: card.completed ? "#B8DEC1" : "#D6D6D6"; font.pixelSize: 12; font.weight: Font.Medium
                elide: Text.ElideRight; textFormat: Text.PlainText
            }
            TaskSwitcher { id: expandedSwitcher; objectName: "agentTaskSwitcher" }
            RoundAction {
                id: newTaskAction
                objectName: "agentNewTaskButton"
                visible: card.task
                enabled: agent.canAddTask
                implicitWidth: 28; implicitHeight: 28
                actionKind: "attach"; hint: "新建对话"
                onClicked: agent.addTask()
            }
            RoundAction {
                id: linkAction
                objectName: "agentLinkButton"
                visible: card.task; enabled: agent.canAddTask
                actionKind: "link"; hint: "连接到最近对话"
                onClicked: agent.conversationPicker.open()
            }
        }
        Label {
            id: conversationTitle
            objectName: "agentConversationTitle"
            visible: card.task && agent.conversationTitle.length > 0
            Layout.fillWidth: true
            text: agent.conversationTitle; color: "#AAACAF"; font.pixelSize: 11
            elide: Text.ElideRight; textFormat: Text.PlainText
        }
        ScrollView {
            id: messageViewport
            objectName: "agentMessageViewport"
            visible: !card.task || card.question || agent.busy || agent.feedback.length
            Layout.fillWidth: true
            // Grow the window upwards before scrolling this text. Reserve the
            // current card's controls and composer; none of these measurements
            // depend on the animated window height or another request's size.
            Layout.preferredHeight: Math.min(message.implicitHeight, Math.max(50,
                card.maximumExpandedHeight - 32 - trailingContent.implicitHeight - 10
                - (heading.visible ? 38 : 0)
                - (conversationTitle.visible ? conversationTitle.implicitHeight + 10 : 0)))
            Layout.minimumHeight: Layout.preferredHeight
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            AgentMessage {
                id: message
                objectName: "agentMessage"
                width: messageViewport.availableWidth
                viewport: messageViewport
                markdown: card.task
                text: card.question ? requestInfo.message : card.task ? (agent.feedback || agent.taskText || "正在处理…") : cardInfo.message || ""
            }
        }
        ColumnLayout {
            id: trailingContent
            Layout.fillWidth: true
            spacing: 10
            ScrollView {
                id: questionOptions
                objectName: "agentQuestionOptions"
                visible: card.question && numberedOptions.count > 0
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(148, numberedList.implicitHeight)
                Layout.minimumHeight: Layout.preferredHeight
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                Column {
                    id: numberedList
                    width: questionOptions.availableWidth
                    spacing: 8
                    Repeater {
                        id: numberedOptions
                        model: card.question ? requestInfo.options || [] : []
                        delegate: Column {
                            required property var modelData
                            required property int index
                            width: numberedList.width
                            spacing: 2
                            Text {
                                objectName: "agentChoiceText" + index
                                width: parent.width
                                text: (index + 1) + ".  " + modelData.label
                                color: modelData.enabled ? "#E4E4E4" : "#929292"
                                font.pixelSize: 13; wrapMode: Text.Wrap; textFormat: Text.PlainText
                            }
                            Text {
                                visible: !!modelData.reason
                                x: 19; width: parent.width - 19
                                text: modelData.reason || ""
                                color: "#A8A8A8"; font.pixelSize: 12
                                wrapMode: Text.Wrap; textFormat: Text.PlainText
                            }
                        }
                    }
                }
            }
            Text {
                visible: card.task && (agent.errorMessage.length || !!requestInfo.blocked || !!requestInfo.expired
                                      || (!card.question && cardInfo.questionCount > 0))
                Layout.fillWidth: true
                text: agent.errorMessage || (requestInfo.expired ? "刚才的问题已结束，清空输入后继续。" : requestInfo.blocked ||
                      "有新询问，本次输入结束后自动显示。")
                color: "#DCBE8D"; font.pixelSize: 12; wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
            Text {
                visible: !card.task && !!cardInfo.blocked
                Layout.fillWidth: true; text: cardInfo.blocked || ""; wrapMode: Text.Wrap
                color: "#DCBE8D"; font.pixelSize: 11; maximumLineCount: 2; elide: Text.ElideRight
            }
            ScrollView {
                id: optionViewport
                objectName: "agentOptionViewport"
                visible: !card.task && optionRepeater.count > 0
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(card.question ? 78 : 96,optionFlow.implicitHeight)
                Layout.minimumHeight: Layout.preferredHeight
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            Flow {
                id: optionFlow
                width: optionViewport.availableWidth
                spacing: 7
                Repeater {
                    id: optionRepeater
                    model: card.task ? [] : cardInfo.options || []
                    delegate: Button {
                        required property var modelData
                        objectName: "agentOption" + modelData.index
                        text: modelData.label
                        enabled: modelData.enabled
                        focusPolicy: Qt.NoFocus
                        leftInset: 0; rightInset: 0; topInset: 0; bottomInset: 0
                        leftPadding: 14; rightPadding: 14; topPadding: 8; bottomPadding: 8
                        implicitHeight: Math.max(32, contentItem.implicitHeight + 16)
                        implicitWidth: Math.min(optionViewport.availableWidth, Math.max(68, contentItem.implicitWidth + 28))
                        readonly property bool chosen: false
                        background: Rectangle {
                            radius: 16
                            color: parent.chosen ? "#ECECEC" : parent.hovered ? "#494949" : "#3C3C3C"
                            Behavior on color { ColorAnimation { duration: card.motionEnabled ? 120 : 0 } }
                            border.color: parent.chosen ? "#ECECEC" : "#606060"; border.width: 1
                            opacity: parent.enabled ? 1 : .4
                        }
                        contentItem: Text { text: parent.text; color: parent.chosen ? "#232323" : "#E5E5E5"; font.pixelSize: card.buttonFontSize; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        onClicked: agent.choose(card.cardKey, modelData.index)
                    }
                }
            }
            }
            Rectangle {
                visible: card.task && (card.question || agent.busy || agent.feedback.length)
                Layout.fillWidth: true; Layout.preferredHeight: 1
                color: "#484848"
            }
            ListView {
                id: attachmentList
                objectName: "agentAttachments"
                visible: card.task && card.attachments.length > 0
                Layout.fillWidth: true
                Layout.preferredHeight: 64
                orientation: ListView.Horizontal
                spacing: 8; clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: card.attachments
                ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    objectName: "agentAttachment" + index
                    width: 188; height: 56; radius: 12
                    color: "#3E3E3E"; border.color: "#555555"
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 8; spacing: 8
                        Rectangle {
                            width: 38; height: 38; radius: 6; color: "#505050"; clip: true
                            Image {
                                objectName: "agentAttachmentPreview" + index
                                anchors.fill: parent
                                source: modelData.kind === "image" ? modelData.url : ""
                                sourceSize: Qt.size(76, 76)
                                fillMode: Image.PreserveAspectCrop; asynchronous: true
                            }
                            AgentActionIcon {
                                anchors.centerIn: parent; width: 24; height: 24
                                visible: modelData.kind !== "image"
                                action: "file"; ink: "#D5D5D5"
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 3
                            Text {
                                Layout.fillWidth: true; text: modelData.name
                                color: "#EEEEEE"; font.pixelSize: 12
                                elide: Text.ElideMiddle; textFormat: Text.PlainText
                            }
                            Text {
                                text: (modelData.kind === "image" ? "图片 · " : "文件 · ")
                                    + (modelData.size >= 1048576 ? (modelData.size / 1048576).toFixed(1) + " MB"
                                    : Math.max(1, Math.ceil(modelData.size / 1024)) + " KB")
                                color: "#AAAAAA"; font.pixelSize: 10
                            }
                        }
                        RoundAction {
                            objectName: "agentRemoveAttachment" + index
                            implicitWidth: 20; implicitHeight: 20
                            actionKind: "cancel"; hint: "移除 " + modelData.name
                            onClicked: agent.removeAttachment(card.inputKey, modelData.path)
                        }
                    }
                }
            }
            ScrollView {
                id: editorViewport
                visible: card.task
                Layout.fillWidth: true
                Layout.preferredHeight: Math.max(card.task && !card.question && !agent.busy && !agent.feedback.length ? 54 : 38,
                                                Math.min(96,editor.contentHeight))
                Layout.minimumHeight: Layout.preferredHeight
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                TextArea {
                    id: editor
                    objectName: "agentEditor"
                    width: editorViewport.availableWidth
                    height: Math.max(editorViewport.availableHeight,contentHeight)
                    color: "#F4F4F4"
                    font.pixelSize: card.task && !card.question && !agent.busy && !agent.feedback.length ? 18 : 15
                    selectionColor: "#63758A"; selectedTextColor: "white"
                    wrapMode: TextEdit.Wrap; selectByMouse: true
                    textFormat: TextEdit.PlainText
                    padding: 0; leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                    enabled: !card.sending && !requestInfo.blocked
                    background: Item {}
                    Accessible.name: card.question ? "Agent 问题回答" : "Agent 任务输入"
                    Text {
                        x: 0; y: 0; width: parent.width
                        visible: !editor.text.length && !editor.preeditText.length
                        text: card.question ? ((requestInfo.options || []).length ? "输入编号或直接回答…" : "回答 Agent…") : (agent.busy ? "修改当前任务…" : "Do anything")
                        color: "#949494"; font: editor.font
                    }
                    cursorDelegate: Rectangle {
                        width: 1.5; height: editor.font.pixelSize + 4
                        color: "#ECECEC"; visible: editor.activeFocus
                        SequentialAnimation on opacity {
                            running: editor.activeFocus; loops: Animation.Infinite
                            NumberAnimation { from: 1; to: 1; duration: 500 }
                            NumberAnimation { from: 0; to: 0; duration: 500 }
                        }
                    }
                    onTextChanged: {
                        if (!card.syncing) agent.edit(card.editorKey, text)
                    }
                    onActiveFocusChanged: card.syncInputFocus()
                    onEnabledChanged: Qt.callLater(card.restoreEditorFocus)
                    onPreeditTextChanged: agent.editorComposing(card.editorKey, preeditText.length > 0)
                    Keys.onPressed: function(event) {
                        if (!editor.preeditText.length && event.matches(StandardKey.Paste)
                                && agent.pasteAttachments(card.editorKey)) {
                            event.accepted = true
                            return
                        }
                        if (!editor.preeditText.length && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !(event.modifiers & Qt.ShiftModifier)) {
                            agent.submit(card.editorKey); event.accepted = true
                        }
                    }
                }
            }
            RowLayout {
                id: contextActions
                visible: card.task
                Layout.fillWidth: true; spacing: 8
                Button {
                    id: openCodexAction
                    objectName: "agentOpenCodexButton"
                    text: "从 Codex 中打开"
                    Accessible.name: text
                    enabled: agent.hasCodexThread; focusPolicy: Qt.NoFocus
                    implicitWidth: openCodexLabel.implicitWidth + 16; implicitHeight: 26
                    leftInset: 0; rightInset: 0; topInset: 0; bottomInset: 0
                    leftPadding: 8; rightPadding: 8; topPadding: 0; bottomPadding: 0
                    contentItem: Text {
                        id: openCodexLabel
                        objectName: "agentOpenCodexLabel"
                        text: openCodexAction.text; font.pixelSize: 11
                        color: openCodexAction.enabled ? "#B9B9B9" : "#777777"
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: openCodexAction.hovered && openCodexAction.enabled ? "#414141" : "transparent"
                        radius: 8
                    }
                    onClicked: agent.openCodexTask()
                    ToolTip.visible: hovered; ToolTip.delay: 650
                    ToolTip.text: enabled ? "打开 Codex 并跳转到此任务的对话" : "任务创建对话后可打开"
                }
                Item { Layout.fillWidth: true }
            }
            Text {
                visible: card.task && agent.codexSyncUnavailable
                Layout.fillWidth: true
                text: agent.codexSyncMessage; color: "#E2C28E"; font.pixelSize: 11
                wrapMode: Text.Wrap
            }
            Text {
                visible: card.task && agent.contextMessage.length > 0
                Layout.fillWidth: true
                text: agent.contextMessage; color: "#A5A5A5"; font.pixelSize: 11
                elide: Text.ElideRight
            }
            RowLayout {
                id: composerActions
                Layout.fillWidth: true; spacing: 8
                RoundAction {
                    objectName: "agentCaptureButton"
                    visible: card.task
                    enabled: !card.question && !agent.pickingAttachments && !agent.contextCapturing
                    implicitWidth: 30; implicitHeight: 30
                    actionKind: "capture"
                    hint: agent.autoContextMode === "smart" ? "截图，智能截图已开启"
                        : agent.autoContextMode === "always" ? "截图，自动截图已开启（每次任务）"
                        : "截图，智能截图未开启"
                    opacity: enabled ? 1 : .4
                    onClicked: agent.startCapture(card.editorKey)
                }
                Button {
                    id: approvalMode
                    objectName: "agentApprovalModeButton"
                    visible: card.task
                    text: agent.nativeExecution
                        ? (agent.approvalMode === "all" ? "全部授权" : agent.approvalMode === "auto" ? "自动授权" : "逐次授权")
                        : agent.autoApprove ? "自动授权" : "逐次授权"
                    Accessible.name: text
                    focusPolicy: Qt.NoFocus
                    implicitHeight: 30; implicitWidth: approvalLabel.implicitWidth + 36
                    leftInset: 0; rightInset: 0; topInset: 0; bottomInset: 0
                    padding: 6
                    leftPadding: 6; rightPadding: 6; topPadding: 6; bottomPadding: 6
                    background: Rectangle {
                        radius: 10; color: approvalMode.hovered ? "#414141" : "transparent"
                        Behavior on color { ColorAnimation { duration: card.motionEnabled ? 120 : 0 } }
                    }
                    contentItem: Item {
                        Row {
                            anchors.centerIn: parent; spacing: 6
                            Canvas {
                                id: approvalShield
                                width: 14; height: 16
                                property bool checked: agent.autoApprove
                                onCheckedChanged: requestPaint()
                                onPaint: {
                                    const c=getContext("2d")
                                    c.reset(); c.strokeStyle=approvalLabel.color; c.lineWidth=1.2
                                    c.lineJoin="round"; c.lineCap="round"
                                    c.beginPath(); c.moveTo(7,1.5); c.lineTo(12,3.5)
                                    c.lineTo(12,8); c.quadraticCurveTo(12,11.5,7,14)
                                    c.quadraticCurveTo(2,11.5,2,8); c.lineTo(2,3.5); c.closePath(); c.stroke()
                                    if (checked) { c.beginPath(); c.moveTo(4.6,7.3); c.lineTo(6.4,9); c.lineTo(9.5,5.8); c.stroke() }
                                }
                            }
                            Text {
                                id: approvalLabel
                                objectName: "agentApprovalLabel"
                                text: approvalMode.text
                                color: agent.nativeExecution && agent.approvalMode === "all" ? "#F5A45D" : "#B9B9B9"
                                onColorChanged: approvalShield.requestPaint()
                                font.pixelSize: card.buttonFontSize; height: 16
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                    ToolTip.visible: hovered
                    ToolTip.delay: 500
                    ToolTip.text: agent.approvalMode === "all"
                        ? "自动处理应用、命令、文件和网络授权；点击切换"
                        : agent.approvalMode === "auto" ? "应用权限优先始终允许，其他授权在浮窗确认；点击切换"
                        : "在浮窗或 Codex 中确认授权；已保存的始终允许仍有效；点击切换"
                    onClicked: agent.cycleApprovalMode()
                }
                Text {
                    visible: !card.task
                    text: "仅鼠标选择"
                    color: "#959595"; font.pixelSize: 11
                }
                Item { Layout.fillWidth: true }
                Text {
                    visible: card.task
                    objectName: "agentSendHint"
                    text: cardInfo.questionCount > 1 ? "还有 " + (cardInfo.questionCount - 1) + " 个询问" : "自动截图，上滑发送"
                    Layout.minimumWidth: 0; Layout.fillWidth: true; elide: Text.ElideRight
                    color: "#A5A5A5"; font.pixelSize: 11
                }
                Button {
                    id: composerAction
                    objectName: "agentSendButton"
                    readonly property bool hasText: editor.text.trim().length > 0 || editor.preeditText.trim().length > 0
                    readonly property bool hasInput: hasText || card.attachments.length > 0
                    readonly property string actionKind: hasInput || !agent.busy ? "send" : "cancel"
                    readonly property string hint: actionKind === "cancel" ? "取消任务"
                        : card.question ? "发送回答" : agent.busy ? "发送修改" : "发送任务"
                    visible: card.task
                    implicitWidth: 30; implicitHeight: 30; padding: 0
                    enabled: !agent.contextCapturing && (actionKind !== "send" || (hasInput && !editor.preeditText.length && !card.sending
                        && !requestInfo.blocked))
                    focusPolicy: Qt.NoFocus
                    leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                    leftInset: 0; rightInset: 0; topInset: 0; bottomInset: 0
                    Accessible.name: hint
                    ToolTip.visible: hovered; ToolTip.text: hint; ToolTip.delay: 650
                    background: Rectangle {
                        radius: width / 2
                        color: composerAction.down ? "#D8D8D8" : composerAction.hovered ? "#ECECEC" : "#FFFFFF"
                        Behavior on color { ColorAnimation { duration: card.motionEnabled ? 120 : 0 } }
                    }
                    contentItem: Item {
                        AgentActionIcon {
                            objectName: "agentSendIcon"
                            anchors.centerIn: parent; width: 22; height: 22
                            action: composerAction.actionKind; ink: "#111111"
                        }
                    }
                    onClicked: {
                        if (actionKind === "send") agent.submit(card.editorKey)
                        else agent.cancel()
                    }
                }
            }
        }
    }
    }
    DropArea {
        id: attachmentDrop
        objectName: "agentAttachmentDrop"
        anchors.fill: parent
        enabled: card.task && !card.question
        onEntered: function(drag) { drag.accepted = drag.hasUrls }
        onDropped: function(drop) {
            if (drop.hasUrls && agent.addAttachmentUrls(card.inputKey, drop.urls)) {
                drop.acceptProposedAction()
                agent.windowFocused(card.cardKey, true)
                agent.focusCard(card.cardKey)
            }
        }
    }
    Rectangle {
        anchors.fill: parent; radius: card.radius
        visible: attachmentDrop.containsDrag
        color: "#EB333333"; border.width: 2; border.color: "#ADBBCB"
        Text { anchors.centerIn: parent; text: "松开以添加图片或文件"; color: "#F4F4F4"; font.pixelSize: 15 }
    }
    Connections {
        target: agent
        function onChanged() { card.refresh() }
    }
}
