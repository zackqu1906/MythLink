import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    required property var catalog
    signal applicationAdded(string bundle)
    UiTheme { id: theme }
    property bool showAll: false
    readonly property string query: appSearch.text.trim().toLowerCase()
    readonly property var runningApps: catalog.candidates.filter(function(app) { return app.running === true })
    readonly property bool showingRunning: !query && !showAll && runningApps.length > 0
    readonly property var results: showingRunning ? runningApps : catalog.candidates.filter(function(app) {
        return (app.search || (app.label + " " + app.value)).toLowerCase().indexOf(dialog.query) >= 0
    })
    objectName: "addGestureApplicationDialog"
    title: "添加应用"
    modal: true
    onAboutToShow: { appSearch.text = ""; showAll = false; catalog.refreshApplications() }
    onOpened: appSearch.forceActiveFocus()
    footer: DialogButtonBox {
        UiAction { text: "关闭"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
        onRejected: dialog.close()
    }
    background: Rectangle { color: theme.surface; radius: 16; border.color: theme.line }
    contentItem: ColumnLayout {
        spacing: 14
        Label { Layout.fillWidth: true; text: "搜索本机已安装的应用，也可以添加尚未打开的应用。"; wrapMode: Text.Wrap; color: theme.muted; font.pixelSize: 13 }
        UiTextField {
            id: appSearch
            objectName: "installedApplicationSearch"
            Layout.fillWidth: true; implicitHeight: 46
            hintText: "搜索应用名称"; font.pixelSize: 14
            leftPadding: 40; rightPadding: 14; color: theme.text
            background: Rectangle { radius: 10; color: theme.surface; border.color: appSearch.activeFocus ? theme.primary : theme.line }
            UiIcon { x: 14; anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18; symbol: "search" }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { objectName: "applicationResultsHeading"; text: dialog.query ? "搜索结果" : dialog.showingRunning ? "当前已打开" : "本机应用"; color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
            Label { text: dialog.results.length + " 个"; color: theme.muted; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            UiAction { objectName: "showAllInstalledApplications"; visible: dialog.showingRunning; text: "所有应用"; quiet: true; implicitHeight: 30; onClicked: dialog.showAll = true }
            UiAction { text: dialog.catalog.listBusy ? "搜索中…" : "刷新"; quiet: true; implicitHeight: 30; enabled: !dialog.catalog.listBusy; onClicked: dialog.catalog.refreshApplications() }
        }
        UiNotice { Layout.fillWidth: true; visible: text.length > 0; text: dialog.catalog.listMessage; wrapMode: Text.Wrap; font.pixelSize: 12 }
        ListView {
            id: applicationList
            objectName: "availableGestureApplications"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 8
            model: dialog.results
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }
            delegate: Rectangle {
                required property var modelData
                width: applicationList.width; height: 70; radius: 10; color: theme.subtle
                RowLayout {
                    anchors.fill: parent; anchors.margins: 10; spacing: 12
                    ApplicationIcon {
                        Layout.preferredWidth: 44; Layout.preferredHeight: 44
                        bundle: modelData.value; applicationPath: modelData.path || ""; label: modelData.label
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 4
                        Label { Layout.fillWidth: true; text: modelData.label; color: theme.text; font.pixelSize: 14; elide: Text.ElideRight }
                        Label { Layout.fillWidth: true; text: modelData.running ? "当前已打开" : "已安装"; color: theme.muted; font.pixelSize: 11 }
                    }
                    UiAction {
                        readonly property bool added: dialog.catalog.apps.some(function(app) { return app.value === modelData.value })
                        objectName: "addGestureApp_" + modelData.value
                        text: added ? "选择" : "添加"; primary: !added
                        onClicked: {
                            if (dialog.catalog.addApplication(modelData.value)) {
                                dialog.applicationAdded(modelData.value)
                                dialog.close()
                            }
                        }
                    }
                }
            }
            Label {
                anchors.centerIn: parent; width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                visible: applicationList.count === 0
                text: dialog.catalog.listBusy ? "正在搜索本机应用…" : dialog.query ? "没有找到匹配的应用" : "没有找到应用，可刷新重试"
                color: theme.muted; font.pixelSize: 13
            }
        }
    }
}
