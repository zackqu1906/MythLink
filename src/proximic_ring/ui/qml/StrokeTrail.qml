import QtQuick

Item {
    id: trail
    objectName: "strokeTrailDisplay"
    property var points: []
    function updateTrace(value, finished) {
        fade.stop(); points = value; canvas.opacity = 1; canvas.requestPaint()
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
            const origin = p[0]
            let extentX = 1, extentY = 1
            for (let i = 1; i < p.length; i++) {
                extentX = Math.max(extentX, Math.abs(p[i][0] - origin[0]))
                extentY = Math.max(extentY, Math.abs(p[i][1] - origin[1]))
            }
            const scale = Math.min(1.6, Math.min(width, height) * 0.43 / Math.max(extentX, extentY))
            function point(i) { return {x: width / 2 + (p[i][0] - origin[0]) * scale,
                                      y: height / 2 + (p[i][1] - origin[1]) * scale} }
            // Smooth the display only. Recognition uses the untouched path.
            c.strokeStyle = "#082ACB"; c.lineWidth = 3.2; c.lineCap = "round"; c.lineJoin = "round"
            c.beginPath(); const a = point(0); c.moveTo(a.x, a.y)
            for (let i = 1; i < p.length - 1; i++) {
                const b = point(i), d = point(i + 1)
                c.quadraticCurveTo(b.x, b.y, (b.x + d.x) / 2, (b.y + d.y) / 2)
            }
            const last = point(p.length - 1); c.lineTo(last.x, last.y); c.stroke()
        }
    }
    NumberAnimation { id: fade; target: canvas; property: "opacity"; from: 1; to: 0; duration: 280; easing.type: Easing.OutCubic }
    // Display only: no MouseArea, pointer handlers, or touch drawing.
}
