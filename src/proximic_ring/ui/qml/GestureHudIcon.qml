import QtQuick

Canvas {
    id: icon
    property string symbol: "up"
    property color ink: "#F2FAFF"
    width: 40; height: 40
    onSymbolChanged: requestPaint()
    onInkChanged: requestPaint()
    onPaint: {
        var c = getContext("2d")
        c.reset()
        c.scale(width / 40, height / 40)
        c.strokeStyle = ink
        c.fillStyle = ink
        c.lineWidth = 2.7
        c.lineCap = "round"
        c.lineJoin = "round"
        function line(points) {
            c.beginPath(); c.moveTo(points[0], points[1])
            for (var i = 2; i < points.length; i += 2) c.lineTo(points[i], points[i + 1])
            c.stroke()
        }
        function box(x, y, w, h, r) {
            c.beginPath(); c.roundedRect(x, y, w, h, r, r); c.stroke()
        }
        if (symbol === "up") {
            line([20,32,20,8]); line([10,18,20,8,30,18])
        } else if (symbol === "down") {
            line([20,8,20,32]); line([10,22,20,32,30,22])
        } else if (symbol === "undo") {
            line([16,9,7,18,16,26]); c.beginPath(); c.moveTo(8,18)
            c.lineTo(24,18); c.bezierCurveTo(38,18,37,34,23,34); c.lineTo(16,34); c.stroke()
        } else if (symbol === "mic") {
            box(15,5,10,21,5); c.beginPath(); c.moveTo(10,20)
            c.bezierCurveTo(10,36,30,36,30,20); c.stroke()
            line([20,32,20,37]); line([15,37,25,37])
        } else if (symbol === "edit") {
            line([8,28,27,9,34,16,15,35,7,36,8,28,15,35]); line([23,13,30,20])
        } else if (symbol === "apps") {
            box(6,6,17,17,4); box(18,18,16,16,4)
        } else if (symbol === "keyboard") {
            box(3,8,34,24,5)
            for (var k = 0; k < 4; k++) { c.beginPath(); c.arc(10 + 7*k,16,1.4,0,2*Math.PI); c.fill() }
            line([11,25,29,25])
        } else if (symbol === "scroll") {
            line([20,5,20,35]); line([10,15,20,5,30,15])
            line([10,25,20,35,30,25])
        } else if (symbol === "pinch") {
            line([11,30,18,23,22,16,28,10])
            line([18,23,25,22,30,17])
        } else if (symbol === "left" || symbol === "right") {
            if (symbol === "left") { line([33,20,7,20]); line([17,10,7,20,17,30]) }
            else { line([7,20,33,20]); line([23,10,33,20,23,30]) }
        } else if (symbol === "clockwise" || symbol === "counterclockwise") {
            if (symbol === "counterclockwise") { c.translate(40,0); c.scale(-1,1) }
            c.beginPath(); c.arc(20,21,12,0.3,Math.PI*1.8); c.stroke()
            line([28,6,31,14,22,14])
        } else if (symbol === "tap") {
            c.beginPath(); c.arc(20,20,6,0,Math.PI*2); c.stroke()
            line([20,3,20,7]); line([20,33,20,37]); line([3,20,7,20]); line([33,20,37,20])
        } else if (symbol === "snap") {
            line([23,5,11,23,21,23,17,35,30,17,20,17,23,5])
        }
    }
}
