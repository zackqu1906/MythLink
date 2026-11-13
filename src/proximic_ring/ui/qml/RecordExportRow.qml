import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: row
    required property var exporter
    property string title: ""
    property string description: ""
    property string rowName: ""
    property string viewName: ""
    property string exportName: ""
    property string statusName: ""
    property string folderName: ""
    signal viewRequested()
    signal exportRequested()
    spacing: 8
    PreferenceRow {
        objectName: row.rowName
        Layout.fillWidth: true
        linked: true; title: row.title; description: row.description
        RowLayout {
            spacing: 8
            FigmaAction {
                objectName: row.viewName
                text: "查看"; implicitWidth: 72; implicitHeight: 36
                Accessible.name: "查看" + row.title
                onClicked: row.viewRequested()
            }
            FigmaAction {
                objectName: row.exportName
                text: row.exporter.busy ? "导出中…" : "导出"; implicitWidth: 88; implicitHeight: 36
                Accessible.name: "导出" + row.title
                enabled: !row.exporter.busy
                onClicked: row.exportRequested()
            }
        }
    }
    UiNotice {
        objectName: row.statusName
        Layout.fillWidth: true; Layout.leftMargin: 16
        visible: text.length > 0
        text: row.exporter.message; warning: row.exporter.error; wrapMode: Text.Wrap
    }
    FigmaAction {
        objectName: row.folderName
        Layout.leftMargin: 4
        visible: row.exporter.path.length > 0
        text: "打开导出文件夹"; quiet: true; implicitHeight: 28
        onClicked: row.exporter.openFolder()
    }
}
