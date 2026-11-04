// Same 3×3 menu as tools/test_complete_stroke_input.py: punctuation on top,
// six candidates below, handwriting and selection path drawn over the cells.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: menu
    required property var touchpad
    readonly property var candidateVisuals: touchpad.strokeCandidateVisuals
    readonly property var selection: touchpad.selectionState
    readonly property bool selecting: selection.phase === "selecting"
    readonly property string activeDirection: selecting ? (selection.direction || "center") : ""
    // Same order as stroke_menu.CANDIDATE_CELLS / PUNCTUATION_CELLS.
    readonly property var candidateCells: [
        {direction: "center", row: 1, column: 1, arrow: "首选"}, {direction: "left", row: 1, column: 0, arrow: "←"},
        {direction: "right", row: 1, column: 2, arrow: "→"}, {direction: "down-left", row: 2, column: 0, arrow: "↙"},
        {direction: "down", row: 2, column: 1, arrow: "↓"}, {direction: "down-right", row: 2, column: 2, arrow: "↘"}]
    readonly property var punctuationCells: [
        {direction: "up-left", arrow: "↖", symbol: "，", title: "逗号"},
        {direction: "up", arrow: "↑", symbol: "。", title: "句号"},
        {direction: "up-right", arrow: "↗", symbol: "？", title: "问号"}]
    property var cells: ({})
    color: "#F3F6FC"; radius: 14
    border.color: "#DCE4F0"; border.width: 1
    FigmaTheme { id: theme }

    function updateTrace(points, finished) { trail.updateTrace(points, finished) }

    GridLayout {
        id: grid
        objectName: "strokeCandidateRow"
        anchors.fill: parent; anchors.margins: 10
        columns: 3; rows: 3; rowSpacing: 8; columnSpacing: 8

        Repeater {
            model: menu.punctuationCells
            delegate: Button {
                id: action
                required property var modelData
                required property int index
                objectName: "strokePunctuation" + index
                readonly property bool directionActive: menu.activeDirection === modelData.direction
                Layout.row: 0; Layout.column: index
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.preferredWidth: 1; Layout.preferredHeight: 1
                focusPolicy: Qt.NoFocus; hoverEnabled: enabled
                topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0; padding: 0
                Accessible.name: "输入标点 " + modelData.symbol
                Component.onCompleted: menu.cells[modelData.direction] = action
                background: Rectangle {
                    radius: 10
                    color: action.directionActive ? "#234CDB" : action.down ? "#D8E3FA" : (action.enabled && action.hovered) ? "#E7EDFB" : "#EDF2FB"
                    border.width: action.directionActive ? 2 : 1
                    border.color: action.directionActive ? "#1739AC" : "#DCE4F0"
                }
                contentItem: Item {
                    readonly property color ink: action.directionActive ? "white" : "#647895"
                    readonly property bool large: action.height >= 115
                    Text { x: 12; y: 7; text: action.modelData.arrow; color: parent.ink; font.pixelSize: parent.large ? 14 : 12 }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: 25; height: Math.max(22, parent.height - 54)
                        verticalAlignment: Text.AlignVCenter
                        text: action.modelData.symbol; color: parent.ink
                        font.pixelSize: Math.min(46, Math.max(22, parent.height / 3))
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom; anchors.bottomMargin: 7
                        text: action.modelData.title; color: parent.ink
                        font.pixelSize: parent.large ? 12 : 11
                    }
                }
                onClicked: menu.touchpad.insertPunctuation(modelData.symbol)
            }
        }

        Repeater {
            model: menu.candidateCells
            delegate: Button {
                id: cell
                required property var modelData
                required property int index
                objectName: "strokeCandidate" + index
                readonly property string character: index < menu.touchpad.strokeCandidates.length ? menu.touchpad.strokeCandidates[index] : ""
                readonly property var visual: character.length && menu.candidateVisuals.length > index ? menu.candidateVisuals[index] : null
                readonly property bool directionActive: menu.activeDirection === modelData.direction
                readonly property bool chosen: index === menu.touchpad.selectedCandidate && character.length > 0
                readonly property bool compact: height < 130
                Layout.row: modelData.row; Layout.column: modelData.column
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.preferredWidth: 1; Layout.preferredHeight: 1
                enabled: character.length > 0
                focusPolicy: Qt.NoFocus; hoverEnabled: enabled
                topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0; padding: 0
                Accessible.name: character.length ? "第" + (index + 1) + "候选 " + character + (visual ? "，" + visual.status : "") : "暂无候选"
                Component.onCompleted: menu.cells[modelData.direction] = cell
                background: Rectangle {
                    radius: 10
                    color: cell.directionActive ? "#EDF2FF" : cell.hovered && cell.enabled ? "#F4F7FF"
                         : cell.index === 0 && cell.enabled ? "#F0F4FF" : "#FFFFFF"
                    border.width: cell.directionActive || cell.chosen || (cell.index === 0 && cell.enabled) ? 2 : 1
                    border.color: cell.directionActive || cell.chosen ? "#345EE5"
                                : cell.index === 0 && cell.enabled ? "#AABCF2" : "#DCE4F0"
                }
                contentItem: Item {
                    readonly property color ink: cell.directionActive ? "#345EE5" : cell.enabled ? "#71809A" : "#A7B0C0"
                    readonly property real glyphTop: cell.compact ? 22 : 27
                    readonly property real glyphBottom: height - (cell.compact ? 21 : 28)
                    readonly property real side: Math.max(20, Math.min(112, width - 24, glyphBottom - glyphTop))
                    Text { x: 12; y: 7; text: cell.modelData.arrow; color: parent.ink; font.pixelSize: cell.compact ? 12 : 14 }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 12; y: 7
                        text: cell.index + 1; color: parent.ink; font.pixelSize: cell.compact ? 12 : 14
                    }
                    Image {
                        objectName: "strokeCandidateGlyph" + cell.index
                        x: (parent.width - width) / 2; y: parent.glyphTop + (parent.glyphBottom - parent.glyphTop - height) / 2
                        width: parent.side; height: width
                        source: cell.visual ? cell.visual.source : ""
                        sourceSize.width: 192; sourceSize.height: 192
                        fillMode: Image.PreserveAspectFit
                        visible: source.toString().length > 0
                    }
                    Text {
                        x: (parent.width - parent.side) / 2; y: parent.glyphTop + (parent.glyphBottom - parent.glyphTop - parent.side) / 2
                        width: parent.side; height: parent.side
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: cell.character.length ? cell.character : "—"
                        visible: !cell.visual || !cell.visual.source
                        font.family: Qt.platform.os === "osx" ? "Kaiti SC" : "KaiTi"
                        font.pixelSize: Math.round(parent.side * .82)
                        color: cell.enabled ? "#24334D" : "#C3CBD7"
                    }
                    Text {
                        x: 6; width: parent.width - 12
                        y: parent.height - (cell.compact ? 21 : 24); height: 18
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: cell.visual ? cell.visual.status + (cell.visual.next ? " · 下一笔 " + cell.visual.next : "")
                                          : cell.character.length ? "候选" : "等待输入"
                        color: parent.ink; font.pixelSize: cell.width >= 105 ? 11 : 10
                        elide: Text.ElideRight
                    }
                }
                onClicked: menu.touchpad.selectCandidate(index)
            }
        }
    }

    // Display only: both layers ignore pointer input so the cells stay clickable.
    Item {
        id: overlay
        anchors.fill: grid
        enabled: false

        StrokeTrail {
            id: trail
            // Handwriting is centered like the standalone trial; only the
            // selection path below starts from the hold origin.
            anchors.fill: parent
            centered: true
            opacity: .42
            visible: !menu.selecting
            trailStyle: ({color: "#234CDB", line_width: 3, hold_ms: menu.touchpad.strokeTrailStyle.hold_ms,
                          fade_ms: menu.touchpad.strokeTrailStyle.fade_ms})
        }

        Canvas {
            id: selectionPath
            objectName: "strokeSelectionPath"
            anchors.fill: parent
            // Always mounted: a hidden Canvas would drop the first selecting packet's paint.

            function rect(direction) {
                const item = menu.cells[direction]
                if (!item) return null
                const p = item.mapToItem(selectionPath, 0, 0)
                return {x: p.x, y: p.y, width: item.width, height: item.height,
                        cx: p.x + item.width / 2, cy: p.y + item.height / 2}
            }
            // Port of stroke_trial_trace.selection_point: categorical sectors,
            // with travel compressed inside the target cell.
            function project(point, origin, direction) {
                const dx = point[0] - origin[0], dy = point[1] - origin[1]
                const distance = Math.hypot(dx, dy)
                const center = rect("center")
                const threshold = menu.touchpad.selectionDistance
                if (distance < threshold) {
                    const radius = Math.min(center.width, center.height) * .36
                    return {x: center.cx + dx / threshold * radius, y: center.cy + dy / threshold * radius}
                }
                if (!direction) {
                    const degrees = Math.atan2(dy, dx) * 180 / Math.PI + 22.5
                    const sector = Math.floor((((degrees % 360) + 360) % 360) / 45)
                    direction = ["right", "down-right", "down", "down-left", "left", "up-left", "up", "up-right"][sector]
                }
                const target = rect(direction)
                if (!target) return {x: center.cx, y: center.cy}
                const vx = target.cx - center.cx, vy = target.cy - center.cy
                const gain = .72 + .24 * (1 - Math.exp(-(distance / threshold - 1) / 4))
                const length = Math.hypot(vx, vy)
                const lateral = Math.min(target.width, target.height) * .22
                const x = center.cx + vx * gain + (dx / distance - vx / length) * lateral
                const y = center.cy + vy * gain + (dy / distance - vy / length) * lateral
                const inset = Math.min(10, target.width * .15, target.height * .15)
                return {x: Math.max(target.x + inset, Math.min(target.x + target.width - inset, x)),
                        y: Math.max(target.y + inset, Math.min(target.y + target.height - inset, y))}
            }
            onPaint: {
                const c = getContext("2d"); c.reset()
                const display = menu.selection.display
                if (!menu.selecting || !display || !display.points || !display.points.length || !rect("center")) return
                const raw = display.points
                const path = raw.map(function(point, i) {
                    return selectionPath.project(point, display.origin, i === raw.length - 1 ? menu.selection.direction : null)
                })
                const start = rect("center")
                c.globalAlpha = .42
                c.strokeStyle = "#234CDB"; c.lineWidth = 3; c.lineCap = "round"; c.lineJoin = "round"
                c.beginPath(); c.moveTo(start.cx, start.cy)
                for (let i = 0; i < path.length; i++) c.lineTo(path[i].x, path[i].y)
                c.stroke()
                // The stable origin and live endpoint stay distinct over the glyphs.
                c.globalAlpha = .9
                c.fillStyle = "#FFFFFF"; c.strokeStyle = "#21A675"; c.lineWidth = 2
                c.beginPath(); c.arc(start.cx, start.cy, 4, 0, 2 * Math.PI); c.fill(); c.stroke()
                const end = path[path.length - 1]
                c.fillStyle = "#345EE5"; c.strokeStyle = "#FFFFFF"
                c.beginPath(); c.arc(end.x, end.y, 6, 0, 2 * Math.PI); c.fill(); c.stroke()
            }
            Connections {
                target: menu.touchpad
                function onSelectionChanged() { selectionPath.requestPaint() }
            }
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
        }
    }
}
