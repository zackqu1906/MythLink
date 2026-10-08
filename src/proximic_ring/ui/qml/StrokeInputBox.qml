import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: box
    objectName: "strokeCompositionPanel"
    required property var controller
    readonly property var touchpad: controller.touchpad
    property bool inputVisible: false
    signal homeRequested()
    UiTheme { id: theme }

    function updateContext() {
        if (inputVisible) touchpad.setStrokeContext(editor.text.slice(0, editor.cursorPosition))
    }
    function syncInputTarget() {
        Qt.callLater(function() {
            if (box.touchpad) { box.touchpad.setLocalStrokeInput(box.inputVisible); box.updateContext() }
        })
    }
    onInputVisibleChanged: { syncInputTarget(); if (!inputVisible) trail.updateTrace([], false) }
    Component.onCompleted: syncInputTarget()

    ColumnLayout {
        id: workspace
        width: Math.min(780, box.width - 32)
        anchors.horizontalCenter: parent.horizontalCenter
        y: Math.max(12, Math.min(70, (box.height - implicitHeight) / 3))
        spacing: 16

        RowLayout {
            objectName: "strokeStatusRow"
            Layout.fillWidth: true; Layout.preferredHeight: 36; spacing: 10
            Rectangle { Layout.preferredWidth: 7; Layout.preferredHeight: 7; Layout.alignment: Qt.AlignVCenter; radius: 4; color: box.touchpad.active ? theme.success : theme.placeholder }
            Label {
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.alignment: Qt.AlignVCenter
                text: box.touchpad.active ? "Ring 已开启 · " + box.touchpad.remaining + " 秒" : box.controller.connected ? "Ring 已就绪" : "可键入笔画，或连接 Ring"
                font.pixelSize: 12; color: theme.muted
                elide: Text.ElideRight
            }
            UiAction {
                objectName: "strokeRingToggle"
                visible: box.controller.connected
                enabled: box.touchpad.active || box.touchpad.available
                text: box.touchpad.busy ? "准备中…" : box.touchpad.active ? "停止" : "开启 Ring"
                Layout.preferredWidth: 108; Layout.preferredHeight: 34; Layout.alignment: Qt.AlignVCenter
                focusPolicy: Qt.NoFocus
                onClicked: {
                    const starting = !box.touchpad.active
                    box.touchpad.toggle()
                    if (starting) editor.forceActiveFocus()
                }
            }
            UiAction {
                visible: !box.controller.connected
                text: "连接 Ring"; Layout.preferredHeight: 34; Layout.alignment: Qt.AlignVCenter; focusPolicy: Qt.NoFocus
                onClicked: box.homeRequested()
            }
        }

        Rectangle {
            id: searchShell
            objectName: "strokeSearchShell"
            Layout.fillWidth: true; height: 64; radius: 32
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
                cursorDelegate: Rectangle { width: 1; color: theme.primary; visible: editor.activeFocus && !box.touchpad.strokeCode.length }
                Keys.onPressed: function(event) {
                    if (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) return
                    const code = event.text.toLowerCase()
                    if (["h", "s", "p", "n", "z"].indexOf(code) >= 0) {
                        box.touchpad.addStroke(code); event.accepted = true
                    } else if (box.touchpad.strokeCode.length || box.touchpad.predictionMode) {
                        if (event.key >= Qt.Key_1 && event.key <= Qt.Key_5) {
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
                text: "写一笔，输入一个字"; font: editor.font; color: theme.placeholder
                visible: !editor.text.length && !editor.preeditText.length && !box.touchpad.strokeCode.length
            }
            Item {
                anchors.fill: parent; anchors.leftMargin: editor.leftPadding; anchors.rightMargin: editor.rightPadding
                clip: true; visible: box.touchpad.strokeCode.length > 0
                Row {
                    x: editor.cursorRectangle.x - editor.leftPadding - prefix.implicitWidth
                    anchors.verticalCenter: parent.verticalCenter
                    Text { id: prefix; text: editor.text.slice(0, editor.cursorPosition); font: editor.font; color: theme.text }
                    Text {
                        id: composition; objectName: "strokeCodeLabel"
                        visible: text.length > 0; text: box.touchpad.strokeCode.split("𠃍").join("乛")
                        font: editor.font; color: theme.primary
                    }
                    Rectangle { visible: composition.visible; width: 1; height: 25; color: theme.primary; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: editor.text.slice(editor.cursorPosition); font: editor.font; color: theme.text }
                }
            }
        }

        RowLayout {
            id: candidates
            objectName: "strokeCandidateRow"
            Layout.fillWidth: true; Layout.preferredHeight: 56
            spacing: 4
            Label { visible: box.touchpad.predictionMode; text: "联想"; font.pixelSize: 11; color: theme.placeholder }
            Repeater {
                model: box.touchpad.strokeCandidates
                delegate: Button {
                    required property int index
                    required property string modelData
                    objectName: "strokeCandidate" + index
                    Layout.fillWidth: true; Layout.preferredHeight: 52
                    focusPolicy: Qt.NoFocus
                    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
                    Accessible.name: "候选 " + (index + 1) + " " + modelData
                    background: Rectangle {
                        radius: 12
                        color: index === box.touchpad.selectedCandidate ? theme.selection : parent.hovered ? theme.subtle : "transparent"
                    }
                    contentItem: Text {
                        text: (index + 1) + "  " + modelData
                        font.family: theme.family; font.pixelSize: 22
                        color: index === box.touchpad.selectedCandidate ? theme.primary : theme.text
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: box.touchpad.selectCandidate(index)
                }
            }
            Label {
                Layout.fillWidth: true
                visible: !box.touchpad.strokeCandidates.length
                text: box.touchpad.strokeCode.length ? "没有匹配的字，退一笔试试" : "候选字会显示在这里"
                horizontalAlignment: Text.AlignHCenter
                font.pixelSize: 13; color: theme.placeholder
            }
            ToolButton {
                objectName: "strokePreviousPage"
                visible: box.touchpad.candidatePage > 0
                text: "‹"; font.pixelSize: 24; focusPolicy: Qt.NoFocus
                Accessible.name: "上一页"
                background: Rectangle { radius: 8; color: parent.hovered ? theme.subtle : "transparent" }
                onClicked: box.touchpad.previousCandidates()
            }
            ToolButton {
                objectName: "strokeNextPage"
                visible: box.touchpad.moreCandidates
                text: "›"; font.pixelSize: 24; focusPolicy: Qt.NoFocus
                Accessible.name: "下一页"
                background: Rectangle { radius: 8; color: parent.hovered ? theme.subtle : "transparent" }
                onClicked: box.touchpad.nextCandidates()
            }
        }

        StrokeTrail {
            id: trail
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Layout.preferredHeight
            Layout.preferredHeight: Math.max(54, Math.min(140, box.height - 320))
        }
        RowLayout {
            objectName: "strokeTools"
            Layout.alignment: Qt.AlignHCenter; spacing: 6
            Repeater {
                model: [{symbol:"一",code:"h"}, {symbol:"丨",code:"s"}, {symbol:"丿",code:"p"}, {symbol:"丶",code:"n"}, {symbol:"乛",code:"z"}]
                delegate: UiAction {
                    required property var modelData
                    objectName: "strokeButton_" + modelData.code
                    text: modelData.symbol; font.pixelSize: 20
                    Layout.preferredWidth: 40; Layout.preferredHeight: 40; focusPolicy: Qt.NoFocus
                    leftPadding: 0; rightPadding: 0
                    quiet: true
                    Accessible.name: "输入笔画 " + modelData.symbol
                    onClicked: box.touchpad.addStroke(modelData.code)
                }
            }
            UiAction { text: "退笔"; quiet: true; implicitHeight: 32; focusPolicy: Qt.NoFocus; enabled: box.touchpad.strokeCode.length > 0; Accessible.name: "退一笔"; onClicked: box.touchpad.undoStroke() }
            UiAction { text: "清空"; quiet: true; implicitHeight: 32; focusPolicy: Qt.NoFocus; enabled: box.touchpad.strokeCode.length > 0; Accessible.name: "清空笔画"; onClicked: box.touchpad.clearStrokes() }
        }
        Label {
            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
            text: "左右滑选字 · 轻触确认 · 上滑退笔/删字 · 下滑清空"
            font.pixelSize: 12; color: theme.muted
        }
        Label {
            objectName: "strokeCommitMessage"
            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
            text: box.touchpad.state === "error" ? box.touchpad.message : box.touchpad.commitMessage
            visible: box.touchpad.state === "error" || text.indexOf("失败") >= 0
            font.pixelSize: 12; color: "#B54756"; wrapMode: Text.Wrap
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
            if (box.inputVisible) trail.updateTrace(points, finished)
        }
    }
}
