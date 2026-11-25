// Keep the gesture text primary and use the small illustrations as examples.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: tutorial
    objectName: "strokeTutorial"
    signal closeRequested()
    property bool compact: false
    readonly property color ink: "#24334D"
    readonly property color mutedInk: theme.muted
    readonly property color accent: "#345EE5"
    readonly property color faint: "#C9D4EC"
    readonly property bool running: visible && Window.window !== null && Window.window.visible
    // 0..1 loop shared by the illustrations.
    property real phase: 0
    implicitWidth: 260
    readonly property int inset: compact ? 8 : 12
    implicitHeight: body.implicitHeight + 2 * inset
    radius: 12; color: "#F7F9FE"
    border.color: "#E0E6F0"; border.width: 1
    FigmaTheme { id: theme }

    NumberAnimation on phase {
        from: 0; to: 1; duration: 3200; loops: Animation.Infinite
        running: tutorial.running
    }

    component StepText: Label {
        Layout.fillWidth: true; Layout.minimumWidth: 0
        wrapMode: Text.Wrap
        font.family: theme.family; font.pixelSize: 12; lineHeight: 1.15
        color: "#24334D"; textFormat: Text.StyledText
    }
    component Figure: Rectangle {
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: tutorial.compact ? 58 : 76
        Layout.preferredHeight: tutorial.compact ? 46 : 60
        radius: 8; color: "#FFFFFF"
        border.color: "#E0E6F0"; border.width: 1
    }

    ColumnLayout {
        id: body
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: tutorial.inset }
        spacing: tutorial.compact ? 4 : 12

        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Label {
                Layout.fillWidth: true
                text: "基础教学"
                font.family: theme.family; font.pixelSize: 13; font.weight: Font.Bold; color: tutorial.ink
            }
            AbstractButton {
                objectName: "strokeTutorialClose"
                implicitWidth: 24; implicitHeight: 24
                focusPolicy: Qt.NoFocus; hoverEnabled: true
                Accessible.name: "关闭基础教学"
                ToolTip.visible: hovered; ToolTip.text: "关闭教学，可在标题栏重新打开"; ToolTip.delay: 500
                background: Rectangle { radius: 6; color: parent.down ? "#D9DFF9" : parent.hovered ? "#EBEFFA" : "transparent" }
                // Drawn, so it never falls back to a missing glyph.
                contentItem: Item {
                    Repeater {
                        model: [45, -45]
                        delegate: Rectangle {
                            required property var modelData
                            anchors.centerIn: parent
                            width: 11; height: 1.5; radius: 1
                            rotation: modelData; color: tutorial.mutedInk
                        }
                    }
                }
                onClicked: tutorial.closeRequested()
            }
        }

        // 1. Stroke by stroke: 十 written as 一 then 丨.
        RowLayout {
            objectName: "strokeTutorialWrite"
            Layout.fillWidth: true; spacing: 8
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                StepText { text: "<b>一笔一笔</b>写出笔画。" }
                Label {
                    text: "写「十」时，先横后竖。"
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font.family: theme.family; font.pixelSize: 11; color: tutorial.mutedInk
                }
            }
            Figure {
                Canvas {
                    id: writeCanvas
                    anchors.fill: parent; anchors.margins: 6
                    property real phase: tutorial.phase
                    onPhaseChanged: requestPaint()
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.reset()
                        const w = width, h = height, cx = w / 2, cy = h / 2, r = Math.min(w, h) * 0.38
                        // 0–0.35 horizontal, 0.4–0.75 vertical, then hold.
                        const a = Math.max(0, Math.min(1, phase / 0.35))
                        const b = Math.max(0, Math.min(1, (phase - 0.4) / 0.35))
                        ctx.lineCap = "round"; ctx.lineWidth = 4
                        ctx.strokeStyle = tutorial.faint
                        ctx.beginPath(); ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy)
                        ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r); ctx.stroke()
                        ctx.strokeStyle = phase > 0.8 ? tutorial.ink : tutorial.accent
                        ctx.beginPath(); ctx.moveTo(cx - r, cy); ctx.lineTo(cx - r + 2 * r * a, cy); ctx.stroke()
                        if (b > 0) { ctx.beginPath(); ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy - r + 2 * r * b); ctx.stroke() }
                    }
                }
            }
        }

        // 2. Single tap = first candidate; two-finger swipe in eight directions = the rest.
        RowLayout {
            objectName: "strokeTutorialSelect"
            Layout.fillWidth: true; spacing: 8
            StepText {
                text: "<b>单击动作</b>可在笔画输入页设置。<br><b>双指滑向目标格</b>，抬手确认。"
            }
            Figure {
                Grid {
                    id: miniGrid
                    anchors.centerIn: parent
                    columns: 3; spacing: 3
                    // Center (tap) first, then a tour of the eight directions.
                    readonly property var order: [4, 4, 5, 8, 7, 6, 3, 0, 1, 2]
                    readonly property int active: order[Math.min(order.length - 1, Math.floor(tutorial.phase * order.length))]
                    Repeater {
                        model: 9
                        delegate: Rectangle {
                            required property int index
                            width: tutorial.compact ? 12 : 15; height: width; radius: 3
                            color: index === miniGrid.active ? tutorial.accent
                                : index === 4 ? "#DCE4F7" : "#EEF2FA"
                            border.color: "#D3DCEE"; border.width: index === miniGrid.active ? 0 : 1
                            Text {
                                anchors.centerIn: parent
                                text: ["↖", "↑", "↗", "←", "1", "→", "↙", "↓", "↘"][parent.index]
                                font.pixelSize: 9
                                color: parent.index === miniGrid.active ? "white" : tutorial.mutedInk
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            objectName: "strokeTutorialPunctuation"
            Layout.fillWidth: true; spacing: 8
            StepText {
                text: "双指滑到<b>句号</b>再上滑展开。<br>滑选标点，抬手确认。<br>成对符号各占半格。<br>沿中列下拉填满<b>取消</b>返回。"
            }
            Figure {
                id: punctuationFigure
                readonly property bool expanded: tutorial.phase >= .48
                Column {
                    anchors.centerIn: parent
                    scale: tutorial.compact ? .8 : 1
                    spacing: 3
                    UiIcon {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: 14; height: 14
                        symbol: "chevron-left"; rotation: 90; ink: tutorial.accent
                    }
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        visible: !punctuationFigure.expanded
                        width: 22; height: 17; radius: 3; color: tutorial.accent
                        Text { anchors.centerIn: parent; text: "。"; color: "white"; font.pixelSize: 11 }
                    }
                    Grid {
                        anchors.horizontalCenter: parent.horizontalCenter
                        visible: punctuationFigure.expanded
                        columns: 3; spacing: 2
                        Repeater {
                            model: ["、", "“”", "；", "《》", "！", "（）", "…", "—", "："]
                            delegate: Rectangle {
                                required property string modelData
                                required property int index
                                width: 16; height: 11; radius: 2
                                color: index === 4 ? tutorial.accent : "#EEF2FA"
                                Text {
                                    anchors.centerIn: parent
                                    text: parent.modelData; font.pixelSize: 8
                                    color: parent.index === 4 ? "white" : tutorial.ink
                                }
                            }
                        }
                    }
                }
            }
        }

        // Continue the two-finger left swipe to the clear threshold.
        RowLayout {
            objectName: "strokeTutorialClear"
            Layout.fillWidth: true; spacing: 8
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                StepText { text: "<b>双指左滑</b>退一笔，继续向左外拉后抬手清空。" }
                Label {
                    text: "没有笔画时，短滑退格，长拉删除前文。"
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font.family: theme.family; font.pixelSize: 11; color: tutorial.mutedInk
                }
            }
            Figure {
                Canvas {
                    objectName: "strokeTutorialLeftPull"
                    anchors.fill: parent; anchors.margins: 6
                    property real phase: tutorial.phase
                    onPhaseChanged: requestPaint()
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.reset()
                        const start = width - 8, end = 8
                        const progress = Math.min(1, phase / 0.8)
                        const x = start + (end - start) * progress
                        ctx.lineCap = "round"
                        ctx.lineWidth = 2; ctx.strokeStyle = "#8EA4D4"
                        for (const y of [height * 0.35, height * 0.65]) {
                            ctx.strokeStyle = "#8EA4D4"
                            ctx.beginPath(); ctx.moveTo(start, y); ctx.lineTo(end, y)
                            ctx.moveTo(end + 5, y - 4); ctx.lineTo(end, y); ctx.lineTo(end + 5, y + 4)
                            ctx.stroke()
                            ctx.strokeStyle = tutorial.accent
                            ctx.beginPath(); ctx.moveTo(start, y); ctx.lineTo(x, y); ctx.stroke()
                            ctx.fillStyle = tutorial.accent
                            ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill()
                        }
                    }
                }
            }
        }
    }
}
