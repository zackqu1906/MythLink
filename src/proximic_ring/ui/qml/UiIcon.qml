import QtQuick
import QtQuick.Window
import QtQuick.Controls.impl

// Original Figma vectors for the redesigned shell. Remaining status symbols
// are local UI primitives; they are not substitutes for the hand illustrations.
Item {
    id: icon
    property string symbol: "home"
    property color ink: "#0225BA"
    property bool tint: false
    readonly property var assetNames: ["home", "voice", "gesture", "mail", "settings", "help",
        "bluetooth", "history", "clock", "operation", "edit", "coffee", "book", "copy", "more", "search", "save", "microphone", "pointer", "tool"]
    readonly property bool hasAsset: assetNames.indexOf(symbol) >= 0
    implicitWidth: 24; implicitHeight: 24

    IconImage {
        objectName: "figmaIconImage"
        anchors.fill: parent
        visible: icon.hasAsset
        source: icon.hasAsset ? "../assets/figma/icon-" + icon.symbol + ".svg" : ""
        sourceSize.width: Math.ceil(width * Screen.devicePixelRatio)
        fillMode: Image.PreserveAspectFit
        smooth: true
        color: icon.tint ? icon.ink : "transparent"
    }
    Canvas {
        id: fallback
        anchors.fill: parent
        visible: !icon.hasAsset
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        Connections {
            target: icon
            function onSymbolChanged() { fallback.requestPaint() }
            function onInkChanged() { fallback.requestPaint() }
        }
        onPaint: {
            var c = getContext("2d")
            c.reset(); c.scale(width / 24, height / 24)
            c.strokeStyle = icon.ink; c.fillStyle = icon.ink
            c.lineWidth = 1.8; c.lineCap = "round"; c.lineJoin = "round"
            function line(p) {
                c.beginPath(); c.moveTo(p[0],p[1])
                for (var i=2;i<p.length;i+=2) c.lineTo(p[i],p[i+1])
                c.stroke()
            }
            function box(x,y,w,h,r) { c.beginPath(); c.roundedRect(x,y,w,h,r,r); c.stroke() }
            function circle(x,y,r) { c.beginPath(); c.arc(x,y,r,0,Math.PI*2); c.stroke() }
            if (icon.symbol === "mic") {
                box(9,3,6,12,3); c.beginPath(); c.arc(12,11,6,0,Math.PI); c.stroke()
                line([12,17,12,21]); line([8,21,16,21])
            } else if (icon.symbol === "check") {
                line([5,12,10,17,20,6])
            } else if (icon.symbol === "lock") {
                box(5,10,14,11,2); c.beginPath(); c.arc(12,10,5,Math.PI,Math.PI*2); c.stroke(); line([12,14,12,17])
            } else if (icon.symbol === "search") {
                circle(10,10,6); line([15,15,21,21])
            } else if (icon.symbol === "apps") {
                box(3,3,7,7,1); box(14,3,7,7,1); box(3,14,7,7,1); box(14,14,7,7,1)
            } else if (icon.symbol === "arrow") {
                line([4,12,20,12]); line([15,7,20,12,15,17])
            }
        }
    }
}
