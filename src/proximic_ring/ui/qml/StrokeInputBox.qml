// §2.1/§3.3: editor and glyphs first; settings wrap; square glyphs retain aspect ratio.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: box
    objectName: "strokeCompositionPanel"
    required property var controller
    readonly property var touchpad: controller.touchpad
    readonly property var candidateVisuals: touchpad.strokeCandidateVisuals
    readonly property var selectedVisual: candidateVisuals.length > touchpad.selectedCandidate
                                                  ? candidateVisuals[touchpad.selectedCandidate] : null
    property bool inputVisible: false
    property bool compactPresentation: false
    readonly property bool ringWriting: touchpad.active && touchpad.inputMode === "stroke"
    // Same wording as the standalone trial's selection hint.
    readonly property string selectionHint: {
        const state = touchpad.selectionState
        if (ringWriting && !editor.activeFocus) return "输入框未聚焦 · 戒指输入已暂停，点入输入框继续"
        if (state.phase === "selecting") return "选字模式 · 抬手确认"
        if (state.phase === "holding") return "保持不动 · " + Math.round(state.progress || 0) + "%"
        if (state.phase === "writing") return "书写中 · 抬手结束"
        return "停留 0.333 秒进入选字"
    }
    readonly property bool narrow: width < 600
    readonly property bool wide: width >= 960
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

    GridLayout {
        id: workspace
        columns: box.wide ? 2 : 1
        columnSpacing: 32; rowSpacing: 16
        width: Math.max(0, Math.min(1120, box.width - (box.narrow ? 0 : 32)))
        anchors.horizontalCenter: parent.horizontalCenter
        y: 12

        RowLayout {
            objectName: "strokeStatusRow"
            Layout.columnSpan: workspace.columns
            Layout.fillWidth: true; Layout.preferredHeight: 36; spacing: 10
            Rectangle { Layout.preferredWidth: 7; Layout.preferredHeight: 7; Layout.alignment: Qt.AlignVCenter; radius: 4; color: box.touchpad.active ? theme.success : theme.placeholder }
            Label {
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.alignment: Qt.AlignVCenter
                text: box.touchpad.strokePreparing ? "正在准备笔画输入…"
                    : box.touchpad.active && box.touchpad.inputMode === "stroke" ? "笔画输入中 · 双击回到鼠标"
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

        Rectangle {
            id: searchShell
            objectName: "strokeSearchShell"
            Layout.columnSpan: workspace.columns
            Layout.fillWidth: true; height: 80; radius: 12
            color: theme.surface
            border.color: editor.activeFocus ? "#B6C2EE" : theme.line
            border.width: 1
            UiIcon {
                anchors.left: parent.left; anchors.leftMargin: 22
                anchors.verticalCenter: parent.verticalCenter
                width: 20; height: 20; symbol: "edit"; ink: theme.muted
            }
            TextField {
                id: editor
                objectName: "strokeTestEditor"
                anchors.fill: parent
                leftPadding: 56; rightPadding: 24
                topPadding: 0; bottomPadding: 0
                font.family: theme.family; font.pixelSize: 22
                color: box.touchpad.strokeCode.length ? "transparent" : theme.text; selectionColor: theme.selection; selectedTextColor: theme.text
                placeholderText: ""
                placeholderTextColor: theme.placeholder
                selectByMouse: true
                background: Item {}
                onTextChanged: box.updateContext()
                onCursorPositionChanged: box.updateContext()
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
                anchors.left: parent.left; anchors.leftMargin: 56
                anchors.verticalCenter: parent.verticalCenter
                text: "写一笔，输入一个字"; font: editor.font; color: theme.muted
                visible: !editor.text.length && !editor.preeditText.length && !box.touchpad.strokeCode.length
            }
            Item {
                anchors.fill: parent; anchors.leftMargin: editor.leftPadding; anchors.rightMargin: editor.rightPadding
                clip: true; visible: box.touchpad.strokeCode.length > 0
                Row {
                    x: editor.cursorRectangle.x - editor.leftPadding - prefix.implicitWidth
                    anchors.verticalCenter: parent.verticalCenter
                    Text { id: prefix; text: editor.text.slice(0, editor.cursorPosition); font: editor.font; color: theme.text }
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
                    Rectangle { visible: composition.visible; width: 1; height: 25; color: theme.primary; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: editor.text.slice(editor.cursorPosition); font: editor.font; color: theme.text }
                }
            }
        }

        ColumnLayout {
            objectName: "strokeSelectionColumn"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            Layout.alignment: Qt.AlignTop
            spacing: 10
            readonly property real menuSide: Math.min(width, 540)

            RowLayout {
                objectName: "strokeSelectionHeader"
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: parent.menuSide; Layout.maximumWidth: parent.menuSide; spacing: 8
                Label { text: "八向选字"; color: theme.text; font.pixelSize: 16; font.weight: Font.Medium }
                Label {
                    objectName: "strokeSelectionHint"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: box.selectionHint
                    color: box.touchpad.selectionState.phase === "selecting" ? "#234CDB" : theme.muted
                    font.pixelSize: 12; font.weight: box.touchpad.selectionState.phase === "selecting" ? Font.Bold : Font.Normal
                    elide: Text.ElideRight
                }
                ToolButton {
                    objectName: "strokePreviousPage"
                    enabled: box.touchpad.candidatePage > 0
                    text: "‹"; font.pixelSize: 22; focusPolicy: Qt.NoFocus
                    Accessible.name: "上一页"
                    background: Rectangle { radius: 8; color: parent.hovered && parent.enabled ? theme.subtle : "transparent" }
                    onClicked: box.touchpad.previousCandidates()
                }
                Label {
                    objectName: "strokePageLabel"
                    Layout.minimumWidth: 72; horizontalAlignment: Text.AlignHCenter
                    text: box.touchpad.strokeCode.length && !box.touchpad.strokeCandidates.length ? "没有匹配的字"
                        : "第 " + (box.touchpad.candidatePage + 1) + " / " + box.touchpad.candidatePageCount + " 页"
                    font.pixelSize: 12; color: theme.muted
                }
                ToolButton {
                    objectName: "strokeNextPage"
                    enabled: box.touchpad.moreCandidates
                    text: "›"; font.pixelSize: 22; focusPolicy: Qt.NoFocus
                    Accessible.name: "下一页"
                    background: Rectangle { radius: 8; color: parent.hovered && parent.enabled ? theme.subtle : "transparent" }
                    onClicked: box.touchpad.nextCandidates()
                }
            }

            StrokeSelectionMenu {
                id: selectionMenu
                objectName: "strokeSelectionMenu"
                touchpad: box.touchpad
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: parent.menuSide; Layout.preferredHeight: parent.menuSide
            }

            Rectangle {
                objectName: "strokeSelectionProgress"
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: parent.menuSide; Layout.preferredHeight: 5
                radius: 2; color: "#E9EEF7"
                Rectangle {
                    height: parent.height; radius: 2; color: "#345EE5"
                    width: parent.width * Math.max(0, Math.min(100, box.touchpad.selectionState.progress || 0)) / 100
                }
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: parent.menuSide; Layout.maximumWidth: parent.menuSide; spacing: 8
                Rectangle {
                    objectName: "strokeCompositionChip"
                    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: 36
                    radius: 6; color: "#F3F6FC"; clip: true
                    Row {
                        x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                        visible: box.touchpad.strokeCode.length > 0
                        Repeater {
                            model: box.touchpad.strokeTokens
                            delegate: Text {
                                required property var modelData
                                text: modelData.symbol + (modelData.fuzzy ? "?" : "")
                                font.pixelSize: 17
                                color: modelData.correction ? "#DC2626" : modelData.fuzzy ? "#B58A00" : theme.text
                            }
                        }
                    }
                    Text {
                        x: 10; anchors.verticalCenter: parent.verticalCenter
                        visible: !box.touchpad.strokeCode.length
                        text: box.touchpad.predictionMode ? "联想候选" : "等待落笔"
                        font.pixelSize: 13; color: theme.muted
                    }
                }
                FigmaAction { text: "退一笔"; implicitHeight: 36; focusPolicy: Qt.NoFocus; enabled: box.touchpad.strokeCode.length > 0; Accessible.name: "退一笔"; onClicked: box.touchpad.undoStroke() }
                FigmaAction { text: "清空笔画"; implicitHeight: 36; focusPolicy: Qt.NoFocus; enabled: box.touchpad.strokeCode.length > 0; Accessible.name: "清空笔画"; onClicked: box.touchpad.clearStrokes() }
            }

            Flow {
                objectName: "strokeGlyphLegend"
                Layout.alignment: Qt.AlignHCenter; spacing: 14
                Repeater {
                    model: [{label: "已输入", ink: "#171C28", hollow: false},
                            {label: "未输入", ink: "#98A1B2", hollow: true},
                            {label: "模糊笔画", ink: "#EAB308", hollow: false},
                            {label: "疑似错笔", ink: "#DC2626", hollow: false},
                            {label: "下一笔", ink: "#612563EB", hollow: false}]
                    delegate: Row {
                        required property var modelData
                        spacing: 5
                        Rectangle {
                            width: 9; height: 9; radius: 2; anchors.verticalCenter: parent.verticalCenter
                            color: modelData.hollow ? "transparent" : modelData.ink
                            border.color: modelData.ink; border.width: modelData.hollow ? 1 : 0
                        }
                        Text { text: modelData.label; font.pixelSize: 11; color: theme.muted }
                    }
                }
            }
            Label {
                objectName: "strokeCandidateProgress"
                Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
                text: box.touchpad.strokeCode.length && !box.touchpad.strokeCandidates.length ? "没有匹配的字，退一笔试试"
                    : !box.selectedVisual ? "候选字会按笔顺显示输入进度"
                    : box.touchpad.predictionMode ? "联想候选 · 停留后滑向目标选字，或继续写下一个字"
                    : "「" + box.selectedVisual.character + "」" + box.selectedVisual.status
                      + (box.selectedVisual.next ? " · 下一笔 " + box.selectedVisual.next : "")
                font.pixelSize: 12; color: theme.muted; elide: Text.ElideRight
            }
        }
        ColumnLayout {
            id: tuning
            Layout.fillWidth: true; Layout.minimumWidth: 0
            Layout.preferredWidth: box.wide ? 360 : -1
            Layout.maximumWidth: box.wide ? 400 : Number.POSITIVE_INFINITY
            Layout.alignment: Qt.AlignTop
            spacing: 16
            Label { text: "书写设置"; color: theme.text; font.pixelSize: 16; font.weight: Font.Medium }
        GridLayout {
            Layout.fillWidth: true; columns: 2; columnSpacing: 8; rowSpacing: 8
            RowLayout {
                spacing: 8
                FigmaSwitch {
                    objectName: "strokePersonalLearningSwitch"
                    checked: box.touchpad.personalLearning
                    enabled: !box.touchpad.strokeCode.length && !box.touchpad.strokeCommitPending
                    focusPolicy: Qt.NoFocus
                    Accessible.name: "学习个人写法"
                    onToggled: box.touchpad.personalLearning = checked
                }
                Label { text: "学习个人写法"; color: theme.muted; font.pixelSize: 12 }
            }
            UiTextField {
                objectName: "strokeWritingProfile"
                Layout.preferredWidth: 100; Layout.maximumWidth: 140; Layout.fillWidth: true; maximumLength: 24
                text: box.touchpad.writingProfile
                placeholderText: "个人代号"; Accessible.name: "书写档案"
                enabled: !box.touchpad.strokeCode.length && !box.touchpad.strokeCommitPending
                onEditingFinished: box.touchpad.setWritingProfile(text)
            }
            Label {
                objectName: "strokePersonalLearningStatus"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                text: box.touchpad.personalLearningStatus; wrapMode: Text.Wrap
                font.pixelSize: 11; color: theme.muted
            }
            FigmaAction {
                text: "撤回学习"; quiet: true; focusPolicy: Qt.NoFocus
                enabled: !box.touchpad.strokeCode.length && !box.touchpad.strokeCommitPending
                onClicked: box.touchpad.undoPersonalLearning()
            }
            FigmaAction {
                text: "清空档案"; quiet: true; Layout.columnSpan: 1; focusPolicy: Qt.NoFocus
                enabled: !box.touchpad.strokeCode.length && !box.touchpad.strokeCommitPending
                onClicked: box.touchpad.clearPersonalLearning()
            }
        }

        Label { text: "手动笔画 · 仅用于调试"; color: theme.muted; font.pixelSize: 12 }
        Flow {
            objectName: "strokeTools"
            Layout.fillWidth: true; spacing: 8
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
            Layout.fillWidth: true; columns: box.wide || box.narrow ? 4 : 7; columnSpacing: 8; rowSpacing: 8
            enabled: box.touchpad.fuzzyMatching && !box.touchpad.strokeCommitPending
            Label { text: "模糊程度"; font.pixelSize: 12; color: theme.muted }
            Label { text: "低"; font.pixelSize: 12; color: theme.muted }
            Slider {
                objectName: "strokeFuzzyStrengthSlider"
                Layout.fillWidth: true; Layout.minimumWidth: 100
                from: 0; to: 100; stepSize: 1
                value: box.touchpad.fuzzyStrength
                focusPolicy: Qt.NoFocus
                Accessible.name: "模糊程度"
                onMoved: box.touchpad.fuzzyStrength = Math.round(value)
                onPressedChanged: if (!pressed) editor.forceActiveFocus()
            }
            Label { text: "高"; font.pixelSize: 12; color: theme.muted }
            Label {
                objectName: "strokeFuzzyStrengthValue"
                text: box.touchpad.fuzzyStrength + (box.touchpad.fuzzyStrength === 50 ? " · 标准" : "")
                font.pixelSize: 12; color: theme.text
            }
            FigmaAction {
                objectName: "strokeFuzzyStrengthReset"
                text: "恢复默认"; quiet: true; focusPolicy: Qt.NoFocus
                onClicked: { box.touchpad.fuzzyStrength = 50; editor.forceActiveFocus() }
            }
        }
        Label {
            objectName: "strokeAdaptiveFuzzyStatus"
            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
            text: box.touchpad.adaptiveFuzzyStatus
            font.pixelSize: 12; color: theme.muted; wrapMode: Text.Wrap
        }
        Label {
            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
            text: "选字：静止 0.333 秒（书写后也可停顿）→ 向目标滑动 → 抬手确认 · 左→右退笔／删字 · 三段往返清空笔画"
            wrapMode: Text.Wrap
            font.pixelSize: 12; color: theme.muted
        }
        Label {
            objectName: "strokeCommitMessage"
            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
            text: box.touchpad.state === "error" ? box.touchpad.message : box.touchpad.commitMessage
            visible: text.length > 0
            font.pixelSize: 12
            color: box.touchpad.state === "error" || text.indexOf("失败") >= 0 ? "#B54756" : theme.muted
            wrapMode: Text.Wrap
        }
        Label {
            objectName: "strokeTrialHelp"
            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: "点击「开始试写」后用 Ring 写笔画，停留后滑向候选并抬手确认。也可点入输入框后双击进入笔画，再双击回到鼠标。"
            font.pixelSize: 12; color: theme.muted
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
        function onStrokeTraceChanged(points, finished) {
            if (box.inputVisible) selectionMenu.updateTrace(points, finished)
        }
    }
}
