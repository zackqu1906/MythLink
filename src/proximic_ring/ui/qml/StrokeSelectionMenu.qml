// Same 3×3 menu as stroke_menu: punctuation on top, delete on the left, five
// candidates; a two-finger swipe toward a cell selects it. Handwriting and the
// two-finger selection path are drawn over the cells.
import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Shapes

Rectangle {
    id: menu
    required property var touchpad
    property Item viewport: null
    readonly property var candidateVisuals: touchpad.strokeCandidateVisuals
    readonly property var selection: touchpad.selectionState
    readonly property bool selecting: selection.phase === "selecting"
    readonly property bool clearing: selecting && selection.direction === "left" && selection.clear_armed === true
    readonly property string longPullAction: touchpad.strokeCode.length ? "清空笔画" : "删除前文"
    readonly property real clearPullGutter: 60
    readonly property real correctionPullGutter: touchpad.handwritingCorrectionActive ? 60 : 0
    readonly property bool correcting: selecting && touchpad.handwritingCorrectionActive
                                     && selection.direction === "right" && selection.correction_armed === true
    readonly property real correctionProgress: correcting ? 1 : Math.max(0, Math.min(1, selection.correction_progress || 0))
    readonly property real clearProgress: clearing ? 1 : Math.max(0, Math.min(1, selection.clear_progress || 0))
    property var deleteCell: null
    property var correctionCell: null
    readonly property string activeDirection: selecting ? (selection.direction || "center") : ""
    readonly property var punctuationPull: selecting ? selection.punctuation_pull : null
    readonly property bool morePunctuationOpen: !!punctuationPull && punctuationPull.opened === true
    property real punctuationX: width / 2
    property real punctuationY: 0
    function updatePunctuationPosition() {
        const pull = punctuationPull
        const cell = pull ? cells[pull.direction] : null
        if (!cell) return
        const half = morePunctuation.width / 2
        const viewportOrigin = viewport ? mapToItem(viewport, 0, 0) : Qt.point(0, 0)
        const minX = viewport ? half + 8 - viewportOrigin.x : half
        const maxX = viewport ? viewport.width - half - 8 - viewportOrigin.x : width - half
        const minY = viewport ? half + 8 - viewportOrigin.y : -half
        const maxY = viewport ? viewport.height - half - cancelPunctuation.height - 12 - viewportOrigin.y : height - half
        const progress = pull.progress
        const startX = grid.x + cell.x + cell.width / 2
        const endX = Math.min(Math.max(startX, minX), maxX)
        const endY = Math.min(Math.max(8, minY), maxY)
        const startY = grid.y + cell.y + cell.height / 2
        punctuationX = startX + (endX - startX) * progress
        punctuationY = startY + (endY - startY) * progress
    }
    onPunctuationPullChanged: updatePunctuationPosition()
    onWidthChanged: updatePunctuationPosition()
    onHeightChanged: updatePunctuationPosition()
    Connections {
        target: menu.viewport
        ignoreUnknownSignals: true
        function onContentYChanged() { menu.updatePunctuationPosition() }
        function onWidthChanged() { menu.updatePunctuationPosition() }
        function onHeightChanged() { menu.updatePunctuationPosition() }
    }
    // Briefly highlights the cell a two-finger swipe just confirmed.
    property string committedDirection: ""
    // Same order as stroke_menu.CANDIDATE_CELLS / PUNCTUATION_CELLS / ACTION_CELLS.
    readonly property var candidateCells: [
        {direction: "center", row: 1, column: 1, arrow: "首选"}, {direction: "right", row: 1, column: 2, arrow: "→"},
        {direction: "down-left", row: 2, column: 0, arrow: "↙"}, {direction: "down", row: 2, column: 1, arrow: "↓"},
        {direction: "down-right", row: 2, column: 2, arrow: "↘"}]
    readonly property var punctuationCells: [
        {direction: "up-left", row: 0, column: 0, arrow: "↖", symbol: "，", title: "逗号"},
        {direction: "up", row: 0, column: 1, arrow: "↑", symbol: "。", title: "句号"},
        {direction: "up-right", row: 0, column: 2, arrow: "↗", symbol: "？", title: "问号"},
        {direction: "left", row: 1, column: 0, arrow: "←", title: "删除", action: "delete"}]
    property var cells: ({})
    Timer { id: commitFlash; interval: 280; onTriggered: menu.committedDirection = "" }
    Connections {
        target: menu.touchpad
        function onSelectionCommitted(direction) { menu.committedDirection = direction; commitFlash.restart() }
    }
    color: clearing ? "#F6C6C2" : selecting ? (activeDirection === "left" ? "#FBE4E2" : "#DDE8FF") : "#F3F6FC"
    radius: 14
    border.color: clearing ? "#9C2F2A" : selecting ? (activeDirection === "left" ? "#C2413B" : "#345EE5") : "#DCE4F0"
    border.width: selecting ? 3 : 1
    FigmaTheme { id: theme }

    function updateTrace(points, finished) { trail.updateTrace(points, finished) }

    GridLayout {
        id: grid
        objectName: "strokeCandidateRow"
        anchors.fill: parent; anchors.margins: 10
        columns: 3; rows: 3; rowSpacing: 8; columnSpacing: 8

        Repeater {
            model: menu.punctuationCells
            delegate: Basic.Button {
                id: action
                required property var modelData
                required property int index
                objectName: modelData.action === "delete" ? "strokeDeleteCell" : "strokePunctuation" + index
                readonly property bool directionActive: menu.activeDirection === modelData.direction
                                                        || menu.committedDirection === modelData.direction
                Layout.row: modelData.row; Layout.column: modelData.column
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.preferredWidth: 1; Layout.preferredHeight: 1
                focusPolicy: Qt.NoFocus; hoverEnabled: enabled
                padding: 0; horizontalPadding: 0
                Accessible.name: modelData.action === "delete" ? (menu.clearing ? menu.longPullAction : "删除一笔或退格") : "输入标点 " + modelData.symbol
                Component.onCompleted: {
                    menu.cells[modelData.direction] = action
                    if (modelData.action === "delete") menu.deleteCell = action
                }
                background: Rectangle {
                    radius: 10
                    Behavior on color { ColorAnimation { duration: 120 } }
                    color: menu.clearing && action.modelData.action === "delete" ? "#9C2F2A" : action.directionActive ? (action.modelData.action === "delete" ? "#C2413B" : "#234CDB") : action.down ? "#D8E3FA" : (action.enabled && action.hovered) ? "#E7EDFB" : "#EDF2FB"
                    border.width: action.directionActive ? 2 : 1
                    border.color: action.directionActive ? (action.modelData.action === "delete" ? "#9C2F2A" : "#1739AC") : "#DCE4F0"
                }
                contentItem: Item {
                    readonly property color ink: action.directionActive ? "white" : "#647895"
                    readonly property bool large: action.height >= 115
                    Text {
                        id: actionDirection
                        x: 12; y: 7
                        text: action.modelData.arrow; color: parent.ink; font.pixelSize: parent.large ? 14 : 12
                    }
                    Text {
                        id: actionSymbol
                        anchors { left: parent.left; right: parent.right; top: actionDirection.bottom; bottom: actionCaption.top
                                  leftMargin: 8; rightMargin: 8; topMargin: 2; bottomMargin: 2 }
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: action.modelData.symbol || ""; color: parent.ink
                        font.pixelSize: Math.min(46, Math.round(parent.height / 3))
                        fontSizeMode: Text.Fit; minimumPixelSize: 12
                    }
                    Shape {
                        visible: action.modelData.action === "delete"
                        anchors.centerIn: actionSymbol
                        width: 32; height: 22
                        scale: Math.min(1.35, actionSymbol.width / width, actionSymbol.height / height)
                        ShapePath {
                            strokeColor: actionSymbol.color; strokeWidth: 1.8
                            fillColor: "transparent"; joinStyle: ShapePath.RoundJoin; capStyle: ShapePath.RoundCap
                            PathSvg { path: "M 1,11 L 11,2 H 31 V 20 H 11 Z M 15,7 L 24,15 M 24,7 L 15,15" }
                        }
                    }
                    Text {
                        id: actionCaption
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom; anchors.bottomMargin: 7
                        text: menu.clearing && action.modelData.action === "delete" ? menu.longPullAction : action.modelData.title; color: parent.ink
                        font.pixelSize: parent.large ? 12 : 11
                    }
                }
                onClicked: modelData.action === "delete" ? menu.touchpad.deleteOne()
                                                         : menu.touchpad.insertPunctuation(modelData.symbol)
            }
        }

        Repeater {
            model: menu.candidateCells
            delegate: Basic.Button {
                id: cell
                required property var modelData
                required property int index
                objectName: "strokeCandidate" + index
                readonly property string character: index < menu.touchpad.strokeCandidates.length ? menu.touchpad.strokeCandidates[index] : ""
                readonly property var visual: character.length && menu.candidateVisuals.length > index ? menu.candidateVisuals[index] : null
                readonly property bool directionActive: menu.activeDirection === modelData.direction
                                                        || (menu.committedDirection === modelData.direction && character.length > 0)
                readonly property bool chosen: index === menu.touchpad.selectedCandidate && character.length > 0
                readonly property bool compact: height < 130
                Layout.row: modelData.row; Layout.column: modelData.column
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.preferredWidth: 1; Layout.preferredHeight: 1
                enabled: character.length > 0
                focusPolicy: Qt.NoFocus; hoverEnabled: enabled
                padding: 0; horizontalPadding: 0
                Accessible.name: character.length ? "第" + (index + 1) + "候选 " + character + (visual ? "，" + visual.status : "") : "暂无候选"
                Component.onCompleted: {
                    menu.cells[modelData.direction] = cell
                    if (modelData.direction === "right") menu.correctionCell = cell
                }
                background: Rectangle {
                    radius: 10
                    Behavior on color { ColorAnimation { duration: 120 } }
                    color: cell.directionActive ? "#EDF2FF" : cell.hovered && cell.enabled ? "#F4F7FF"
                         : cell.index === 0 && cell.enabled ? "#F0F4FF" : "#FFFFFF"
                    border.width: cell.directionActive || cell.chosen || (cell.index === 0 && cell.enabled) ? 2 : 1
                    border.color: cell.directionActive || cell.chosen ? "#345EE5"
                                : cell.index === 0 && cell.enabled ? "#AABCF2" : "#DCE4F0"
                }
                contentItem: Item {
                    readonly property color ink: cell.directionActive ? "#345EE5" : cell.enabled ? "#71809A" : "#A7B0C0"
                    readonly property real glyphTop: 7 + Math.max(candidateDirection.height, candidateRank.height) + 2
                    readonly property real glyphBottom: candidateStatus.y - 2
                    readonly property real side: Math.max(1, Math.min(112, width - 24, glyphBottom - glyphTop))
                    Text {
                        id: candidateDirection
                        x: 12; y: 7
                        text: cell.modelData.arrow; color: parent.ink; font.pixelSize: cell.compact ? 12 : 14
                    }
                    Text {
                        id: candidateRank
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
                        // Guesses that only fill an otherwise short page are drawn lighter.
                        opacity: cell.visual && cell.visual.guess ? .5 : 1
                    }
                    Text {
                        x: (parent.width - parent.side) / 2; y: parent.glyphTop + (parent.glyphBottom - parent.glyphTop - parent.side) / 2
                        width: parent.side; height: parent.side
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: cell.character.length ? cell.character : "—"
                        visible: !cell.visual || !cell.visual.source
                        font.family: Qt.platform.os === "osx" ? "Kaiti SC" : "KaiTi"
                        font.pixelSize: Math.max(1, Math.round(parent.side * .82))
                        fontSizeMode: Text.Fit; minimumPixelSize: 1
                        color: cell.enabled ? "#24334D" : "#C3CBD7"
                    }
                    Text {
                        id: candidateStatus
                        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 6 }
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

    // Keep the original grid readable under the raised punctuation surface.
    Item {
        id: morePunctuation
        objectName: "strokeMorePunctuation"
        z: 2
        width: Math.round(menu.width * .72); height: width
        x: menu.punctuationX - width / 2; y: menu.punctuationY - height / 2
        opacity: menu.morePunctuationOpen ? 1 : 0
        scale: menu.morePunctuationOpen ? 1 : .64
        visible: opacity > 0
        enabled: menu.morePunctuationOpen
        Behavior on opacity { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        Behavior on scale {
            NumberAnimation {
                duration: menu.morePunctuationOpen ? 220 : 140
                easing.type: menu.morePunctuationOpen ? Easing.OutBack : Easing.InCubic
                easing.overshoot: 1.1
            }
        }
        Rectangle {
            anchors.fill: parent; anchors.topMargin: 3
            radius: 14; color: "#2424334D"
        }
        Rectangle {
            anchors.fill: parent
            radius: 14; color: "#E8F3F6FC"; border.color: "#AABCF2"
        }
        GridLayout {
            anchors.fill: parent; anchors.margins: 8
            columns: 3; rows: 3; columnSpacing: 4; rowSpacing: 4
            Repeater {
                model: menu.touchpad.morePunctuationCells
                delegate: Rectangle {
                    id: punctuationCell
                    required property var modelData
                    required property int index
                    Layout.preferredWidth: 1; Layout.preferredHeight: 1
                    Layout.fillWidth: true; Layout.fillHeight: true
                    radius: 10; color: "#CCFFFFFF"; border.color: "#DCE4F0"
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 2; spacing: 2
                        Repeater {
                            model: punctuationCell.modelData
                            delegate: Basic.Button {
                                id: symbolButton
                                required property string modelData
                                readonly property bool selected: menu.morePunctuationOpen
                                    && menu.punctuationPull.symbol === modelData
                                objectName: "strokeMorePunctuation-" + modelData
                                Layout.preferredWidth: 1; Layout.preferredHeight: 1
                                Layout.fillWidth: true; Layout.fillHeight: true
                                implicitWidth: 1; implicitHeight: 1
                                focusPolicy: Qt.NoFocus
                                padding: 0
                                Accessible.name: "输入 " + modelData
                                background: Rectangle {
                                    radius: 8
                                    color: symbolButton.selected ? "#234CDB" : symbolButton.hovered ? "#DDE8FF" : "transparent"
                                    border.width: symbolButton.selected ? 1 : 0
                                    border.color: "#1739AC"
                                }
                                contentItem: Canvas {
                                    readonly property var outline: menu.touchpad.punctuationOutline(symbolButton.modelData)
                                    readonly property color ink: symbolButton.selected ? "white" : "#24334D"
                                    readonly property real glyphScale: Math.max(10, Math.min(30, morePunctuation.height * .16)) / 96
                                    onInkChanged: requestPaint()
                                    onOutlineChanged: requestPaint()
                                    onGlyphScaleChanged: requestPaint()
                                    onAvailableChanged: if (available) requestPaint()
                                    onWidthChanged: requestPaint()
                                    onHeightChanged: requestPaint()
                                    onPaint: {
                                        const c = getContext("2d"); c.reset()
                                        c.translate(width / 2, height / 2)
                                        c.scale(glyphScale, glyphScale)
                                        c.fillStyle = ink
                                        c.beginPath()
                                        for (const contour of outline) {
                                            c.moveTo(contour[0][0], contour[0][1])
                                            for (let i = 1; i < contour.length; i++)
                                                c.lineTo(contour[i][0], contour[i][1])
                                            c.closePath()
                                        }
                                        c.fill()
                                    }
                                }
                                onClicked: menu.touchpad.insertPunctuation(modelData)
                            }
                        }
                    }
                    Rectangle {
                        visible: punctuationCell.modelData.length === 2
                        width: 1; height: parent.height - 12; color: "#DCE4F0"
                        anchors.centerIn: parent
                    }
                }
            }
        }
    }

    component PullSlider: Item {
        id: slider
        property bool shown: false
        property bool armed: progress >= 1
        property bool destructive: false
        property real progress: 0
        property int arrowRotation: 0
        property string caption
        readonly property color ink: armed ? "white" : destructive ? "#A6322C" : "#234CDB"
        width: menu.clearPullGutter; height: 58
        opacity: shown ? 1 : 0
        scale: shown ? 1 : .9
        visible: opacity > 0
        enabled: false
        Accessible.role: Accessible.StaticText
        Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        // Keep the slider visible with Qt's software renderer too. A shader
        // layer (MultiEffect) can blank the whole armed control on Windows.
        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: 19
            visible: slider.armed
            color: slider.destructive ? "#9C2F2A" : "#234CDB"
            opacity: .18
        }
        Canvas {
            id: surface
            anchors.fill: parent
            onAvailableChanged: if (available) requestPaint()
            onVisibleChanged: if (visible) requestPaint()
            onPaint: {
                const c = getContext("2d"); c.reset()
                c.beginPath(); c.roundedRect(.5, .5, width - 1, height - 1, 16, 16)
                c.fillStyle = slider.destructive ? "#FCE9E6" : "#EDF2FB"; c.fill()
                c.save(); c.clip()
                c.fillStyle = slider.destructive ? (slider.armed ? "#9C2F2A" : "#EDABA5")
                                                 : (slider.armed ? "#234CDB" : "#AABCF2")
                const p = slider.progress
                if (slider.arrowRotation === 0)
                    c.fillRect(width * (1 - p), 0, width * p, height)
                else if (slider.arrowRotation === 180)
                    c.fillRect(0, 0, width * p, height)
                else
                    c.fillRect(0, slider.arrowRotation === 90 ? height * (1 - p) : 0, width, height * p)
                c.restore()
                c.strokeStyle = slider.destructive ? "#C2413B" : "#345EE5"
                c.lineWidth = 1; c.stroke()
            }
            Connections {
                target: slider
                function onProgressChanged() { surface.requestPaint() }
                function onArmedChanged() { surface.requestPaint() }
            }
        }
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 9; spacing: -8
            Repeater {
                model: slider.destructive || slider.arrowRotation === 180 ? 2 : 1
                UiIcon {
                    width: 22; height: 22
                    symbol: slider.arrowRotation === 180 ? "chevron-right" : "chevron-left"
                    rotation: slider.arrowRotation === 180 ? 0 : slider.arrowRotation
                    ink: slider.ink
                }
            }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 32; text: slider.caption; font.pixelSize: 12; font.weight: Font.Medium; color: slider.ink
        }
    }

    PullSlider {
        id: cancelPunctuation
        objectName: "strokeCancelPunctuation"
        z: 3
        x: morePunctuation.x + (morePunctuation.width - width) / 2
        y: morePunctuation.y + morePunctuation.height * (1 + morePunctuation.scale) / 2 + 4
        shown: menu.morePunctuationOpen
        progress: menu.punctuationPull?.cancel_progress || 0
        arrowRotation: -90
        caption: "取消"
        Accessible.name: "继续下滑取消标点选择"
    }

    PullSlider {
        id: punctuationSlider
        objectName: "strokePunctuationPull"
        z: 3
        x: menu.punctuationX - width / 2; y: menu.punctuationY - height / 2
        shown: !!menu.punctuationPull && !menu.morePunctuationOpen
        progress: menu.punctuationPull?.progress || 0
        arrowRotation: 90
        caption: "更多"
        Accessible.name: "继续上滑展开更多标点"
    }

    PullSlider {
        id: clearPull
        objectName: "strokeClearPull"
        x: 10 - width
        y: menu.deleteCell ? 10 + menu.deleteCell.y + (menu.deleteCell.height - height) / 2 : 0
        shown: menu.selecting && menu.activeDirection === "left"
        destructive: true
        armed: menu.clearing
        progress: menu.clearProgress
        caption: menu.touchpad.strokeCode.length ? "清空" : "删除前文"
        Accessible.name: (menu.clearing ? "抬手" : "继续向左外拉") + menu.longPullAction
    }

    PullSlider {
        id: correctionPull
        objectName: "strokeCorrectionPull"
        x: menu.width - 10
        y: menu.correctionCell ? 10 + menu.correctionCell.y + (menu.correctionCell.height - height) / 2 : 0
        width: menu.correctionPullGutter
        shown: menu.touchpad.handwritingCorrectionActive && menu.selecting && menu.activeDirection === "right"
        armed: menu.correcting
        progress: menu.correctionProgress
        arrowRotation: 180
        caption: "修正"
        Accessible.name: (menu.correcting ? "抬手" : "继续向右外拉") + "修正前文"
    }

    // Display only: both layers ignore pointer input so the cells stay clickable.
    Item {
        id: overlay
        anchors.fill: grid
        enabled: false

        StrokeTrail {
            id: trail
            // Handwriting is centered like the standalone trial; only the
            // two-finger selection path below starts from the centre cell.
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
            anchors.margins: -10
            anchors.leftMargin: -10 - menu.clearPullGutter
            anchors.rightMargin: -10 - menu.correctionPullGutter
            // Always mounted: a hidden Canvas would drop the first selecting packet's paint.

            function rect(direction) {
                const item = direction === "clear" ? clearPull : direction === "correction" ? correctionPull : menu.cells[direction]
                if (!item) return null
                const p = item.mapToItem(selectionPath, 0, 0)
                return {x: p.x, y: p.y, width: item.width, height: item.height,
                        cx: p.x + item.width / 2, cy: p.y + item.height / 2}
            }
            // Keep the sector projection; extend left into the clear tab.
            function project(point, origin, direction) {
                const dx = point[0] - origin[0], dy = point[1] - origin[1]
                const distance = Math.hypot(dx, dy)
                const center = rect("center")
                const threshold = menu.selection.threshold || menu.touchpad.selectionDistance
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
                let boundedX = Math.max(target.x + inset, Math.min(target.x + target.width - inset, x))
                let boundedY = Math.max(target.y + inset, Math.min(target.y + target.height - inset, y))
                if (direction === "left") {
                    const approach = Math.max(0, Math.min(1, (-dx - threshold) / (2 * threshold)))
                    boundedX += (target.x - boundedX) * approach
                    const clearDistance = menu.selection.clear_distance || 5 * threshold
                    const outward = Math.max(0, Math.min(1, (-dx - 3 * threshold) / (clearDistance - 3 * threshold)))
                    const clear = rect("clear")
                    boundedX += (clear.x + 10 - boundedX) * outward
                    boundedY += (clear.cy - boundedY) * outward
                } else if (direction === "right" && menu.touchpad.handwritingCorrectionActive) {
                    const approach = Math.max(0, Math.min(1, (dx - threshold) / (2 * threshold)))
                    boundedX += (target.x + target.width - boundedX) * approach
                    const limit = menu.selection.clear_distance || 5 * threshold
                    const outward = Math.max(0, Math.min(1, (dx - 3 * threshold) / (limit - 3 * threshold)))
                    const edit = rect("correction")
                    boundedX += (edit.x + edit.width - 10 - boundedX) * outward
                    boundedY += (edit.cy - boundedY) * outward
                }
                return {x: boundedX, y: boundedY}
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
                if (menu.punctuationPull) return
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
