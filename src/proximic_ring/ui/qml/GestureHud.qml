import QtQuick

Item {
    id: root
    width: 360; height: 360
    property string mode: "input"
    property string notice: ""
    property bool preview: false
    property var globalLabels: ({show_menu: "食指捏合", switch_mode: "中指捏合", window_selector: "握拳"})
    property bool inputFieldsAvailable: true
    property string inputFieldsHint: "当前窗口未提供文本框"
    property real entranceTime: 0
    readonly property bool animationRunning: entrance.running
    readonly property bool inputMode: sceneActive || mode === "input"
    readonly property color accent: inputMode ? "#568BB5" : "#8D78B5"
    readonly property real orbitRadius: 112
    readonly property real centerX: 180
    readonly property real centerY: 160
    readonly property real hubProgress: bezier(progress(0, 620), 0.16, 1, 0.3, 1)
    readonly property real orbitProgress: bezier(progress(60, 880), 0.35, 0, 0.15, 1)
    readonly property real innerProgress: bezier(progress(420, 700), 0.35, 0, 0.15, 1)
    property var orbs: []
    property int overflow: 0
    property string contextLabel: ""
    property bool voiceDisabled: false
    property bool sceneActive: false
    property string modeTitle: "输入模式"
    readonly property var satellites: orbs
    readonly property string fieldHint: !sceneActive && orbs.some(function(orb) { return orb.inputField }) ? inputFieldsHint : ""

    function clamp(value) { return Math.max(0, Math.min(1, value)) }
    function progress(delay, duration) { return clamp((entranceTime - delay) / duration) }
    function quart(value) { return 1 - Math.pow(1 - value, 4) }
    function bezier(t, x1, y1, x2, y2) {
        // Match the CSS timing curves in gesture-hud-entrance.html.
        if (t <= 0 || t >= 1) return t
        function curve(s, a, b) { return 3*(1-s)*(1-s)*s*a + 3*(1-s)*s*s*b + s*s*s }
        var low = 0, high = 1, u = 0.5
        for (var j = 0; j < 12; ++j) {
            u = (low + high) / 2
            if (curve(u, x1, x2) < t) low = u; else high = u
        }
        return curve(u, y1, y2)
    }
    function replayEntrance() {
        entrance.stop()
        entranceTime = 0
        entrance.start()
    }
    function stopEntrance() { entrance.stop() }

    NumberAnimation {
        id: entrance
        target: root; property: "entranceTime"
        from: 0; to: 6000; duration: 6000
    }
    onOrbitProgressChanged: orbit.requestPaint()
    onInnerProgressChanged: orbit.requestPaint()
    onAccentChanged: orbit.requestPaint()

    Item {
        width: 360; height: 360
        scale: Math.min(root.width, root.height) / 360
        transformOrigin: Item.TopLeft

        Canvas {
            id: orbit
            anchors.fill: parent
            onPaint: {
                var c = getContext("2d"); c.reset()
                var start = -Math.PI/2, end = start + 2*Math.PI*root.orbitProgress
                c.lineWidth = 0.7
                c.strokeStyle = Qt.rgba(root.accent.r,root.accent.g,root.accent.b,0.28)
                c.beginPath(); c.arc(root.centerX,root.centerY,root.orbitRadius,start,end); c.stroke()
                // The drawing head has the brief comet highlight of the reference.
                if (root.orbitProgress > 0 && root.orbitProgress < 1) {
                    c.lineWidth = 1.5; c.lineCap = "round"
                    c.strokeStyle = Qt.rgba(root.accent.r,root.accent.g,root.accent.b,
                                           Math.sin(Math.PI*root.orbitProgress)*0.9)
                    c.beginPath(); c.arc(root.centerX,root.centerY,root.orbitRadius,
                                        Math.max(start,end-0.16),end); c.stroke()
                }
                c.lineWidth = 0.6
                c.strokeStyle = Qt.rgba(root.accent.r,root.accent.g,root.accent.b,0.12*root.innerProgress)
                c.beginPath(); c.arc(root.centerX,root.centerY,61,start,
                                    start+2*Math.PI*root.innerProgress); c.stroke()
            }
        }

        Rectangle {
            objectName: "gestureHudHub"
            x: root.centerX - 50; y: root.centerY - 50
            width: 100; height: 100; radius: 50
            opacity: root.hubProgress
            scale: 0.82 + 0.18 * root.hubProgress
            color: root.inputMode ? "#F3FAFF" : "#F6F2FF"
            border.width: 1; border.color: root.inputMode ? "#B5CEE1" : "#D2C4E7"
            GestureHudIcon {
                x: 35; y: 12; width: 30; height: 30
                symbol: root.sceneActive ? "apps" : root.inputMode ? "keyboard" : "scroll"; ink: root.accent
            }
            Text {
                x: 0; y: 47; width: parent.width
                text: root.modeTitle
                font.pixelSize: 14; font.weight: Font.DemiBold
                color: "#192B43"; horizontalAlignment: Text.AlignHCenter
            }
            Text {
                x: 6; y: 72; width: 88
                text: root.sceneActive ? (root.orbs.length ? "场景手势" : "尚未绑定手势") : root.voiceDisabled ? "语音组停用" : root.inputMode ? "语音与编辑" : "滚动与应用操作"
                color: root.voiceDisabled && !root.sceneActive ? "#96631D" : "#556780"
                font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter
            }
        }

        Repeater {
            model: root.satellites
            delegate: GestureHudOrb {
                id: satellite
                required property int index
                required property var modelData
                objectName: "gestureSatellite" + index
                readonly property real finalAngle: index * 360 / root.satellites.length
                readonly property real t: root.progress(220 + index * 105, 1000)
                readonly property real eased: root.quart(t)
                readonly property real theta: (finalAngle - 150 * (1 - eased)) * Math.PI / 180
                readonly property real radius: root.orbitRadius * ((58/170) + (1-58/170)*eased)
                readonly property real floatOffset: root.entranceTime <= 2000 ? 0 :
                    -1.2 * (1 - Math.cos((root.entranceTime-2000)/5500*2*Math.PI))
                x: root.centerX + radius*Math.sin(theta) - width/2
                y: root.centerY - radius*Math.cos(theta) - height/2 + floatOffset
                // Scale around the orb centre, without rotating text or icons.
                transform: Scale {
                    origin.x: satellite.width/2; origin.y: satellite.height/2
                    xScale: 0.30 + 0.70*satellite.eased; yScale: xScale
                }
                opacity: root.clamp(t*1.7)
                symbol: modelData.symbol
                tint: modelData.scope === "global" ? "#F2D9AA" : root.inputMode ? "#B4DEF2" : "#D9CDF3"
                label: modelData.label; detail: modelData.action
                labelWidth: 120
                labelOffset: {
                    var preferred = Math.sin(finalAngle*Math.PI/180) > 0.5 ? 14 :
                                    Math.sin(finalAngle*Math.PI/180) < -0.5 ? -14 : 0
                    var base = satellite.x + (satellite.width - 120)/2
                    return Math.max(6, Math.min(234, base + preferred)) - base
                }
                // Text waits until the spiral is almost at its resting radius.
                labelOpacity: root.clamp((t-0.68)/0.32)
                available: !modelData.inputField || root.inputFieldsAvailable
            }
        }
    }
    Rectangle {
        objectName: "gestureHudContext"
        x: 12; y: 0; width: parent.width - 24; height: 22; radius: 11
        color: "#F5F8FC"; border.color: "#D9E3ED"
        visible: root.contextLabel.length > 0
        Text {
            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
            text: root.contextLabel; color: "#334760"; font.pixelSize: 11
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
    Text {
        objectName: "gestureHudOverflow"
        x: 12; y: 330; width: parent.width - 24; height: 14
        text: root.notice || ((root.overflow ? "另有 " + root.overflow + " 项手势 · " : "") +
                             (root.fieldHint || (root.overflow ? "可在场景与手势中查看" : "")))
        font.pixelSize: 10; color: root.notice ? "#9C651D" : "#40536B"
        horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
        style: Text.Outline; styleColor: "#F6F9FC"
    }
    Rectangle {
        objectName: "gestureHudGlobalShortcuts"
        visible: !root.sceneActive
        x: 6; y: 346; width: parent.width - 12; height: 14; radius: 7
        color: "#F5F8FC"
        Text {
            anchors.fill: parent
            text: root.globalLabels.show_menu + " 提示 · " + root.globalLabels.switch_mode + " 切换 · " + root.globalLabels.window_selector + " 窗口"
            color: "#334760"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
    }
}
