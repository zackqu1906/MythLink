import QtQuick

Item {
    id: trail
    objectName: "strokeTrailDisplay"
    property var points: []
    required property var trailStyle
    property bool standard: false
    // Center the ink's bounding box instead of anchoring its first point.
    property bool centered: false
    function updateTrace(value, finished) {
        fade.stop(); points = value; standard = finished; canvas.opacity = 1; canvas.requestPaint()
        if (finished && value.length > 1) fade.restart()
    }
    Canvas {
        id: canvas
        objectName: "strokeTrailCanvas"
        anchors.fill: parent
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const c = getContext("2d"); c.reset()
            const p = trail.points
            if (p.length < 2) return
            let dx = 0, dy = 0
            if (trail.centered) {
                let x0 = p[0][0], x1 = x0, y0 = p[0][1], y1 = y0
                for (let i = 1; i < p.length; i++) {
                    x0 = Math.min(x0, p[i][0]); x1 = Math.max(x1, p[i][0])
                    y0 = Math.min(y0, p[i][1]); y1 = Math.max(y1, p[i][1])
                }
                dx = .5 - (x0 + x1) / 2; dy = .5 - (y0 + y1) / 2
            }
            function point(i) { return {x: (p[i][0] + dx) * width, y: (p[i][1] + dy) * height} }
            // Smooth the display only. Recognition uses the untouched path.
            c.strokeStyle = trail.trailStyle.color; c.lineWidth = trail.trailStyle.line_width
            c.lineCap = "round"; c.lineJoin = "round"
            c.beginPath(); const a = point(0); c.moveTo(a.x, a.y)
            for (let i = 1; i < p.length - 1; i++) {
                const b = point(i), d = point(i + 1)
                if (trail.standard) c.lineTo(b.x, b.y)
                else c.quadraticCurveTo(b.x, b.y, (b.x + d.x) / 2, (b.y + d.y) / 2)
            }
            const last = point(p.length - 1); c.lineTo(last.x, last.y); c.stroke()
        }
    }
    SequentialAnimation {
        id: fade
        PauseAnimation { duration: trail.trailStyle.hold_ms }
        NumberAnimation { target: canvas; property: "opacity"; from: 1; to: 0; duration: trail.trailStyle.fade_ms; easing.type: Easing.OutCubic }
    }
    // Display only: no MouseArea, pointer handlers, or touch drawing.
}
