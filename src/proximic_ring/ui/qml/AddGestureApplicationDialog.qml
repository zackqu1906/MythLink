import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaDialog {
    id: dialog
    required property var catalog
    signal applicationAdded(string bundle)
    FigmaTheme { id: theme }
    property bool showAll: false
    readonly property string query: appSearch.text.trim().toLowerCase()
    readonly property var runningApps: catalog.candidates.filter(function(app) { return app.running === true })
    readonly property bool showingRunning: !query && !showAll && runningApps.length > 0
    readonly property var results: showingRunning ? runningApps : catalog.candidates.filter(function(app) {
        return (app.search || (app.label + " " + app.value)).toLowerCase().indexOf(dialog.query) >= 0
    })
    objectName: "addGestureApplicationDialog"
    title: "添加应用"
    padding: 20; topPadding: 0; bottomPadding: 0
    header: Label { text: dialog.title; leftPadding: 20; rightPadding: 20; topPadding: 20; bottomPadding: 12; font.family: theme.family; font.pixelSize: 18; font.weight: Font.Medium; color: theme.text }
    modal: true
    onAboutToShow: { appSearch.text = ""; showAll = false; catalog.refreshApplications() }
    onOpened: appSearch.forceActiveFocus()
    footer: Item {
        implicitHeight: 72
        FigmaAction { anchors.fill: parent; anchors.leftMargin: 20; anchors.rightMargin: 20; anchors.topMargin: 12; anchors.bottomMargin: 20; font.pixelSize: 16; text: "关闭"; onClicked: dialog.close() }
    }
    background: Rectangle { color: theme.surface; radius: 12; border.color: theme.line }
    contentItem: ColumnLayout {
        spacing: 12
        UiTextField {
            id: appSearch
            objectName: "installedApplicationSearch"
            Layout.fillWidth: true; implicitHeight: 40
            hintText: "搜索本机应用"; placeholderText: hintText; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium
            leftPadding: 16; rightPadding: 16; color: theme.text
            background: Rectangle { radius: 8; color: theme.background; border.color: appSearch.activeFocus ? theme.primary : theme.line }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { objectName: "applicationResultsHeading"; text: dialog.query ? "搜索结果" : dialog.showingRunning ? "当前已打开" : "本机应用"; color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
            Label { text: dialog.results.length + " 个"; color: theme.muted; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            FigmaAction { objectName: "showAllInstalledApplications"; visible: dialog.showingRunning; text: "所有应用"; quiet: true; implicitHeight: 30; onClicked: dialog.showAll = true }
            FigmaAction { text: dialog.catalog.listBusy ? "搜索中…" : "刷新"; quiet: true; implicitHeight: 30; enabled: !dialog.catalog.listBusy; onClicked: dialog.catalog.refreshApplications() }
        }
        UiNotice { Layout.fillWidth: true; visible: text.length > 0; text: dialog.catalog.listMessage; wrapMode: Text.Wrap; font.pixelSize: 12 }
        ListView {
            id: applicationList
            objectName: "availableGestureApplications"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredHeight: Math.min(280, Math.max(88, contentHeight))
            clip: true; spacing: 12
            model: dialog.results
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }
            delegate: Rectangle {
                required property var modelData
                width: applicationList.width; height: 44; radius: 8; color: theme.surface
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 4; spacing: 12
                    ApplicationIcon {
                        Layout.preferredWidth: 28; Layout.preferredHeight: 28
                        bundle: modelData.value; applicationPath: modelData.path || ""; label: modelData.label
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 4
                        Label { Layout.fillWidth: true; text: modelData.label; color: theme.text; font.pixelSize: 16; font.weight: Font.Medium; elide: Text.ElideRight }
                    }
                    FigmaAction {
                        readonly property bool added: dialog.catalog.apps.some(function(app) { return app.value === modelData.value })
                        objectName: "addGestureApp_" + modelData.value
                        text: added ? "选择" : "添加"; quiet: true; implicitHeight: 32; contentColor: theme.primary
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
