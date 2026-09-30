import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    required property var statistics
    property bool todaySelected: true
    readonly property var summary: statistics.summary
    readonly property var totals: todaySelected ? summary.today : summary.total
    UiTheme { id: theme }
    spacing: 8
    Component.onCompleted: statistics.active = visible
    onVisibleChanged: if (statistics) statistics.active = visible

    function countText(value) {
        if (value >= 100000000) return (value / 100000000).toFixed(1).replace(/\.0$/, "") + "亿"
        if (value >= 10000) return (value / 10000).toFixed(1).replace(/\.0$/, "") + "万"
        return String(value)
    }
    function duration(seconds) {
        var divisor = seconds >= 3600 ? 3600 : seconds >= 60 ? 60 : 1
        return {value: seconds > 0 && seconds < 0.1 ? "<0.1" : (seconds / divisor).toFixed(1).replace(/\.0$/, ""),
                unit: divisor === 3600 ? "小时" : divisor === 60 ? "分钟" : "秒"}
    }
    function known(metric, values) {
        if (metric === "voiceSeconds") return !values.records || values.missingDuration < values.records
        if (metric === "inputCharacters") return values.missingText === 0 || values.inputCharacters > 0
        return true
    }
    function incomplete(metric, values) {
        return metric === "voiceSeconds" ? values.missingDuration > 0 : metric === "inputCharacters" && values.missingText > 0
    }
    function footer(metric, unit) {
        if (!totals.records) return todaySelected ? (summary.undatedRecords ? "今日暂无可统计记录" : "今日暂无语音记录") : "暂无语音记录"
        if (incomplete(metric, totals)) return metric === "voiceSeconds" ? "部分记录缺少时长" : "部分输入结果未保存"
        if (!todaySelected) return "基于保留的记录"
        var previous = summary.yesterday
        if (!previous.records) return "昨日暂无语音记录"
        if (incomplete(metric, previous)) return "昨日数据不完整"
        var delta = totals[metric] - previous[metric]
        if (Math.abs(delta) < 0.0005) return "与昨日持平"
        var formatted = metric === "voiceSeconds" ? duration(Math.abs(delta)) : {value: countText(Math.abs(delta)), unit: unit}
        return "较昨日 " + (delta > 0 ? "+" : "−") + formatted.value + " " + formatted.unit
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 4
        Label { text: "使用统计"; color: theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
        Item { Layout.fillWidth: true }
        UiAction { objectName: "homeStatsToday"; text: "今日"; primary: panel.todaySelected; quiet: !primary; implicitHeight: 28; topPadding: 0; bottomPadding: 0; onClicked: panel.todaySelected = true }
        UiAction { objectName: "homeStatsAll"; text: "全部记录"; primary: !panel.todaySelected; quiet: !primary; implicitHeight: 28; topPadding: 0; bottomPadding: 0; onClicked: panel.todaySelected = false }
        UiAction { objectName: "homeStatsInfo"; text: "说明"; quiet: true; implicitHeight: 28; topPadding: 0; bottomPadding: 0; onClicked: explanation.open() }
    }
    RowLayout {
        Layout.fillWidth: true; spacing: 12
        Repeater {
            model: [{key: "inputCharacters", label: "输入字数", unit: "字", icon: "history", tint: "#F2F3FC"},
                    {key: "voiceSeconds", label: "语音使用时长", unit: "秒", icon: "clock", tint: "#EDF5FF"},
                    {key: "completedOperations", label: "完成语音操作", unit: "次", icon: "operation", tint: "#F0F3FF"}]
            delegate: Rectangle {
                id: card
                required property var modelData
                readonly property bool available: panel.known(modelData.key, panel.totals)
                readonly property var formatted: modelData.key === "voiceSeconds" ? panel.duration(panel.totals.voiceSeconds)
                    : {value: panel.countText(panel.totals[modelData.key]), unit: modelData.unit}
                objectName: "homeStat_" + modelData.key
                Layout.fillWidth: true; Layout.preferredWidth: 160; Layout.minimumWidth: 0; Layout.preferredHeight: 216
                radius: 16; color: modelData.tint
                Accessible.role: Accessible.StaticText
                Accessible.name: modelData.label + "：" + (available ? panel.totals[modelData.key] + " " + modelData.unit : "数据不完整")
                HoverHandler { id: hover }
                ToolTip.visible: hover.hovered
                ToolTip.delay: 400
                ToolTip.text: card.Accessible.name
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 8
                    Rectangle {
                        width: 34; height: 34; radius: 10; color: "#E3E9FC"
                        UiIcon { anchors.centerIn: parent; width: 19; height: 19; symbol: card.modelData.icon }
                    }
                    Item {
                        Layout.fillWidth: true
                        implicitHeight: valueLabel.implicitHeight
                        Label {
                            id: valueLabel
                            objectName: "homeStatValue_" + card.modelData.key
                            width: Math.min(implicitWidth, Math.max(0, parent.width - unitLabel.implicitWidth - 4))
                            text: card.available ? card.formatted.value : "—"
                            font.pixelSize: 36; font.weight: Font.DemiBold; color: theme.text
                            fontSizeMode: Text.Fit; minimumPixelSize: 20; elide: Text.ElideRight
                        }
                        Label {
                            id: unitLabel
                            x: valueLabel.width + 4
                            anchors.bottom: valueLabel.bottom; anchors.bottomMargin: 7
                            text: card.formatted.unit; color: theme.muted; font.pixelSize: 12
                        }
                    }
                    Label { Layout.fillWidth: true; text: card.modelData.label; color: theme.muted; font.pixelSize: 13 }
                    Item { Layout.fillHeight: true }
                    Label {
                        objectName: "homeStatHint_" + card.modelData.key
                        Layout.fillWidth: true; Layout.preferredHeight: 28
                        text: panel.footer(card.modelData.key, card.modelData.unit)
                        color: theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap
                    }
                }
            }
        }
    }

    Dialog {
        id: explanation
        objectName: "homeStatisticsExplanation"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, parent.width - 48)
        modal: true; popupType: Popup.Item
        title: "统计说明"
        padding: 24
        background: Rectangle { color: theme.surface; radius: 16; border.color: theme.line }
        header: Label {
            text: explanation.title
            leftPadding: 24; rightPadding: 24; topPadding: 24; bottomPadding: 4
            color: theme.text; font.pixelSize: 22; font.weight: Font.DemiBold
        }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
        contentItem: ColumnLayout {
            spacing: 16
            Label { Layout.fillWidth: true; text: "基于当前保留的语音记录。删除或清空历史后，统计同步调整。"; color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap }
            Repeater {
                model: [
                    {title: "输入字数", detail: "仅计成功听写且未撤回的结果，按非空白字符计数，包含标点与字母。编辑后的整段文字不重复计入。"},
                    {title: "语音使用时长", detail: "汇总已保存录音的实际时长，包含取消或未识别成功的录音。缺少时长的记录不计入。"},
                    {title: "完成语音操作", detail: "成功听写或编辑，每条记录计一次；撤回、失败或取消不计入。此处不统计应用快捷键操作。"},
                    {title: "今日与昨日", detail: "按记录时间和电脑本地日期划分。今日数字与昨日全天比较，全部记录包含更早的历史。"}
                ]
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true; spacing: 5
                    Label { text: modelData.title; color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Label { Layout.fillWidth: true; text: modelData.detail; color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap }
                }
            }
            Label {
                Layout.fillWidth: true; visible: panel.summary.undatedRecords > 0
                text: panel.summary.undatedRecords + " 条记录缺少有效时间，仅计入全部记录。"
                color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
            }
        }
        footer: DialogButtonBox {
            leftPadding: 24; rightPadding: 24; topPadding: 0; bottomPadding: 20
            UiAction { objectName: "closeHomeStatisticsExplanation"; text: "知道了"; primary: true; onClicked: explanation.close() }
        }
    }
}
