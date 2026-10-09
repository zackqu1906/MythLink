import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ScrollView {
    id: page
    required property var controller
    UiTheme { id: theme }
    objectName: "gesturesPage"
    property bool applicationScope: false
    property string selectedGesture: "circle-clockwise"
    readonly property var catalog: controller.appGestures.catalog
    readonly property string selectedApp: catalog.selectedApp
    readonly property bool sceneScope: applicationScope && catalog.selectedScene !== "regular"
    readonly property var selectedApplication: catalog.apps.filter(function(app) { return app.value === page.selectedApp })[0] || null
    property string managementFeedback: ""
    onSelectedAppChanged: {
        managementFeedback = ""
        if (!selectedApp) applicationScope = false
    }
    function selectApplication(bundle) {
        managementFeedback = ""
        applicationScope = true
        if (selectedApp !== bundle) catalog.selectApplication(bundle)
    }
    function addApplication() { addDialog.open() }
    readonly property var gestures: [
        {key: "circle-clockwise", title: "顺时针旋转", row: 0, col: 0},
        {key: "clench", title: "握拳", row: 0, col: 1},
        {key: "circle-counterclockwise", title: "逆时针旋转", row: 0, col: 2},
        {key: "swipe-up", title: "上滑", row: 1, col: 1},
        {key: "swipe-left", title: "左滑", row: 2, col: 0},
        {key: "tap", title: "点击 Tap", row: 2, col: 1},
        {key: "swipe-right", title: "右滑", row: 2, col: 2},
        {key: "swipe-down", title: "下滑", row: 3, col: 1},
        {key: "index-pinch", title: "食指捏合", row: 4, col: 0},
        {key: "middle-pinch", title: "食中捏合", row: 4, col: 1},
        {key: "snap", title: "响指", row: 4, col: 2}
    ]
    readonly property string selectedTitle: gestures.filter(function(g) { return g.key === selectedGesture })[0].title
    function description(key) {
        var occupied = catalog.globalOccupancy[key]
        if (occupied) return occupied
        if (controller.appGestures.inputSourceGesture === key) return "切换输入法"
        if (applicationScope) {
            var binding = (catalog.bindings[selectedApp] || {})[key]
            if (binding) return binding.label
            if (catalog.pendingBindings[key]) return catalog.pendingBindings[key] + " · 等待快捷键"
            if (sceneScope) return "此场景未绑定"
            if (catalog.voiceOverrideNotice && catalog.voiceGestures.indexOf(key) >= 0) return "语音组已停用 · 未绑定"
        }
        // Presentation only: observe existing bindings, never create defaults here.
        var voice = controller.gestureBindings
        var names = []
        if (voice.confirm.indexOf(key) >= 0) names.push("开始 / 结束语音")
        if (voice.undo.indexOf(key) >= 0) names.push("语音撤销 / 取消")
        if (voice.switch_mode.indexOf(key) >= 0) names.push("语音编辑指令")
        var fixed = {"swipe-up": "输入时发送 · 操作时滚动", "swipe-down": "选择输入框 / 向下滚动"}
        if (fixed[key]) names.push(fixed[key])
        return names.length ? names.join(" · ") : "未配置映射"
    }
    function locked(key) {
        return applicationScope ? Boolean(catalog.globalOccupancy[key])
            : catalog.voiceGestures.indexOf(key) >= 0 || controller.appGestures.inputSourceGesture === key
    }
    function sourceLabel(key) {
        if (applicationScope && catalog.isSharedNavigation(key)) return "浏览器通用"
        if (catalog.globalOccupancy[key]) return applicationScope ? "全局占用" : "全局功能"
        if (applicationScope && catalog.pendingBindings[key]) return "已保存预设"
        if (sceneScope) return (catalog.bindings[selectedApp] || {})[key] ? catalog.selectedSceneLabel : "场景独立配置"
        if (catalog.voiceGestures.indexOf(key) >= 0) return "语音手势组"
        return ""
    }
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AlwaysOff
    AddGestureApplicationDialog {
        id: addDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(620, page.Window.width - 48)
        height: Math.min(600, page.Window.height - 64)
        catalog: page.catalog
        onApplicationAdded: function(bundle) { page.selectApplication(bundle) }
    }
    Dialog {
        id: purposeDialog
        objectName: "applicationPurposeDialog"
        property string application: ""
        parent: Overlay.overlay; anchors.centerIn: parent
        modal: true; popupType: Popup.Item; width: Math.min(440, page.Window.width - 48); padding: 24
        title: "添加场景"
        onAboutToShow: {
            application = page.selectedApp
            purposeChoice.currentIndex = 0
        }
        background: Rectangle { radius: 16; color: theme.surface; border.color: theme.line }
        contentItem: ColumnLayout {
            spacing: 14
            Label { Layout.fillWidth: true; text: "选择要添加的场景，自动配置对应的手势；原有场景和快捷键会保留。"; wrapMode: Text.Wrap; color: theme.muted }
            SettingsSelect { id: purposeChoice; objectName: "applicationPurposeChoice"; Layout.fillWidth: true; model: page.catalog.addableScenes; textRole: "label"; valueRole: "value" }
        }
        footer: DialogButtonBox {
            padding: 18
            UiAction { objectName: "cancelApplicationPurpose"; text: "取消"; onClicked: purposeDialog.reject() }
            UiAction { objectName: "saveApplicationPurpose"; text: "添加"; primary: true; enabled: purposeChoice.currentIndex >= 0; onClicked: {
                if (purposeDialog.application === page.selectedApp && page.catalog.addScene(purposeChoice.currentValue)) purposeDialog.close()
            } }
        }
    }
    Dialog {
        id: managementDialog
        objectName: "applicationManagementDialog"
        property string operation: ""
        property string application: ""
        property string applicationName: ""
        property int bindingCount: 0
        property string errorMessage: ""
        function review(action) {
            if (!page.selectedApplication) return
            operation = action
            application = page.selectedApp
            applicationName = page.selectedApplication.label
            bindingCount = page.catalog.bindingCount(application)
            errorMessage = ""
            open()
        }
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(480, page.Window.width - 48)
        modal: true; popupType: Popup.Item
        closePolicy: Popup.CloseOnEscape
        padding: 24
        title: operation === "remove" ? "移除这个应用？" : operation === "restore" ? "恢复默认映射？" : "清空此应用的映射？"
        background: Rectangle { color: theme.surface; radius: 16; border.color: theme.line }
        header: Label {
            text: managementDialog.title
            leftPadding: 24; rightPadding: 24; topPadding: 24; bottomPadding: 4
            color: theme.text; font.pixelSize: 22; font.weight: Font.DemiBold
        }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
        contentItem: ColumnLayout {
            spacing: 12
            Label { Layout.fillWidth: true; text: managementDialog.applicationName; color: theme.text; font.pixelSize: 16; wrapMode: Text.Wrap }
            Label {
                Layout.fillWidth: true
                text: managementDialog.bindingCount + " 个已保存映射"
                color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
            }
            Label {
                Layout.fillWidth: true
                text: managementDialog.operation === "remove"
                    ? "将从 Mythlink 的应用列表移除，同时清空它的映射。不会卸载电脑上的应用，之后可重新添加。"
                    : managementDialog.operation === "restore"
                    ? "将此应用的常规与场景映射恢复为内置默认配置，替换已保存的映射。恢复后仍可自由修改，其他应用及全局配置保持不变。"
                    : "将清空这个应用的映射，保留应用图标。浏览器保留默认的转圈切换页面操作。其他应用的配置及语音、系统手势保持不变。"
                color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
            }
            UiNotice { Layout.fillWidth: true; visible: text.length > 0; text: managementDialog.errorMessage; font.pixelSize: 12; wrapMode: Text.Wrap }
        }
        footer: DialogButtonBox {
            leftPadding: 24; rightPadding: 24; topPadding: 0; bottomPadding: 20; spacing: 10
            UiAction { objectName: "cancelApplicationManagement"; text: "取消"; onClicked: managementDialog.reject() }
            UiAction {
                objectName: "confirmApplicationManagement"
                text: managementDialog.operation === "remove" ? "移除应用" : managementDialog.operation === "restore" ? "恢复默认" : "清空映射"; primary: true
                onClicked: {
                    var done = managementDialog.operation === "remove"
                        ? page.catalog.removeApplication(managementDialog.application)
                        : managementDialog.operation === "restore"
                        ? page.catalog.restoreDefaultMappings(managementDialog.application)
                        : page.catalog.clearApplicationBindings(managementDialog.application)
                    if (done) {
                        page.managementFeedback = managementDialog.operation === "remove"
                            ? "已移除「" + managementDialog.applicationName + "」"
                            : managementDialog.operation === "restore"
                            ? "已恢复「" + managementDialog.applicationName + "」的默认映射，可继续修改"
                            : "已清空「" + managementDialog.applicationName + "」的映射"
                        managementDialog.close()
                    } else managementDialog.errorMessage = "应用配置已变化，请关闭窗口后重试。"
                }
            }
        }
    }
    ColumnLayout {
        width: page.availableWidth; spacing: 16
        Rectangle {
            Layout.fillWidth: true; implicitHeight: 90; radius: 14; color: theme.surface; border.color: theme.line
            RowLayout {
                anchors.fill: parent; anchors.margins: 14; spacing: 16
                UiAction { objectName: "globalGestureScope"; text: "全局默认"; primary: !page.applicationScope; onClicked: page.applicationScope = false }
                Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 32; color: theme.line }
                ListView {
                    id: applicationTabs
                    objectName: "gestureApplicationTabs"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    orientation: ListView.Horizontal; spacing: 8; clip: true
                    model: page.catalog.apps
                    currentIndex: {
                        for (var i = 0; i < model.length; ++i)
                            if (page.applicationScope && model[i].value === page.selectedApp) return i
                        return -1
                    }
                    onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
                    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                    delegate: AbstractButton {
                        id: appTab
                        required property var modelData
                        objectName: "gestureApplication_" + modelData.value
                        width: 62; height: 60
                        checked: page.applicationScope && page.selectedApp === modelData.value
                        hoverEnabled: true
                        Accessible.name: modelData.label + "，应用手势"
                        Accessible.role: Accessible.PageTab
                        ToolTip.visible: hovered; ToolTip.delay: 350; ToolTip.text: modelData.label
                        onClicked: page.selectApplication(modelData.value)
                        background: Rectangle {
                            radius: 12; color: appTab.checked ? theme.selection : appTab.hovered ? theme.subtle : "transparent"
                            border.color: appTab.activeFocus ? theme.primary : "transparent"
                        }
                        contentItem: Item {
                            ApplicationIcon {
                                anchors.horizontalCenter: parent.horizontalCenter; y: 2; width: 46; height: 46
                                bundle: modelData.value; applicationPath: modelData.path || ""; label: modelData.label
                            }
                            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; y: 53; width: 5; height: 5; radius: 2.5; color: theme.primary; visible: appTab.checked }
                        }
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter; x: 2
                        width: parent.width; visible: applicationTabs.count === 0
                        text: "为应用单独配置手势"; font.pixelSize: 13; color: theme.muted; elide: Text.ElideRight
                    }
                }
                UiAction { objectName: "addGestureApplicationButton"; text: "＋ 添加应用"; enabled: page.controller.appGestures.supported; onClicked: page.addApplication() }
            }
        }
        Rectangle {
            objectName: "applicationSceneBar"
            visible: page.applicationScope && Boolean(page.selectedApp) && (page.catalog.availableScenes.length > 1 || page.catalog.addableScenes.length > 0)
            Layout.fillWidth: true; implicitHeight: sceneControls.implicitHeight + 28
            color: theme.surface; radius: 12; border.color: theme.line
            ColumnLayout {
                id: sceneControls
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 14; spacing: 10
                RowLayout {
                    Label { text: "手势配置"; color: theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                    Item { Layout.fillWidth: true }
                    Label { text: "按前台状态自动生效"; color: theme.muted; font.pixelSize: 11 }
                }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    Repeater {
                        model: page.catalog.availableScenes
                        UiAction {
                            required property var modelData
                            objectName: modelData.value + "GestureScene"
                            text: modelData.label
                            primary: page.catalog.selectedScene === modelData.value
                            onClicked: page.catalog.selectScene(modelData.value)
                        }
                    }
                    UiAction {
                        objectName: "addApplicationSceneButton"
                        text: "＋ 添加场景"; visible: page.catalog.addableScenes.length > 0
                        onClicked: purposeDialog.open()
                    }
                }
                Label {
                    objectName: "gestureSceneHint"
                    Layout.fillWidth: true; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
                    text: page.catalog.isBrowser
                        ? "自动识别当前网页的视频、PDF 等场景，无需添加网站。顺时针切换下一个 Chat / 标签页，逆时针切换上一个；所有场景共用默认配置中的转圈绑定。输入文字时暂停场景动作，转圈仍可切换页面。"
                        : !page.catalog.isBrowser && page.catalog.supportsPresentation && page.catalog.selectedScene === "regular"
                        ? "默认配置用于未进入放映时的应用快捷键，例如响指开始放映；放映中的翻页、结束动作请在「放映」中设置。"
                        : page.sceneScope
                        ? "仅在此应用前台处于「" + page.catalog.selectedSceneLabel + "」且未输入文字时生效；场景手势独立于常规配置。"
                          + (["video", "music"].indexOf(page.catalog.selectedScene) >= 0 ? "播放暂停时仍可使用播放控制。" : "")
                        : "覆盖任意语音组手势后，该应用常规状态下停用整组语音操作。全局占用的手势不能在应用内修改。"
                }
                Label {
                    objectName: "gestureSceneObservation"
                    visible: page.sceneScope && page.catalog.isBrowser; Layout.fillWidth: true
                    text: page.catalog.sceneHint; color: theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap
                }
                UiNotice {
                    Layout.fillWidth: true
                    visible: page.catalog.defaultMappingNotice.length > 0
                    text: page.catalog.defaultMappingNotice
                    font.pixelSize: 11; wrapMode: Text.Wrap
                }
            }
        }
        PermissionNotice {
            objectName: "applicationVoiceOverrideNotice"
            Layout.fillWidth: true
            message: page.applicationScope ? page.catalog.voiceOverrideNotice : ""
            actionText: ""
        }
        PermissionNotice {
            objectName: "applicationGlobalConflictNotice"
            Layout.fillWidth: true
            message: page.applicationScope ? page.catalog.globalConflictNotice : ""
            actionText: ""
        }
        Label { objectName: "applicationManagementFeedback"; Layout.fillWidth: true; visible: text.length > 0; text: page.managementFeedback; color: theme.success; font.pixelSize: 12; wrapMode: Text.Wrap }
        GridLayout {
            Layout.fillWidth: true
            columns: width >= 960 ? 2 : 1
            columnSpacing: 16; rowSpacing: 16
            Rectangle {
                Layout.fillWidth: true; Layout.preferredWidth: 700; Layout.preferredHeight: 664
                Layout.alignment: Qt.AlignTop
                radius: 14; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 18
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "手势映射"; color: theme.text; font.pixelSize: 19; font.weight: Font.DemiBold }
                        Item { Layout.fillWidth: true }
                        Label { text: "点击手势选择功能"; color: theme.muted; font.pixelSize: 12 }
                    }
                    Rectangle {
                        objectName: "applicationManagementBar"
                        Layout.fillWidth: true; implicitHeight: 74
                        visible: page.applicationScope && Boolean(page.selectedApplication)
                        radius: 12; color: theme.surface; border.color: theme.line
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 14; spacing: 12
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 5
                                Label {
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                    text: page.selectedApplication ? page.selectedApplication.label + " · 已绑定 " + Object.keys(page.catalog.bindings[page.selectedApp] || {}).length + " 个手势" : ""
                                    color: theme.text; font.pixelSize: 14; font.weight: Font.DemiBold
                                }
                                Label {
                                    objectName: "applicationMenuReadTime"
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                    text: page.catalog.busy ? "正在读取应用菜单…"
                                        : page.catalog.menuReadAt ? "最近读取 " + page.catalog.menuReadAt + " · 菜单状态以该次读取为准" : "刷新快捷键以查看菜单状态"
                                    color: theme.muted; font.pixelSize: 11
                                }
                            }
                            UiAction { objectName: "refreshApplicationMenu"; text: "刷新快捷键"; quiet: true; enabled: !page.catalog.busy; onClicked: page.catalog.refreshMenu() }
                            UiAction {
                                id: manageApplicationButton
                                objectName: "manageApplicationButton"
                                text: "管理应用 ···"
                                onClicked: applicationMenu.popup()
                                Menu {
                                    id: applicationMenu
                                    MenuItem {
                                        objectName: "changeApplicationPurpose"
                                        text: "添加场景"
                                        visible: page.catalog.addableScenes.length > 0
                                        height: visible ? implicitHeight : 0
                                        onTriggered: purposeDialog.open()
                                    }
                                    MenuItem {
                                        objectName: "disableApplicationScene"
                                        text: "停用当前场景（保留配置）"
                                        visible: page.sceneScope; height: visible ? implicitHeight : 0
                                        onTriggered: page.catalog.disableScene(page.catalog.selectedScene)
                                    }
                                    MenuItem {
                                        objectName: "restoreDefaultApplicationMappings"
                                        text: "恢复默认映射"
                                        enabled: page.catalog.hasDefaultMappings
                                        onTriggered: managementDialog.review("restore")
                                    }
                                    MenuItem {
                                        objectName: "clearApplicationMappings"
                                        text: "清空此应用的映射"
                                        enabled: {
                                            var bindings = page.catalog.bindings
                                            return page.catalog.bindingCount(page.selectedApp) > 0 || page.catalog.pendingDefaultCount > 0
                                        }
                                        onTriggered: managementDialog.review("clear")
                                    }
                                    MenuItem { objectName: "removeGestureApplication"; text: "移除应用"; onTriggered: managementDialog.review("remove") }
                                }
                            }
                        }
                    }
                    Item {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        Repeater {
                            model: page.gestures
                            delegate: AbstractButton {
                                id: card
                                required property var modelData
                                property bool locked: page.locked(modelData.key)
                                property bool selected: page.selectedGesture === modelData.key
                                objectName: "gestureCard_" + modelData.key
                                x: modelData.col * (parent.width + 14) / 3
                                y: modelData.row * (parent.height + 14) / 5
                                width: (parent.width - 28) / 3
                                height: (parent.height - 56) / 5
                                hoverEnabled: true
                                Accessible.name: modelData.title + "，" + page.description(modelData.key) + "，" + page.sourceLabel(modelData.key)
                                onClicked: page.selectedGesture = modelData.key
                                background: Rectangle {
                                    radius: 12
                                    color: card.locked ? "#F0F1F5" : card.selected ? theme.primary : card.hovered ? "#EDF1FF" : theme.subtle
                                    border.width: card.selected ? 2 : 1
                                    border.color: card.selected ? (card.locked ? "#BBC2D3" : "#567DFF") : theme.line
                                }
                                contentItem: RowLayout {
                                    anchors.fill: parent; anchors.margins: 10; spacing: 10
                                    GestureIllustration {
                                        Layout.preferredWidth: Math.max(0, Math.min(card.width >= 204 ? 83 : 64, card.height - 20))
                                        Layout.preferredHeight: Layout.preferredWidth
                                        gesture: modelData.key
                                        locked: card.locked
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 5
                                        Label { Layout.fillWidth: true; text: modelData.title; color: card.locked ? "#7D879B" : card.selected ? "white" : theme.text; font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight }
                                        Label { Layout.fillWidth: true; text: page.description(modelData.key); color: card.locked ? "#8B94A7" : card.selected ? "#D7E1FF" : theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight }
                                        Label { Layout.fillWidth: true; visible: text.length > 0; text: page.sourceLabel(modelData.key); color: card.locked ? "#8B94A7" : card.selected ? "#D7E1FF" : theme.primary; font.pixelSize: 9; elide: Text.ElideRight }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            GestureActionEditor {
                id: actionEditor
                Layout.fillWidth: true; Layout.preferredWidth: 360; Layout.preferredHeight: implicitHeight
                Layout.alignment: Qt.AlignTop
                service: page.controller.appGestures
                globalController: page.controller.ringGestures
                gesture: page.selectedGesture
                gestureTitle: page.selectedTitle
                currentDescription: page.description(page.selectedGesture)
                applicationScope: page.applicationScope
            }
        }
        Item { Layout.preferredHeight: 6 }
    }
}
