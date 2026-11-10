import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: chart
    required property var controller
    readonly property var timeline: controller.signalTimeline
    readonly property var points: timeline.points
    property real viewNow: timeline.clockNow()
    property real lower: -90
    property real upper: -40
    readonly property real windowMs: 60000
    readonly property bool animating: motion.running
    readonly property string rawColor: "#ADB8CA"
    readonly property string smoothedColor: "#315BDC"
    readonly property string baselineColor: "#299173"
    readonly property string thresholdColor: "#D99723"
    readonly property var series: [
        {key:"raw", label:"原始 RSSI", color:rawColor},
        {key:"smoothed", label:"平滑 RSSI", color:smoothedColor},
        {key:"baseline", label:"校准基线", color:baselineColor},
        {key:"threshold", label:"远离阈值", color:thresholdColor}
    ]
    // Rebuild only when telemetry changes; pointer motion searches this cache.
    readonly property var samples: points.filter(function(point) { return point.sample })
    readonly property var inspected: inspection.hovered ? nearestSample(inspection.point.position.x) : null
    objectName: "proximitySignalChart"
    spacing: 8
    FigmaTheme { id: theme }

    function stateColor(state) {
        if (state === "away") return "#CA8A17"
        if (state === "countdown" || state === "locking") return "#D95536"
        if (state === "locked") return "#BC354C"
        if (state === "protected") return "#258A89"
        if (state === "calibrating") return "#8663BC"
        if (state === "normal") return "#315BDC"
        return "#8A96A8"
    }
    function refresh() {
        viewNow = timeline.clockNow()
        var low = Infinity, high = -Infinity
        for (var i = 0; i < points.length; ++i) {
            var point = points[i]
            if (point.time < viewNow - windowMs) continue
            var values = [point.raw, point.smoothed, point.baseline, point.threshold]
            for (var j = 0; j < values.length; ++j) {
                if (typeof values[j] !== "number") continue
                low = Math.min(low, values[j]); high = Math.max(high, values[j])
            }
        }
        if (isFinite(low)) {
            upper = Math.ceil((high + 4) / 10) * 10
            lower = Math.min(Math.floor((low - 4) / 10) * 10, upper - 30)
        }
        plot.requestPaint()
    }
    function nearestSample(position) {
        if (!samples.length || inspectionArea.width <= 0) return null
        var start = viewNow - windowMs
        var target = start + Math.max(0, Math.min(1, position / inspectionArea.width)) * windowMs
        // Same time-based binary lookup as the IMU dashboard cursor.
        var lo = 0, hi = samples.length
        while (lo < hi) {
            var mid = (lo + hi) >> 1
            if (samples[mid].time < target) lo = mid + 1
            else hi = mid
        }
        var before = lo > 0 ? samples[lo - 1] : null
        var after = lo < samples.length ? samples[lo] : null
        if (before && before.time < start) before = null
        var closest = !before ? after : !after ? before
            : target - before.time <= after.time - target ? before : after
        // Do not snap across a missing-data interval or into expired history.
        return closest && closest.time >= start && closest.time <= viewNow
            && Math.abs(closest.time - target) <= 1500 ? closest : null
    }
    function reading(value) { return typeof value === "number" ? value.toFixed(1) : "—" }
    function describe(point) {
        if (point.state === "away") return "弱信号确认 " + point.weak_samples + " / " + point.required
        if (point.state === "countdown") return "远离已确认 · " + point.countdown + " 秒后锁屏"
        if (point.state === "locked") return point.reason === "weak_signal" ? "已锁屏 · 远离触发" : "已锁屏 · 系统或手动"
        return ({normal:"未达到远离条件", protected:"操作保护中", calibrating:"手动校准中",
                 locking:"正在锁屏", disconnected:"已断连", stale:"等待新读数", paused:"监测已暂停", unknown:"等待判定"})[point.state] || ""
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 8
        Rectangle {
            Layout.preferredWidth: stateText.implicitWidth + 18
            implicitHeight: 24; radius: 6
            color: Qt.rgba(stateDot.r, stateDot.g, stateDot.b, 0.10)
            readonly property color stateDot: chart.stateColor(chart.timeline.state)
            Label {
                id: stateText
                objectName: "proximityChartState"
                anchors.centerIn: parent
                text: chart.timeline.stateLabel; color: parent.stateDot; font.pixelSize: 12
            }
        }
        Item { Layout.fillWidth: true }
        Label { text: "最近 60 秒"; color: theme.muted; font.pixelSize: 11 }
    }
    Rectangle {
        Layout.fillWidth: true; implicitHeight: 180
        color: "#F7F9FD"; radius: 8
        Canvas {
            id: plot
            objectName: "proximitySignalCanvas"
            anchors.fill: parent
            antialiasing: true
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var left = 38, right = width - 12, top = 12, bottom = height - 25
                var start = chart.viewNow - chart.windowMs
                function x(t) { return left + (t - start) / chart.windowMs * (right - left) }
                function y(v) { return bottom - (v - chart.lower) / (chart.upper - chart.lower) * (bottom - top) }
                ctx.font = "10px sans-serif"
                ctx.lineWidth = 1
                for (var row = 0; row <= 4; ++row) {
                    var value = chart.lower + (chart.upper - chart.lower) * row / 4
                    var yy = y(value)
                    ctx.strokeStyle = "#E4E9F1"; ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(right, yy); ctx.stroke()
                    ctx.fillStyle = "#8792A2"; ctx.textAlign = "right"; ctx.fillText(value.toFixed(0), left - 7, yy + 3)
                }
                for (var column = 0; column <= 4; ++column) {
                    ctx.textAlign = column === 0 ? "left" : column === 4 ? "right" : "center"
                    ctx.fillText(column === 4 ? "现在" : (60 - column * 15) + "s", left + (right - left) * column / 4, height - 7)
                }
                var rows = chart.points
                ctx.save(); ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip()
                // Each band and step uses its captured values, never today's baseline.
                for (var k = 0; k < rows.length; ++k) {
                    var point = rows[k], end = k + 1 < rows.length ? rows[k + 1].time : chart.viewNow
                    if (end < start) continue
                    var a = Math.max(left, x(point.time)), b = Math.min(right, x(end))
                    if (point.state === "away" || point.state === "countdown" || point.state === "locking" || point.state === "locked" || point.state === "protected" || point.state === "calibrating") {
                        ctx.fillStyle = chart.stateColor(point.state)
                        if (point.state === "protected" || point.state === "calibrating") {
                            ctx.globalAlpha = 0.08
                            ctx.fillRect(a, top, b - a, bottom - top)
                        }
                        ctx.globalAlpha = 0.7
                        ctx.fillRect(a, bottom - 3, b - a, 3); ctx.globalAlpha = 1
                    }
                }
                // Draw series above the bands at full opacity with fixed colors.
                function stepLine(key, color, dash) {
                    ctx.strokeStyle = color; ctx.lineWidth = 1.4; ctx.setLineDash(dash)
                    ctx.beginPath()
                    for (var i = 0; i < rows.length; ++i) {
                        var p = rows[i], next = i + 1 < rows.length ? rows[i + 1] : null
                        if (typeof p[key] !== "number") continue
                        ctx.moveTo(x(p.time), y(p[key])); ctx.lineTo(x(next ? next.time : chart.viewNow), y(p[key]))
                        if (next && typeof next[key] === "number") ctx.lineTo(x(next.time), y(next[key]))
                    }
                    ctx.stroke(); ctx.setLineDash([])
                }
                stepLine("baseline", chart.baselineColor, [3, 4])
                stepLine("threshold", chart.thresholdColor, [6, 4])
                function signalLine(key, color, emphasized) {
                    var previous = null
                    ctx.strokeStyle = color
                    ctx.lineWidth = emphasized ? 2.2 : 1
                    for (var i = 0; i < rows.length; ++i) {
                        var p = rows[i]
                        if (p.state === "paused" || p.state === "stale" || p.state === "disconnected") previous = null
                        if (!p.sample) continue
                        if (previous && p.time - previous.time <= 3000) {
                            ctx.beginPath(); ctx.moveTo(x(previous.time), y(previous[key])); ctx.lineTo(x(p.time), y(p[key])); ctx.stroke()
                        }
                        previous = p
                    }
                    if (previous && chart.viewNow - previous.time <= 3000) {
                        ctx.fillStyle = color
                        ctx.beginPath(); ctx.arc(x(previous.time), y(previous[key]), emphasized ? 3 : 1.5, 0, 2 * Math.PI); ctx.fill()
                    }
                }
                signalLine("raw", chart.rawColor, false)
                signalLine("smoothed", chart.smoothedColor, true)
                var previousState = ""
                for (var m = 0; m < rows.length; ++m) {
                    var event = rows[m]
                    if (event.state !== previousState && (event.state === "countdown" || event.state === "locked")) {
                        var at = x(event.time)
                        if (at >= left && at <= right) {
                            ctx.strokeStyle = chart.stateColor(event.state); ctx.globalAlpha = 0.5
                            ctx.beginPath(); ctx.moveTo(at, top); ctx.lineTo(at, bottom); ctx.stroke(); ctx.globalAlpha = 1
                            ctx.fillStyle = chart.stateColor(event.state); ctx.textAlign = at > right - 65 ? "right" : "left"
                            ctx.fillText(event.state === "locked" ? "已锁屏" : "远离 · 倒计时", at, top + (event.state === "locked" ? 23 : 10))
                        }
                    }
                    previousState = event.state
                }
                ctx.restore()
            }
        }
        // Scene-graph markers follow pointer events directly, independently of
        // the scrolling canvas paint. This item never grabs clicks or focus.
        Item {
            id: inspectionArea
            objectName: "proximityChartInspectionArea"
            x: 38; y: 12; width: parent.width - 50; height: parent.height - 37
            clip: true
            HoverHandler { id: inspection; cursorShape: Qt.CrossCursor }
            Item {
                visible: chart.inspected !== null
                x: chart.inspected ? (chart.inspected.time - chart.viewNow + chart.windowMs) / chart.windowMs * parent.width : 0
                height: parent.height
                Rectangle { width: 1; height: parent.height; color: "#8392AA"; opacity: 0.55 }
                Repeater {
                    model: chart.series
                    Rectangle {
                        required property var modelData
                        readonly property var value: chart.inspected ? chart.inspected[modelData.key] : null
                        visible: typeof value === "number"
                        x: -4; y: inspectionArea.height * (chart.upper - (value || 0)) / (chart.upper - chart.lower) - 4
                        width: 8; height: 8; radius: 4
                        color: "white"; border.color: modelData.color; border.width: 2
                    }
                }
            }
        }
    }
    // Reserve space for inspection: no popup over the chart, live values or
    // settings, and no layout shift when entering/leaving the plot.
    Rectangle {
        objectName: "proximityChartReadout"
        Layout.fillWidth: true; implicitHeight: readout.implicitHeight + 16
        color: "#F7F9FD"; border.color: "#E4E9F1"; radius: 8
        ColumnLayout {
            id: readout
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8 }
            spacing: 6
            RowLayout {
                Layout.fillWidth: true; spacing: 12
                Label {
                    objectName: "proximityChartInspectionTime"
                    text: chart.inspected ? Math.max(0, (chart.viewNow - chart.inspected.time) / 1000).toFixed(1) + " 秒前 · dBm"
                        : inspection.hovered ? "此处无有效采样" : "移动到曲线查看读数"
                    color: theme.muted; font.pixelSize: 11
                }
                Label {
                    objectName: "proximityChartInspectionState"
                    Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight
                    text: chart.inspected ? chart.describe(chart.inspected) : "dBm"
                    color: chart.inspected ? chart.stateColor(chart.inspected.state) : theme.muted
                    font.pixelSize: 11
                }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 12
                Repeater {
                    model: chart.series
                    ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 2
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 5
                            Rectangle { implicitWidth: 12; implicitHeight: 2; color: modelData.color }
                            Label { Layout.fillWidth: true; text: modelData.label; color: theme.muted; font.pixelSize: 11 }
                        }
                        Label {
                            objectName: "proximityChartInspection_" + modelData.key
                            Layout.fillWidth: true
                            text: chart.inspected ? chart.reading(chart.inspected[modelData.key]) : "—"
                            color: theme.text; font.pixelSize: 14; font.weight: Font.Medium
                        }
                    }
                }
            }
        }
    }
    Connections { target: chart.timeline; function onChanged() { chart.refresh() } }
    FrameAnimation {
        id: motion
        running: chart.visible && chart.points.length > 0
            && (chart.controller.enabled || chart.controller.calibrating || chart.viewNow - chart.points[chart.points.length - 1].time < chart.windowMs)
        onTriggered: { chart.viewNow = chart.timeline.clockNow(); plot.requestPaint() }
    }
    onVisibleChanged: if (visible) refresh()
    Component.onCompleted: refresh()
}
