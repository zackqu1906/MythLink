import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    required property var controller
    property string namePrefix: "proximityLive"
    property bool showChart: false
    readonly property var calibrationBaseline: controller.savedCalibration.baseline
    readonly property var lockThreshold: typeof controller.learnedThresholds.lock === "number"
        ? controller.learnedThresholds.lock : typeof calibrationBaseline === "number" ? calibrationBaseline - 8 : undefined
    spacing: 10
    FigmaTheme { id: theme }

    function dbm(value) {
        return typeof value === "number" && isFinite(value) ? value.toFixed(1) + " dBm" : "—"
    }

    Label {
        text: "实时距离信号"; color: theme.text; font.pixelSize: 16; font.weight: Font.Medium
    }
    GridLayout {
        Layout.fillWidth: true
        columns: panel.width >= 600 ? 4 : 2
        columnSpacing: 16; rowSpacing: 12
        Repeater {
            model: [
                { key: "Raw", title: "当前 RSSI · 原始", value: panel.controller.rawRssi === "—" ? "—" : panel.controller.rawRssi + " dBm" },
                { key: "Smoothed", title: "判断 RSSI · 平滑", value: panel.controller.rssi === "—" ? "—" : panel.controller.rssi + " dBm" },
                { key: "Baseline", title: "校准基线 · 桌前", value: panel.dbm(panel.calibrationBaseline) },
                { key: "Threshold", title: "远离阈值 · 基线 − 8", value: panel.dbm(panel.lockThreshold) }
            ]
            ColumnLayout {
                required property var modelData
                Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 4
                Label { Layout.fillWidth: true; text: modelData.title; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
                Label {
                    objectName: panel.namePrefix + modelData.key
                    Layout.fillWidth: true
                    text: modelData.value; color: theme.text; font.pixelSize: 20; font.weight: Font.Medium
                    Accessible.name: modelData.title + "：" + modelData.value
                }
            }
        }
    }
    ProximitySignalChart {
        objectName: panel.namePrefix + "Chart"
        Layout.fillWidth: true
        visible: panel.showChart
        controller: panel.controller
    }
    Label {
        objectName: panel.namePrefix + "Decision"
        Layout.fillWidth: true; Layout.preferredWidth: 1
        text: "信号每次回传时刷新。平滑值连续 " + panel.controller.options.awaySamples
            + " 次低于远离阈值后，进入 5 秒锁屏倒计时；近期操作会暂缓锁屏。"
        color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
    }
    Label {
        objectName: panel.namePrefix + "Learning"
        Layout.fillWidth: true; Layout.preferredWidth: 1
        text: panel.controller.baselineLearningStatus
        color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
    }
}
