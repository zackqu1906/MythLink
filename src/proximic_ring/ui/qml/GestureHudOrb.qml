import QtQuick

Item {
    id: orb
    property string symbol: "up"
    property color tint: "#56DDF4"
    property string label: ""
    property string detail: ""
    property bool available: true
    readonly property color effectiveTint: available ? tint : "#67758B"
    property real labelWidth: 84
    property real labelOffset: 0
    property real labelOpacity: 1
    width: 44; height: 44

    Canvas {
        id: globe
        anchors.fill: parent
        opacity: orb.available ? 1 : 0.65
        onPaint: {
            var c = getContext("2d"); c.reset(); c.scale(width/80,height/80)
            var color = orb.effectiveTint
            var halo = c.createRadialGradient(40,40,8,40,40,40)
            halo.addColorStop(0, Qt.rgba(color.r, color.g, color.b, 0.48))
            halo.addColorStop(1, "transparent")
            c.fillStyle = halo; c.fillRect(0,0,80,80)
            var fill = c.createRadialGradient(30,25,2,42,41,31)
            fill.addColorStop(0, Qt.lighter(color, 1.5))
            fill.addColorStop(0.45, color)
            fill.addColorStop(1, Qt.darker(color, 2.5))
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
        width: 20; height: 20
        symbol: orb.symbol
        opacity: orb.available ? 1 : 0.6
        ink: Qt.colorEqual(orb.tint, "#F0B153") && orb.available ? "#4B2B0E" : "#F2FAFF"
    }
    Rectangle {
        objectName: "satelliteLabel"
        x: (orb.width-width)/2 + orb.labelOffset
        y: orb.height - 5
        width: orb.labelWidth
        height: caption.implicitHeight + 4
        opacity: orb.labelOpacity
        radius: 4; color: "#DB101827"
        Text {
            id: caption
            objectName: "satelliteCaption"
            x: 4; y: 2; width: parent.width-8
            horizontalAlignment: Text.AlignHCenter
            text: orb.label + (orb.detail ? "\n" + orb.detail : "")
            font.family: Qt.platform.os === "osx" ? ".AppleSystemUIFont" : "Microsoft YaHei UI"
            font.pixelSize: 9; font.weight: Font.Medium
            lineHeightMode: Text.FixedHeight; lineHeight: 12
            color: !orb.available ? "#8592A7" : Qt.colorEqual(orb.tint,"#F0B153") ? "#FDE68A" :
                   Qt.colorEqual(orb.tint,"#56DDF4") ? "#CFFAFE" : "#E8E5FF"
        }
    }
}
