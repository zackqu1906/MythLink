// The standalone writing trial's left side: the input task card above the
// eight-direction selection card. Pending strokes are drawn at the editor's
// cursor. Short viewports get the compact metrics; narrow widths stack.
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Item {
    id: box
    objectName: "strokeCompositionPanel"
    required property var controller
    readonly property var touchpad: controller.touchpad
    property bool inputVisible: false
    // Height of the scrolling page around this box; 0 when unknown.
    property real viewportHeight: 0
    readonly property bool ringWriting: touchpad.active && touchpad.inputMode === "stroke"
    // Same wording as the standalone trial's selection hint.
    readonly property string selectionHint: {
        const state = touchpad.selectionState
        if (ringWriting && !editor.activeFocus) return "输入框未聚焦 · 戒指输入已暂停，点入输入框继续"
        if (state.phase === "selecting") {
            if (state.clear_armed) return "抬手清空 · 拉回取消"
            if (state.direction === "left") return "抬手删除 · 继续外拉可清空"
            return state.direction ? "双指选字 · 抬手确认" : "已识别双指 · 滑动选字"
        }
        if (state.phase === "writing") return "书写中 · 抬手结束"
        return "双指滑向格子选字 · 双指左滑删除"
    }
    readonly property bool narrow: width < 600
    // The trial's compact mode: give the menu height first on short work areas.
    readonly property bool compact: viewportHeight > 0 && viewportHeight < 780
    readonly property int fieldHeight: compact ? 40 : 46
    readonly property int fieldFont: compact ? 16 : 17
    readonly property int cardMargin: narrow ? 10 : compact ? 12 : 16
    // The basics sit left of the menu on wide cards and above it otherwise.
    readonly property bool tutorialOpen: touchpad.strokeTutorialOpen
    readonly property bool tutorialBeside: tutorialOpen && selectionCard.width >= 600
    readonly property int tutorialWidth: Math.round(Math.min(288, Math.max(236, selectionCard.width * 0.28)))
    readonly property int bodyGap: compact ? 12 : 16
    // Keep the whole selection card inside one viewport where possible.
    readonly property real menuSide: {
        const available = Math.max(0, selectionCard.width - 2 * cardMargin - selectionMenu.clearPullGutter
                                      - (tutorialBeside ? tutorialWidth + bodyGap : 0))
        // A stacked tutorial takes height from the menu.
        const stacked = tutorialOpen && !tutorialBeside ? tutorial.implicitHeight + bodyGap : 0
        const fitted = viewportHeight > 0 ? viewportHeight - (compact ? 200 : 224) - stacked : 432
        return Math.floor(Math.min(available, Math.max(288, Math.min(432, fitted))))
    }
    readonly property color ink: "#24334D"
    readonly property color mutedInk: "#738099"
    implicitHeight: workspace.implicitHeight + 24
    signal homeRequested()
    FigmaTheme { id: theme }

    function updateContext() {
        if (inputVisible) {
            touchpad.observeStrokeEditor(editor.text, editor.cursorPosition)
            touchpad.setStrokeContext(editor.text.slice(0, editor.cursorPosition))
        }
    }
    function syncInputTarget() {
        Qt.callLater(function() {
            if (box.touchpad) { box.touchpad.setLocalStrokeInput(box.inputVisible); box.updateContext() }
        })
    }
    onInputVisibleChanged: {
        syncInputTarget()
        touchpad.setLocalStrokeFocus(inputVisible && editor.activeFocus)
        if (!inputVisible) selectionMenu.updateTrace([], false)
    }
    Component.onCompleted: syncInputTarget()

    component Card: Rectangle {
        color: "#FFFFFF"; radius: 12
        border.color: "#E0E6F0"; border.width: 1
    }
    // Inline components cannot see this file's ids; colors repeat ink/mutedInk.
    component SectionTitle: Label {
        color: "#24334D"; font.pixelSize: 13; font.weight: Font.Bold
    }
    component Muted: Label {
        color: "#738099"; font.pixelSize: 11
    }
    Component {
        id: legendComponent
        Flow {
            objectName: "strokeGlyphLegend"
            spacing: 10
            Repeater {
                model: [{label: "已输入", ink: "#171C28", hollow: false},
                        {label: "未输入", ink: "#98A1B2", hollow: true},
                        {label: "模糊", ink: "#C79600", hollow: false},
                        {label: "疑似错笔", ink: "#DC2626", hollow: false},
                        {label: "下一笔", ink: "#779CF2", hollow: false}]
                delegate: Row {
                    required property var modelData
                    spacing: 4
                    Rectangle {
                        width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter
                        color: modelData.hollow ? "transparent" : modelData.ink
                        border.color: modelData.ink; border.width: modelData.hollow ? 1 : 0
                    }
                    Text { text: modelData.label; font.pixelSize: 11; color: box.mutedInk }
                }
            }
        }
    }

    ColumnLayout {
        id: workspace
        width: Math.max(0, Math.min(1120, box.width))
        anchors.horizontalCenter: parent.horizontalCenter
        y: 12
        spacing: box.compact ? 8 : 10

        RowLayout {
            objectName: "strokeStatusRow"
            Layout.fillWidth: true; Layout.preferredHeight: 36; spacing: 10
            Rectangle { Layout.preferredWidth: 7; Layout.preferredHeight: 7; Layout.alignment: Qt.AlignVCenter; radius: 4; color: box.touchpad.active ? theme.success : theme.placeholder }
            Label {
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.alignment: Qt.AlignVCenter
                text: box.touchpad.strokePreparing ? "正在准备笔画输入…"
                    : box.touchpad.active && box.touchpad.inputMode === "stroke" ? "笔画输入中 · " + box.touchpad.strokeGestureLabel + "回到鼠标"
                    : box.touchpad.active ? "鼠标模式 · 点击「开始试写」进入笔画"
                    : "点击「开始试写」用 Ring 写笔画，也可键入试写"
                font.pixelSize: 12; color: theme.muted
                elide: Text.ElideRight
            }
            FigmaAction {
                objectName: "strokeTrialToggleButton"
                visible: box.controller.connected
                Layout.preferredHeight: 36; Layout.alignment: Qt.AlignVCenter
                focusPolicy: Qt.NoFocus
                text: box.touchpad.active && box.touchpad.inputMode === "stroke" ? "停止试写" : "开始试写"
                primary: !(box.touchpad.active && box.touchpad.inputMode === "stroke")
                enabled: !box.touchpad.busy && (box.touchpad.active || box.touchpad.available)
                onClicked: {
                    editor.forceActiveFocus()
                    box.touchpad.setLocalStrokeInput(true)
                    box.touchpad.setLocalStrokeFocus(true)
                    box.touchpad.toggleLocalStrokeTrial()
                }
            }
            FigmaAction {
                visible: !box.controller.connected
                text: "连接 Ring"; Layout.preferredHeight: 34; Layout.alignment: Qt.AlignVCenter; focusPolicy: Qt.NoFocus
                onClicked: box.homeRequested()
            }
        }

        Card {
            id: taskCard
            objectName: "strokeTaskCard"
            Layout.fillWidth: true
            implicitHeight: task.implicitHeight + 2 * (box.compact ? 8 : 10)
            ColumnLayout {
                id: task
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                          leftMargin: box.cardMargin; rightMargin: box.cardMargin }
                spacing: box.compact ? 4 : 6

                GridLayout {
                    Layout.fillWidth: true
                    columns: box.narrow ? 2 : 4; columnSpacing: 8; rowSpacing: 6
                    SectionTitle { text: "输入任务"; Layout.columnSpan: box.narrow ? 2 : 1 }
                    Basic.TextField {
                        id: target
                        objectName: "strokeTrialTarget"
                        Layout.fillWidth: true; Layout.minimumWidth: 0
                        Layout.columnSpan: box.narrow ? 2 : 1
                        Layout.preferredHeight: box.compact ? 32 : 36
                        leftPadding: 8; rightPadding: 8; topPadding: 0; bottomPadding: 0
                        verticalAlignment: TextInput.AlignVCenter
                        font.family: theme.family; font.pixelSize: 13; color: box.ink
                        placeholderText: "粘贴或输入待测试文本"
                        placeholderTextColor: theme.muted
                        selectByMouse: true
                        enabled: !box.touchpad.trialActive
                        text: box.touchpad.trialTarget
                        onEditingFinished: box.touchpad.setTrialTarget(text)
                        Accessible.name: "待测试文本"
                        background: Rectangle {
                            radius: 6; color: "#F8FAFF"
                            border.width: 1; border.color: target.activeFocus ? "#708DE8" : "#DCE4EF"
                        }
                    }
                    FigmaAction {
                        objectName: "strokeTrialStart"
                        Layout.fillWidth: box.narrow; Layout.preferredHeight: box.compact ? 32 : 36
                        focusPolicy: Qt.NoFocus; primary: true
                        primaryColor: "#345EE5"; primaryHoverColor: "#264DC9"; primaryPressedColor: "#1F40A8"
                        text: box.touchpad.trialFinished ? "重新开始实验" : "开始实验"
                        enabled: !box.touchpad.trialActive
                        onClicked: {
                            box.touchpad.setTrialTarget(target.text)
                            editor.forceActiveFocus()
                            box.touchpad.setLocalStrokeInput(true)
                            box.touchpad.startTrial()
                        }
                    }
                    FigmaAction {
                        objectName: "strokeTrialFinish"
                        Layout.fillWidth: box.narrow; Layout.preferredHeight: box.compact ? 32 : 36
                        focusPolicy: Qt.NoFocus
                        text: "结束并保存"
                        enabled: box.touchpad.trialActive
                        onClicked: { box.touchpad.finishTrial(); editor.forceActiveFocus() }
                    }
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: box.narrow ? 1 : 2; columnSpacing: box.compact ? 8 : 10; rowSpacing: 6
                    RowLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1; spacing: 8
                        Muted { text: "待输入" }
                        Rectangle {
                            objectName: "strokeTrialPreview"
                            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: box.fieldHeight
                            radius: 8; color: "#F3F6FC"; clip: true
                            Text {
                                id: preview
                                // Keep the next expected character in view on long targets.
                                readonly property real margin: box.compact ? 10 : 12
                                readonly property real overflow: implicitWidth - (parent.width - 2 * margin)
                                x: overflow <= 0 ? margin : margin - overflow * box.touchpad.trialPosition
                                anchors.verticalCenter: parent.verticalCenter
                                textFormat: Text.RichText
                                text: box.touchpad.trialPreview
                                font.family: theme.family; font.pixelSize: box.compact ? 16 : 18; color: box.ink
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 1; spacing: 8
                        Muted { text: "已输入" }
                        Rectangle {
                            id: searchShell
                            objectName: "strokeSearchShell"
                            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: box.fieldHeight
                            radius: 6; color: "#F8FAFF"
                            border.color: editor.activeFocus ? "#708DE8" : "#DCE4EF"
                            border.width: 1
                            TextField {
                                id: editor
                                objectName: "strokeTestEditor"
                                anchors.fill: parent
                                leftPadding: box.compact ? 10 : 12; rightPadding: box.compact ? 10 : 12
                                topPadding: 0; bottomPadding: 0
                                verticalAlignment: TextInput.AlignVCenter
                                font.family: theme.family; font.pixelSize: box.fieldFont
                                color: box.touchpad.strokeCode.length ? "transparent" : box.ink; selectionColor: theme.selection; selectedTextColor: box.ink
                                placeholderText: ""
                                selectByMouse: !box.touchpad.trialActive
                                // The trial is sequential transcription: only the ring writes.
                                readOnly: box.touchpad.trialActive
                                background: Item {}
                                onTextChanged: {
                                    if (box.touchpad.trialActive) { deselect(); cursorPosition = length }
                                    box.updateContext()
                                }
                                onCursorPositionChanged: {
                                    if (box.touchpad.trialActive && cursorPosition !== length) { deselect(); cursorPosition = length }
                                    box.updateContext()
                                }
                                onActiveFocusChanged: box.touchpad.setLocalStrokeFocus(box.inputVisible && activeFocus)
                                cursorDelegate: Rectangle { width: 1; color: theme.primary; visible: editor.activeFocus && !box.touchpad.strokeCode.length }
                                Keys.onPressed: function(event) {
                                    if (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) return
                                    const code = event.text.toLowerCase()
                                    if (["h", "s", "p", "n", "z"].indexOf(code) >= 0) {
                                        box.touchpad.addStroke(code); event.accepted = true
                                    } else if (box.touchpad.strokeCode.length || box.touchpad.predictionMode) {
                                        if (event.key >= Qt.Key_1 && event.key < Qt.Key_1 + box.touchpad.candidatePageSize) {
                                            box.touchpad.selectCandidate(event.key - Qt.Key_1); event.accepted = true
                                        } else if (event.key === Qt.Key_Space || event.key === Qt.Key_Return) {
                                            box.touchpad.selectCandidate(box.touchpad.selectedCandidate); event.accepted = true
                                        } else if (box.touchpad.strokeCode.length && event.key === Qt.Key_Backspace) {
                                            box.touchpad.undoStroke(); event.accepted = true
                                        } else if (event.key === Qt.Key_Escape) {
                                            box.touchpad.clearStrokes(); event.accepted = true
                                        } else if (event.key === Qt.Key_Minus) {
                                            box.touchpad.previousCandidates(); event.accepted = true
                                        } else if (event.key === Qt.Key_Plus || event.key === Qt.Key_Equal) {
                                            box.touchpad.nextCandidates(); event.accepted = true
                                        }
                                    }
                                }
                            }
                            Text {
                                objectName: "strokeInputHint"
                                anchors.left: parent.left; anchors.leftMargin: editor.leftPadding
                                anchors.right: parent.right; anchors.rightMargin: editor.rightPadding
                                anchors.verticalCenter: parent.verticalCenter
                                text: box.touchpad.trialActive ? "使用戒指书写并确认候选，已输入文字显示在这里"
                                    : box.touchpad.trialFinished ? "可继续自由书写、选字；重新开始实验后清空并计时"
                                    : "可直接自由书写、选字；开始实验后清空并计时"
                                font: editor.font; color: theme.placeholder; elide: Text.ElideRight
                                visible: !editor.text.length && !editor.preeditText.length && !box.touchpad.strokeCode.length
                            }
                            // Pending strokes sit at the cursor, between the text before and after it.
                            Item {
                                anchors.fill: parent; anchors.leftMargin: editor.leftPadding; anchors.rightMargin: editor.rightPadding
                                clip: true; visible: box.touchpad.strokeCode.length > 0
                                Row {
                                    x: editor.cursorRectangle.x - editor.leftPadding - prefix.implicitWidth
                                    anchors.verticalCenter: parent.verticalCenter
                                    Text { id: prefix; text: editor.text.slice(0, editor.cursorPosition); font: editor.font; color: box.ink }
                                    Row {
                                        id: composition; objectName: "strokeCodeLabel"
                                        property string text: box.touchpad.strokeCode.split("𠃍").join("乛")
                                        visible: text.length > 0; spacing: 8
                                        Accessible.name: text
                                        Repeater {
                                            model: box.touchpad.strokeTokens
                                            delegate: Item {
                                                required property var modelData
                                                required property int index
                                                width: glyph.implicitWidth + (modelData.fuzzy ? 5 : 0)
                                                height: glyph.implicitHeight
                                                Text { id: glyph; text: parent.modelData.symbol; font: editor.font; color: parent.modelData.correction ? "#DC2626" : parent.modelData.fuzzy ? "#B58A00" : theme.primary }
                                                Text {
                                                    objectName: "strokeFuzzyMark_" + parent.index
                                                    text: "?"; visible: parent.modelData.fuzzy
                                                    anchors.right: parent.right
                                                    anchors.bottom: parent.bottom; anchors.bottomMargin: -1
                                                    font.pixelSize: 9; color: parent.modelData.correction ? "#DC2626" : "#B58A00"
                                                }
                                            }
                                        }
                                    }
                                    Rectangle { visible: composition.visible; width: 1; height: box.fieldFont + 4; color: theme.primary; anchors.verticalCenter: parent.verticalCenter }
                                    Text { text: editor.text.slice(editor.cursorPosition); font: editor.font; color: box.ink }
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    objectName: "strokeTrialProgress"
                    Layout.fillWidth: true; Layout.preferredHeight: box.compact ? 3 : 5
                    radius: 2; color: "#E9EEF7"
                    Rectangle {
                        height: parent.height; radius: 2; color: "#345EE5"
                        width: parent.width * box.touchpad.trialProgress / 100
                    }
                }
                Muted {
                    objectName: "strokeTrialHint"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: box.touchpad.trialHint
                    wrapMode: box.compact ? Text.NoWrap : Text.Wrap
                    elide: Text.ElideRight; maximumLineCount: box.compact ? 1 : 3
                    HoverHandler { id: hintHover }
                    ToolTip.visible: hintHover.hovered && truncated
                    ToolTip.text: text
                }
            }
        }

        Card {
            id: selectionCard
            objectName: "strokeSelectionColumn"
            Layout.fillWidth: true
            implicitHeight: selection.implicitHeight + 2 * (box.compact ? 8 : 10)
            ColumnLayout {
                id: selection
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                          leftMargin: box.cardMargin; rightMargin: box.cardMargin }
                spacing: box.compact ? 4 : 6

                GridLayout {
                    objectName: "strokeSelectionHeader"
                    Layout.fillWidth: true
                    columns: box.narrow ? 1 : 2; columnSpacing: 8; rowSpacing: 4
                    RowLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
                        SectionTitle { text: "双指八向选字 · 笔画轨迹" }
                        Label {
                            id: selectionHintLabel
                            objectName: "strokeSelectionHint"
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            horizontalAlignment: box.narrow ? Text.AlignLeft : Text.AlignHCenter
                            text: box.selectionHint
                            color: box.touchpad.selectionState.phase === "selecting" ? "#234CDB" : box.mutedInk
                            font.pixelSize: 11; font.weight: box.touchpad.selectionState.phase === "selecting" ? Font.Bold : Font.Normal
                            elide: Text.ElideRight
                            HoverHandler { id: selectionHintHover }
                            ToolTip.visible: selectionHintHover.hovered && truncated
                            ToolTip.text: selectionHintLabel.text
                        }
                    }
                    RowLayout {
                        Layout.alignment: box.narrow ? Qt.AlignLeft : Qt.AlignRight; spacing: 6
                        FigmaAction {
                            objectName: "strokeTutorialOpen"
                            visible: !box.tutorialOpen
                            implicitHeight: 30; font.pixelSize: 12; focusPolicy: Qt.NoFocus; quiet: true
                            text: "基础教学"
                            onClicked: box.touchpad.strokeTutorialOpen = true
                        }
                        FigmaAction {
                            objectName: "strokePreviousPage"
                            implicitHeight: 30; font.pixelSize: 12; focusPolicy: Qt.NoFocus
                            text: "上一页"
                            enabled: box.touchpad.candidatePage > 0
                            onClicked: box.touchpad.previousCandidates()
                        }
                        Label {
                            objectName: "strokePageLabel"
                            Layout.minimumWidth: 88; horizontalAlignment: Text.AlignHCenter
                            text: box.touchpad.strokeCode.length && !box.touchpad.strokeCandidates.length ? "无常用字匹配"
                                : "第 " + (box.touchpad.candidatePage + 1) + " / " + box.touchpad.candidatePageCount + " 页"
                            font.pixelSize: 12; color: box.ink
                        }
                        FigmaAction {
                            objectName: "strokeNextPage"
                            implicitHeight: 30; font.pixelSize: 12; focusPolicy: Qt.NoFocus
                            text: "下一页"
                            // Wraps from the last page to the first.
                            enabled: box.touchpad.candidatePageCount > 1
                            onClicked: box.touchpad.nextCandidates()
                        }
                    }
                }

                GridLayout {
                    objectName: "strokeSelectionBody"
                    // Center the pair, or stack the tutorial above a centered menu.
                    Layout.fillWidth: !box.tutorialBeside
                    Layout.alignment: Qt.AlignHCenter
                    columns: box.tutorialBeside ? 2 : 1
                    columnSpacing: box.bodyGap; rowSpacing: box.bodyGap
                    StrokeTutorial {
                        id: tutorial
                        visible: box.tutorialOpen
                        Layout.alignment: Qt.AlignTop
                        Layout.preferredWidth: box.tutorialBeside ? box.tutorialWidth : -1
                        Layout.fillWidth: !box.tutorialBeside; Layout.minimumWidth: 0
                        onCloseRequested: box.touchpad.strokeTutorialOpen = false
                    }
                    Item {
                        Layout.alignment: Qt.AlignHCenter | Qt.AlignTop
                        Layout.preferredWidth: box.menuSide + selectionMenu.clearPullGutter
                        Layout.preferredHeight: box.menuSide
                        StrokeSelectionMenu {
                            id: selectionMenu
                            objectName: "strokeSelectionMenu"
                            touchpad: box.touchpad
                            x: clearPullGutter; width: box.menuSide; height: box.menuSide
                        }
                    }
                }

                Rectangle {
                    objectName: "strokeSelectionProgress"
                    Layout.fillWidth: true; Layout.preferredHeight: 5
                    radius: 2; color: "#E9EEF7"
                    Rectangle {
                        height: parent.height; radius: 2; color: "#345EE5"
                        width: parent.width * Math.max(0, Math.min(100, box.touchpad.selectionState.progress || 0)) / 100
                    }
                }

                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Rectangle {
                        objectName: "strokeCompositionChip"
                        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: 36
                        radius: 6; color: "#F3F6FC"; clip: true
                        Row {
                            x: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                            visible: box.touchpad.strokeCode.length > 0
                            Repeater {
                                model: box.touchpad.strokeTokens
                                delegate: Text {
                                    required property var modelData
                                    text: modelData.symbol + (modelData.fuzzy ? "?" : "")
                                    font.pixelSize: 17
                                    color: modelData.correction ? "#DC2626" : box.ink
                                }
                            }
                        }
                        Text {
                            x: 8; anchors.verticalCenter: parent.verticalCenter
                            visible: !box.touchpad.strokeCode.length
                            text: box.touchpad.predictionMode ? "联想候选" : "等待落笔"
                            font.pixelSize: 17; color: box.ink
                        }
                    }
                    Loader { active: box.width >= 760; visible: active; sourceComponent: legendComponent; Layout.alignment: Qt.AlignVCenter }
                    FigmaAction { text: "退一笔"; implicitHeight: 36; focusPolicy: Qt.NoFocus; enabled: box.touchpad.strokeCode.length > 0; Accessible.name: "退一笔"; onClicked: box.touchpad.undoStroke() }
                    FigmaAction { text: "清空笔画"; implicitHeight: 36; focusPolicy: Qt.NoFocus; enabled: box.touchpad.strokeCode.length > 0; Accessible.name: "清空笔画"; onClicked: box.touchpad.clearStrokes() }
                }
                Loader {
                    active: box.width < 760; visible: active; sourceComponent: legendComponent
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                }
                Label {
                    objectName: "strokeCommitMessage"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: box.touchpad.state === "error" ? box.touchpad.message : box.touchpad.commitMessage
                    visible: text.length > 0
                    font.pixelSize: 11
                    color: box.touchpad.state === "error" || text.indexOf("失败") >= 0 ? "#B54756" : box.mutedInk
                    wrapMode: Text.Wrap
                }
            }
        }

        Muted {
            objectName: "strokeTrialHelp"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            text: "双指左滑退格，继续外拉清空笔画 · 三段往返也可清空 · " + box.touchpad.strokeGestureLabel + "退出"
            wrapMode: Text.Wrap
        }

        // The app's own writing settings; the trial's statistics and debug sidebar are not part of the app.
        Card {
            Layout.fillWidth: true
            implicitHeight: tuning.implicitHeight + 24
            ColumnLayout {
                id: tuning
                anchors { left: parent.left; right: parent.right; top: parent.top
                          leftMargin: box.cardMargin; rightMargin: box.cardMargin; topMargin: 12 }
                spacing: 10
                SectionTitle { text: "书写设置" }
                Flow {
                    Layout.fillWidth: true; spacing: 10
                    Row {
                        spacing: 8
                        FigmaSwitch {
                            objectName: "strokePersonalLearningSwitch"
                            anchors.verticalCenter: parent.verticalCenter
                            checked: box.touchpad.personalLearning
                            enabled: !box.touchpad.strokeCode.length && !box.touchpad.strokeCommitPending
                            focusPolicy: Qt.NoFocus
                            Accessible.name: "学习个人写法"
                            onToggled: box.touchpad.personalLearning = checked
                        }
                        Label { text: "学习个人写法"; color: box.mutedInk; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                    }
                    UiTextField {
                        objectName: "strokeWritingProfile"
                        width: 120; maximumLength: 24
                        text: box.touchpad.writingProfile
                        placeholderText: "个人代号"; Accessible.name: "书写档案"
                        enabled: !box.touchpad.strokeCode.length && !box.touchpad.strokeCommitPending
                        onEditingFinished: box.touchpad.setWritingProfile(text)
                    }
                    FigmaAction {
                        text: "撤回学习"; quiet: true; focusPolicy: Qt.NoFocus
                        enabled: !box.touchpad.strokeCode.length && !box.touchpad.strokeCommitPending
                        onClicked: box.touchpad.undoPersonalLearning()
                    }
                    FigmaAction {
                        text: "清空档案"; quiet: true; focusPolicy: Qt.NoFocus
                        enabled: !box.touchpad.strokeCode.length && !box.touchpad.strokeCommitPending
                        onClicked: box.touchpad.clearPersonalLearning()
                    }
                }
                Muted {
                    objectName: "strokePersonalLearningStatus"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: box.touchpad.personalLearningStatus; wrapMode: Text.Wrap
                }
                Flow {
                    objectName: "strokeTools"
                    Layout.fillWidth: true; spacing: 8
                    Label { text: "手动笔画 · 仅用于调试"; color: box.mutedInk; font.pixelSize: 12; height: 40; verticalAlignment: Text.AlignVCenter }
                    Repeater {
                        model: [{symbol:"一",code:"h"}, {symbol:"丨",code:"s"}, {symbol:"丿",code:"p"}, {symbol:"丶",code:"n"}, {symbol:"乛",code:"z"}]
                        delegate: FigmaAction {
                            required property var modelData
                            objectName: "strokeButton_" + modelData.code
                            text: modelData.symbol; font.pixelSize: 20
                            width: 40; height: 40; focusPolicy: Qt.NoFocus
                            leftPadding: 0; rightPadding: 0
                            quiet: true
                            Accessible.name: "输入笔画 " + modelData.symbol
                            onClicked: box.touchpad.addStroke(modelData.code)
                        }
                    }
                    CheckBox {
                        objectName: "strokeFuzzyToggle"
                        text: "模糊匹配"; checked: box.touchpad.fuzzyMatching
                        enabled: !box.touchpad.strokeCommitPending
                        focusPolicy: Qt.NoFocus; font.pixelSize: 12
                        onToggled: { box.touchpad.fuzzyMatching = checked; editor.forceActiveFocus() }
                    }
                }
                GridLayout {
                    objectName: "strokeFuzzyStrengthRow"
                    Layout.fillWidth: true; columns: box.narrow ? 4 : 6; columnSpacing: 8; rowSpacing: 8
                    enabled: box.touchpad.fuzzyMatching && !box.touchpad.strokeCommitPending
                    Label { text: "模糊程度"; font.pixelSize: 12; color: box.mutedInk }
                    Label { text: "低"; font.pixelSize: 12; color: box.mutedInk }
                    Slider {
                        objectName: "strokeFuzzyStrengthSlider"
                        Layout.fillWidth: true; Layout.minimumWidth: 100; Layout.maximumWidth: 420
                        from: 0; to: 100; stepSize: 1
                        value: box.touchpad.fuzzyStrength
                        focusPolicy: Qt.NoFocus
                        Accessible.name: "模糊程度"
                        onMoved: box.touchpad.fuzzyStrength = Math.round(value)
                        onPressedChanged: if (!pressed) editor.forceActiveFocus()
                    }
                    Label { text: "高"; font.pixelSize: 12; color: box.mutedInk }
                    Label {
                        objectName: "strokeFuzzyStrengthValue"
                        text: box.touchpad.fuzzyStrength + (box.touchpad.fuzzyStrength === 50 ? " · 标准" : "")
                        font.pixelSize: 12; color: box.ink
                    }
                    FigmaAction {
                        objectName: "strokeFuzzyStrengthReset"
                        text: "恢复默认"; quiet: true; focusPolicy: Qt.NoFocus
                        onClicked: { box.touchpad.fuzzyStrength = 50; editor.forceActiveFocus() }
                    }
                }
                Muted {
                    objectName: "strokeAdaptiveFuzzyStatus"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: box.touchpad.adaptiveFuzzyStatus
                    wrapMode: Text.Wrap
                }
            }
        }
    }
    Connections {
        target: box.touchpad
        function onLocalBackspaceRequested() {
            if (!box.inputVisible) return
            let start = editor.selectionStart
            let end = editor.selectionEnd
            if (start === end) {
                end = editor.cursorPosition
                if (end === 0) return
                start = end - 1
                // TextField offsets use UTF-16; preserve supplementary characters.
                const low = editor.text.charCodeAt(start)
                if (start > 0 && low >= 0xDC00 && low <= 0xDFFF) {
                    const high = editor.text.charCodeAt(start - 1)
                    if (high >= 0xD800 && high <= 0xDBFF) start--
                }
            }
            editor.remove(start, end)
            editor.cursorPosition = start
            box.updateContext()
        }
        function onLocalCharacterCommitted(character) {
            const start = editor.selectionStart
            editor.remove(start, editor.selectionEnd)
            editor.insert(start, character)
            editor.cursorPosition = start + character.length
            box.updateContext()
        }
        function onLocalEditorClearRequested() {
            editor.clear()
            box.updateContext()
        }
        function onStrokeTraceChanged(points, finished) {
            if (box.inputVisible) selectionMenu.updateTrace(points, finished)
        }
    }
}
