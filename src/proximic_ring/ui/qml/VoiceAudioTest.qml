import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: meter
    required property var practice
    property bool attempted: false
    property bool compact: false
    readonly property string statusText: practice.recording ? (practice.level > 0.012 ? "正在接收声音" : "等待声音，请对着麦克风说话")
          : practice.audioTestPassed ? "已收到声音，收音可用" : attempted ? practice.message : "点击测试，查看实时声音波形"
    readonly property color statusColor: practice.audioTestPassed && !practice.recording ? theme.success : theme.muted
    implicitHeight: compact ? 40 : contents.implicitHeight + 24; implicitWidth: 260
    radius: 8; color: theme.sidebar
    FigmaTheme { id: theme }
    GridLayout {
        id: contents
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: meter.compact ? 8 : 12 }
        columns: meter.compact ? 4 : 5
        columnSpacing: 8; rowSpacing: 8
        FigmaIcon {
            Layout.row: 0; Layout.column: 0
            Layout.preferredWidth: meter.compact ? 16 : 20; Layout.preferredHeight: meter.compact ? 16 : 20
            symbol: "mic"; asset: meter.compact ? "../assets/figma/redesign-567-32897-microphone.svg" : ""
        }
        Label {
            visible: !meter.compact; Layout.row: 0; Layout.column: 1
            text: meter.practice.recording ? meter.practice.seconds.toFixed(1) + " s" : meter.practice.playing ? "回放中" : "收音测试"
            color: theme.muted; font.pixelSize: 12
        }
        Item { visible: !meter.compact; Layout.row: 0; Layout.column: 2; Layout.fillWidth: true }
        FigmaAction {
            objectName: "voiceAudioTestButton"
            Layout.row: 0; Layout.column: meter.compact ? 2 : 3
            Layout.fillWidth: meter.compact
            implicitHeight: meter.compact ? 24 : 28; quiet: !meter.compact
            leftPadding: meter.compact ? 8 : 16; rightPadding: leftPadding
            text: meter.practice.recording && meter.practice.mode === "audio" ? "停止" : "测试"
            enabled: !meter.practice.processing && !meter.practice.playing && (!meter.practice.recording || meter.practice.mode === "audio")
            onClicked: { meter.attempted = true; meter.practice.recording ? meter.practice.finish() : meter.practice.start("audio") }
        }
        FigmaAction {
            objectName: "voiceAudioPlaybackButton"
            Layout.row: 0; Layout.column: meter.compact ? 3 : 4
            visible: meter.practice.audioAvailable && !meter.practice.recording
            implicitHeight: meter.compact ? 24 : 28; quiet: !meter.compact
            leftPadding: meter.compact ? 8 : 16; rightPadding: leftPadding
            text: meter.practice.playing ? "停止" : "回放"
            Accessible.name: meter.practice.playing ? "停止回放" : "回放测试录音"
            enabled: !meter.practice.processing
            onClicked: meter.practice.play()
        }
        Item {
            objectName: "voiceAudioWaveform"
            Layout.row: meter.compact ? 0 : 1; Layout.column: meter.compact ? 1 : 0; Layout.columnSpan: meter.compact ? 1 : 5
            Layout.fillWidth: !meter.compact; Layout.preferredWidth: meter.compact ? 50 : 0; Layout.preferredHeight: meter.compact ? 24 : 32
            Row {
                anchors.fill: parent; spacing: meter.compact ? 4 : 3
                Repeater {
                    model: meter.compact ? 9 : 32
                    Rectangle {
                        required property int index
                        readonly property real sampleLevel: meter.compact
                            ? Math.max.apply(null, meter.practice.levels.slice(Math.floor(index * 32 / 9), Math.floor((index + 1) * 32 / 9)))
                            : meter.practice.levels[index] || 0
                        objectName: "voiceAudioWaveformBar" + index
                        anchors.verticalCenter: parent.verticalCenter
                        width: meter.compact ? 2 : (parent.width - 31 * parent.spacing) / 32
                        height: meter.compact ? 4 + sampleLevel * 20 : 2 + sampleLevel * 30; radius: width / 2
                        color: sampleLevel > 0 ? theme.primary : meter.compact ? theme.muted : theme.placeholder
                        opacity: meter.compact && sampleLevel === 0 ? 0.45 : 1
                        Behavior on height { NumberAnimation { duration: 65; easing.type: Easing.OutCubic } }
                    }
                }
            }
        }
        Label {
            objectName: "voiceAudioSignalStatus"
            visible: !meter.compact
            Layout.row: 2; Layout.column: 0; Layout.columnSpan: 5
            Layout.fillWidth: true; wrapMode: Text.Wrap
            text: meter.statusText
            color: meter.statusColor
            font.pixelSize: 12
        }
    }
}
