// Keep the gesture text primary and use the small illustrations as examples.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: tutorial
    objectName: "strokeTutorial"
    signal closeRequested()
    readonly property color ink: "#24334D"
    readonly property color mutedInk: theme.muted
    readonly property color accent: "#345EE5"
    readonly property color faint: "#C9D4EC"
    readonly property bool running: visible && Window.window !== null && Window.window.visible
    // 0..1 loop shared by the three illustrations.
    property real phase: 0
    implicitWidth: 260
    implicitHeight: body.implicitHeight + 24
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
        Layout.preferredWidth: 76; Layout.preferredHeight: 60
        radius: 8; color: "#FFFFFF"
        border.color: "#E0E6F0"; border.width: 1
    }

    ColumnLayout {
        id: body
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
        spacing: 12

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
                text: "<b>单指单击</b>选首选字。<br><b>双指八向滑动</b>，可选其他候选、标点或删除。"
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
                            width: 15; height: 15; radius: 3
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

        // 3. Show a four-pass swipe (→ ← → ←) to clear the pending strokes.
        RowLayout {
            objectName: "strokeTutorialClear"
            Layout.fillWidth: true; spacing: 8
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                StepText { text: "<b>单指来回滑动</b>，清空全部笔画。" }
                Label {
                    text: "沿箭头连续折返后抬手。"
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font.family: theme.family; font.pixelSize: 11; color: tutorial.mutedInk
                }
            }
            Figure {
                Canvas {
                    objectName: "strokeTutorialZigzag"
                    anchors.fill: parent; anchors.margins: 6
                    property real phase: tutorial.phase
                    onPhaseChanged: requestPaint()
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.reset()
                        const w = width, h = height
                        const pts = [[6, h * 0.12], [w - 6, h * 0.31], [6, h * 0.50],
                                     [w - 6, h * 0.69], [6, h * 0.88]]
                        ctx.lineCap = "round"; ctx.lineJoin = "round"
                        // Keep the whole path visible, including while the dot pauses at the end.
                        ctx.lineWidth = 2; ctx.strokeStyle = "#8EA4D4"
                        ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1])
                        for (let i = 1; i < pts.length; ++i) ctx.lineTo(pts[i][0], pts[i][1])
                        ctx.stroke()
                        const progress = Math.min(1, phase / 0.8) * (pts.length - 1)
                        ctx.lineWidth = 3; ctx.strokeStyle = tutorial.accent
                        ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1])
                        let head = pts[0]
                        for (let i = 1; i < pts.length && progress > i - 1; ++i) {
                            const f = Math.min(1, progress - (i - 1))
                            head = [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f,
                                    pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f]
                            ctx.lineTo(head[0], head[1])
                        }
                        ctx.stroke()
                        // One arrow per pass makes both reversals readable in a still image.
                        ctx.lineWidth = 1.5
                        for (let i = 1; i < pts.length; ++i) {
                            const start = pts[i - 1], end = pts[i]
                            const x = (start[0] + end[0]) / 2, y = (start[1] + end[1]) / 2
                            const angle = Math.atan2(end[1] - start[1], end[0] - start[0])
                            ctx.strokeStyle = tutorial.accent
                            ctx.beginPath()
                            ctx.moveTo(x - 6 * Math.cos(angle - 0.6), y - 6 * Math.sin(angle - 0.6))
                            ctx.lineTo(x, y)
                            ctx.lineTo(x - 6 * Math.cos(angle + 0.6), y - 6 * Math.sin(angle + 0.6))
                            ctx.stroke()
                        }
                        ctx.fillStyle = tutorial.accent
                        ctx.strokeStyle = "white"
                        ctx.beginPath(); ctx.arc(head[0], head[1], 4, 0, Math.PI * 2); ctx.fill()
                        ctx.stroke()
                    }
                }
            }
        }
    }
}
