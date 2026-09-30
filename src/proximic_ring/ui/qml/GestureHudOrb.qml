import QtQuick

Item {
    id: orb
    property string symbol: "up"
    property color tint: "#56DDF4"
    property string label: ""
    property string detail: ""
    property bool available: true
    readonly property color effectiveTint: available ? tint : "#D4DBE5"
    property real labelWidth: 84
    property real labelOffset: 0
    property real labelOpacity: 1
    width: 48; height: 48

    Canvas {
        id: globe
        anchors.fill: parent
        opacity: 1
        onPaint: {
            var c = getContext("2d"); c.reset(); c.scale(width/80,height/80)
            var color = orb.effectiveTint
            var halo = c.createRadialGradient(40,40,8,40,40,40)
            halo.addColorStop(0, Qt.rgba(color.r, color.g, color.b, 0.30))
            halo.addColorStop(1, "transparent")
            c.fillStyle = halo; c.fillRect(0,0,80,80)
            var fill = c.createRadialGradient(30,25,2,42,41,31)
            fill.addColorStop(0, "#FFFFFF")
            fill.addColorStop(0.55, Qt.lighter(color, 1.08))
            fill.addColorStop(1, color)
            c.fillStyle = fill; c.beginPath(); c.arc(40,40,29,0,Math.PI*2); c.fill()
            c.strokeStyle = Qt.rgba(color.r,color.g,color.b,0.6); c.lineWidth = 1; c.stroke()
            // A restrained lower-rim highlight at the compact 32-point diameter.
            c.strokeStyle = Qt.rgba(1,1,1,0.35); c.lineWidth = 1.3
            c.beginPath(); c.arc(40,40,27,0.2,1.4); c.stroke()
        }
        Connections {
            target: orb
            function onEffectiveTintChanged() { globe.requestPaint() }
        }
    }
    GestureHudIcon {
        anchors.centerIn: parent
        width: 23; height: 23
        symbol: orb.symbol
        ink: orb.available ? "#243D58" : "#6B7D92"
    }
    Rectangle {
        objectName: "satelliteLabel"
        x: (orb.width-width)/2 + orb.labelOffset
        y: orb.height - 5
        width: orb.labelWidth
        height: 34
        opacity: orb.labelOpacity
        radius: 6; color: "#F5F8FC"; border.color: "#DCE5EF"
        Text {
            x: 4; y: 3; width: parent.width-8; height: 12
            text: orb.label; font.pixelSize: 10; color: "#516580"
            horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
        }
        Text {
            objectName: "satelliteCaption"
            x: 4; y: 16; width: parent.width-8; height: 15
            horizontalAlignment: Text.AlignHCenter
            text: orb.detail
            font.family: Qt.platform.os === "osx" ? ".AppleSystemUIFont" : "Microsoft YaHei UI"
            font.pixelSize: 12; font.weight: Font.Medium
            elide: Text.ElideRight
            color: orb.available ? "#1B3049" : "#74859A"
        }
    }
}
