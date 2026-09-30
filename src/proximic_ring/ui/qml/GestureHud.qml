import QtQuick

Item {
    id: root
    width: 280; height: 280
    property string mode: "input"
    property string notice: ""
    property bool preview: false
    property var sceneActions: []
    property bool inputFieldsAvailable: true
    property string inputFieldsHint: "当前窗口未提供文本框"
    property real entranceTime: 0
    readonly property bool animationRunning: entrance.running
    readonly property bool inputMode: mode === "input"
    readonly property color accent: inputMode ? "#56DDF4" : "#B7A2FF"
    readonly property real orbitRadius: 86
    readonly property real centerX: 140
    readonly property real centerY: 126
    readonly property real hubProgress: bezier(progress(0, 620), 0.16, 1, 0.3, 1)
    readonly property real orbitProgress: bezier(progress(60, 880), 0.35, 0, 0.15, 1)
    readonly property real innerProgress: bezier(progress(420, 700), 0.35, 0, 0.15, 1)
    readonly property var satellites: inputMode ? [
        {symbol: "up", tone: "cyan", label: "上滑 · 发送", detail: "", width: 74},
        {symbol: "mic", tone: "indigo", label: "Tap · 语音输入", detail: "再 Tap · 结束本句", width: 84},
        {symbol: "edit", tone: "indigo", label: "Tap + 右滑", detail: "语音编辑\n再 Tap · 结束本句", width: 84},
        {symbol: "down", tone: "cyan", label: "下滑 · 选择输入框", detail: inputFieldsHint, width: 114},
        {symbol: "apps", tone: "amber", label: "握拳 · 切换应用", detail: "", width: 86},
        {symbol: "undo", tone: "cyan", label: "左滑 · 撤销", detail: "", width: 70}
    ] : [
        {symbol: "up", tone: "violet", label: "上滑 · 向上滚动", detail: "", width: 94},
        {symbol: "down", tone: "violet", label: "下滑 · 向下滚动", detail: "", width: 86},
        {symbol: "apps", tone: "amber", label: "握拳 · 切换应用", detail: "", width: 86}
    ]

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
        visible: root.sceneActions.length === 0
        width: 280; height: 280
        scale: Math.min(root.width, root.height) / 280
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
            x: root.centerX - 48; y: root.centerY - 48
            width: 96; height: 96; radius: 48
            opacity: root.hubProgress
            scale: 0.82 + 0.18 * root.hubProgress
            color: root.inputMode ? "#F20F1C33" : "#F214142B"
            border.width: 1; border.color: root.inputMode ? "#AA45C6E4" : "#AAAFA0E3"
            GestureHudIcon {
                x: 31; y: 12; width: 34; height: 34
                symbol: root.inputMode ? "keyboard" : "scroll"; ink: root.accent
            }
            Text {
                x: 0; y: 51; width: parent.width
                text: root.inputMode ? "输入模式" : "操作模式"
                font.pixelSize: 13; font.weight: Font.DemiBold
                color: "#F2F5FF"; horizontalAlignment: Text.AlignHCenter
            }
            Rectangle {
                x: 7; y: 73; width: 82; height: 16; radius: 8
                color: "#252C40"; border.color: "#43516A"; border.width: 0.6
                GestureHudIcon { x: 5; y: 3; width: 10; height: 10; symbol: "pinch"; ink: "#CFD8ED" }
                Text {
                    x: 19; height: parent.height; width: 58
                    text: "中指捏合切换"; font.pixelSize: 8
                    color: "#CFD8ED"; verticalAlignment: Text.AlignVCenter
                }
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
                tint: modelData.tone === "amber" ? "#F0B153" : modelData.tone === "cyan" ? "#56DDF4" :
                      modelData.tone === "indigo" ? "#929CEB" : "#B7A2FF"
                label: modelData.label; detail: modelData.detail
                labelWidth: modelData.width
                labelOffset: Math.sin(finalAngle*Math.PI/180) > 0.5 ? 19 :
                             Math.sin(finalAngle*Math.PI/180) < -0.5 ? -10 : 0
                // Text waits until the spiral is almost at its resting radius.
                labelOpacity: root.clamp((t-0.68)/0.32)
                available: !(root.inputMode && modelData.symbol === "down") || root.inputFieldsAvailable
            }
        }
        Text {
            x: 25; y: 265; width: 230
            text: root.notice || (root.preview ? "食指捏合 · 唤起提示  /  预览" : "食指捏合 · 唤起提示")
            opacity: root.notice ? 1 : root.progress(900, 300)
            color: root.notice ? "#FFD47C" : "#A8B8CD"; font.pixelSize: root.notice ? 10 : 8
            horizontalAlignment: Text.AlignHCenter
            style: Text.Outline; styleColor: "#172235"
        }
    }
    Rectangle {
        visible: root.sceneActions.length > 0
        anchors.fill: parent; radius: 18; color: "#E91B2233"; border.color: "#536487"
        Text {
            x: 16; y: 16; width: parent.width - 32
            text: root.sceneActions.length && root.sceneActions[0].application ? root.sceneActions[0].application + " · 放映" : "放映"
            color: "#EAF1FF"; font.pixelSize: 16; font.weight: Font.DemiBold; elide: Text.ElideRight
        }
        Text { x: 16; y: 42; text: "场景专属动作 · 其余沿用默认"; color: "#A8B8CD"; font.pixelSize: 10 }
        Grid {
            x: 12; y: 67; columns: 3; spacing: 6
            Repeater {
                model: root.sceneActions
                Rectangle {
                    required property var modelData
                    required property int index
                    objectName: "sceneGesture_" + index
                    width: 81; height: 52; radius: 8; color: "#344368"
                    Text { x: 7; y: 8; width: parent.width - 14; text: modelData.gesture; color: "#BED0F6"; font.pixelSize: 11; elide: Text.ElideRight }
                    Text { x: 7; y: 26; width: parent.width - 14; text: modelData.action; color: "white"; font.pixelSize: 10; elide: Text.ElideRight }
                }
            }
        }
        Text { x: 16; y: 248; width: parent.width - 32; text: "食指捏合 · 提示    食中捏合 · 切换模式"; color: "#A8B8CD"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter }
    }
}
