import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

UiScrollView {
    id: page
    required property var controller
    FigmaTheme { id: theme }
    objectName: "gesturesPage"
    property bool applicationScope: false
    property string selectedGesture: ""
    signal voiceSettingsRequested()
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
        {key: "circle-clockwise", title: "顺时针", row: 0, col: 0},
        {key: "clench", title: "握拳", row: 0, col: 1},
        {key: "circle-counterclockwise", title: "逆时针", row: 0, col: 2},
        {key: "double-tap", title: "双击", row: 1, col: 0},
        {key: "swipe-up", title: "上滑", row: 1, col: 1},
        {key: "swipe-left", title: "左滑", row: 2, col: 0},
        {key: "tap", title: "点击", row: 2, col: 1},
        {key: "swipe-right", title: "右滑", row: 2, col: 2},
        {key: "swipe-down", title: "下滑", row: 3, col: 1},
        {key: "index-pinch", title: "食拇捏合", row: 4, col: 0},
        {key: "middle-pinch", title: "中拇捏合", row: 4, col: 1},
        {key: "snap", title: "响指", row: 4, col: 2},
        {key: "double-index-pinch", title: "双次食拇捏合", row: 1, col: 2},
        {key: "double-middle-pinch", title: "双次中拇捏合", row: 3, col: 2}
    ]
    readonly property string selectedTitle: (gestures.filter(function(g) { return g.key === selectedGesture })[0] || {}).title || ""
    function description(key) {
        if (applicationScope) {
            var binding = (catalog.bindings[selectedApp] || {})[key]
            if (binding) return binding.label
            if (catalog.pendingBindings[key]) return catalog.pendingBindings[key] + " · 等待快捷键"
            var regular = sceneScope && (catalog.regularBindings[selectedApp] || {})[key]
            if (regular) return "沿用应用 · " + regular.label
        }
        var fallback = catalog.globalOccupancy[key]
        return fallback ? (applicationScope ? "沿用全局 · " : "") + fallback : "未配置映射"
    }
    function locked(key) { return applicationScope && sceneScope && catalog.isSharedNavigation(key) }
    function sourceLabel(key) {
        if (applicationScope && catalog.isSharedNavigation(key) && (catalog.bindings[selectedApp] || {})[key]) return "浏览器通用"
        if (applicationScope && (catalog.bindings[selectedApp] || {})[key]) return "应用优先"
        if (sceneScope && (catalog.regularBindings[selectedApp] || {})[key]) return "应用默认"
        if (catalog.globalOccupancy[key]) return applicationScope ? "全局默认" : "全局功能"
        if (applicationScope && catalog.pendingBindings[key]) return "已保存预设"
        return catalog.voiceGestures.indexOf(key) >= 0 ? "输入框内优先输入" : "不受输入焦点影响"
    }
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AlwaysOff
    AddGestureApplicationDialog {
        id: addDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(400, page.Window.width - 48)
        height: Math.min(560, page.Window.height - 64, implicitHeight)
        catalog: page.catalog
        onApplicationAdded: function(bundle) { page.selectApplication(bundle) }
    }
    FigmaDialog {
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
            FigmaSelect { id: purposeChoice; objectName: "applicationPurposeChoice"; Layout.fillWidth: true; model: page.catalog.addableScenes; textRole: "label"; valueRole: "value" }
        }
        footer: DialogButtonBox {
            padding: 18
            FigmaAction { objectName: "cancelApplicationPurpose"; text: "取消"; onClicked: purposeDialog.reject() }
            FigmaAction { objectName: "saveApplicationPurpose"; text: "添加"; primary: true; enabled: purposeChoice.currentIndex >= 0; onClicked: {
                if (purposeDialog.application === page.selectedApp && page.catalog.addScene(purposeChoice.currentValue)) purposeDialog.close()
            } }
        }
    }
    FigmaDialog {
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
                    : "将清空这个应用的映射，保留应用图标。未绑定的手势将沿用全局配置。其他应用的配置及语音、系统手势保持不变。"
                color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
            }
            UiNotice { Layout.fillWidth: true; visible: text.length > 0; text: managementDialog.errorMessage; font.pixelSize: 12; wrapMode: Text.Wrap }
        }
        footer: DialogButtonBox {
            leftPadding: 24; rightPadding: 24; topPadding: 0; bottomPadding: 20; spacing: 10
            FigmaAction { objectName: "cancelApplicationManagement"; text: "取消"; onClicked: managementDialog.reject() }
            FigmaAction {
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
        GestureHandMap {
            Layout.fillWidth: true; Layout.preferredHeight: implicitHeight
            selectedGesture: page.selectedGesture
            describeGesture: function(gesture) { return page.description(gesture) + " · " + page.sourceLabel(gesture) }
            onGestureChosen: function(gesture) { page.selectedGesture = gesture }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 44; spacing: 16
            FigmaAction {
                objectName: "globalGestureScope"; text: "全局默认"; primary: !page.applicationScope
                Layout.preferredWidth: 120; Layout.preferredHeight: 40; font.pixelSize: 16
                onClicked: page.applicationScope = false
            }
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 44
                color: theme.surface; radius: 12; border.color: theme.line
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 8
                    ListView {
                        id: applicationTabs
                        objectName: "gestureApplicationTabs"
                        Layout.fillWidth: true; Layout.preferredHeight: 36
                        orientation: ListView.Horizontal; spacing: 8; clip: true
                        model: page.catalog.apps
                        currentIndex: {
                            for (var i = 0; i < model.length; ++i)
                                if (page.applicationScope && model[i].value === page.selectedApp) return i
                            return -1
                        }
                        onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
                        ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                        delegate: FigmaAction {
                            id: appTab
                            required property var modelData
                            objectName: "gestureApplication_" + modelData.value
                            width: 44; height: 36; padding: 0
                            checked: page.applicationScope && page.selectedApp === modelData.value
                            Accessible.name: modelData.label + "，应用手势"; Accessible.role: Accessible.PageTab
                            ToolTip.visible: hovered; ToolTip.delay: 350; ToolTip.text: modelData.label
                            onClicked: page.selectApplication(modelData.value)
                            background: Rectangle { radius: 8; color: appTab.checked || appTab.down ? theme.selection : appTab.hovered ? theme.subtle : "transparent"; border.width: appTab.visualFocus ? 1 : 0; border.color: theme.primary }
                            contentItem: Item {
                                ApplicationIcon { anchors.centerIn: parent; width: 28; height: 28; bundle: modelData.value; applicationPath: modelData.path || ""; label: modelData.label }
                            }
                        }
                    }
                    FigmaAction {
                        objectName: "manageGestureConfigurationButton"; text: "配置管理"; quiet: true
                        visible: page.applicationScope; Layout.preferredHeight: 32
                        onClicked: page.contentItem.contentY = Math.max(0, Math.min(applicationManagementBar.y - 8, page.contentHeight - page.availableHeight))
                    }
                    FigmaAction {
                        objectName: "addGestureApplicationButton"; text: "添加应用"; navigation: true
                        Layout.preferredWidth: 124; Layout.preferredHeight: 32; font.pixelSize: 14
                        enabled: page.controller.appGestures.supported
                        onClicked: page.addApplication()
                    }
                }
            }
        }
        GestureActionEditor {
            id: actionEditor
            Layout.fillWidth: true; Layout.preferredHeight: implicitHeight
            service: page.controller.appGestures; globalController: page.controller.ringGestures
            gesture: page.selectedGesture; gestureTitle: page.selectedTitle
            currentDescription: page.description(page.selectedGesture); applicationScope: page.applicationScope
            onVoiceSettingsRequested: page.voiceSettingsRequested()
        }
        ColumnLayout {
            id: applicationManagementBar
            objectName: "applicationManagementBar"
            visible: page.applicationScope && Boolean(page.selectedApplication)
            Layout.fillWidth: true; spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: page.selectedApplication ? page.selectedApplication.label : ""; color: theme.text; font.pixelSize: 16; font.weight: Font.Medium; elide: Text.ElideRight }
                FigmaAction { objectName: "refreshApplicationMenu"; text: page.catalog.busy ? "读取中…" : "刷新快捷键"; quiet: true; implicitHeight: 32; enabled: !page.catalog.busy; onClicked: page.catalog.refreshMenu() }
                FigmaAction {
                    id: manageApplicationButton
                    objectName: "manageApplicationButton"; text: "管理应用"; implicitHeight: 32
                    onClicked: applicationMenu.open()
                FigmaMenu {
                    id: applicationMenu
                    objectName: "applicationManagementMenu"
                    FigmaMenuItem {
                        objectName: "changeApplicationPurpose"
                        text: "添加场景"
                        visible: page.catalog.addableScenes.length > 0
                        height: visible ? implicitHeight : 0
                        onTriggered: purposeDialog.open()
                    }
                    FigmaMenuItem {
                        objectName: "disableApplicationScene"
                        text: "停用当前场景（保留配置）"
                        visible: page.sceneScope; height: visible ? implicitHeight : 0
                        onTriggered: page.catalog.disableScene(page.catalog.selectedScene)
                    }
                    FigmaMenuItem {
                        objectName: "restoreDefaultApplicationMappings"
                        text: "恢复默认映射"
                        enabled: page.catalog.hasDefaultMappings
                        onTriggered: managementDialog.review("restore")
                    }
                    FigmaMenuItem {
                        objectName: "clearApplicationMappings"
                        destructive: true
                        text: "清空此应用的映射"
                        enabled: {
                            var bindings = page.catalog.bindings
                            return page.catalog.bindingCount(page.selectedApp) > 0 || page.catalog.pendingDefaultCount > 0
                        }
                        onTriggered: managementDialog.review("clear")
                    }
                    FigmaMenuItem { objectName: "removeGestureApplication"; destructive: true; text: "移除应用"; onTriggered: managementDialog.review("remove") }
                }

                }
            }
            Flow {
                objectName: "applicationSceneBar"
                visible: page.catalog.availableScenes.length > 1 || page.catalog.addableScenes.length > 0
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: page.catalog.availableScenes
                    FigmaAction {
                        required property var modelData
                        objectName: modelData.value + "GestureScene"; text: modelData.label; implicitHeight: 32
                        primary: page.catalog.selectedScene === modelData.value
                        onClicked: page.catalog.selectScene(modelData.value)
                    }
                }
                FigmaAction { objectName: "addApplicationSceneButton"; text: "添加场景"; implicitHeight: 32; visible: page.catalog.addableScenes.length > 0; onClicked: purposeDialog.open() }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 8
            visible: page.selectedGesture.length > 0 || page.applicationScope || page.managementFeedback.length > 0
            Label { objectName: "menuMappingFeedback"; Layout.fillWidth: true; visible: text.length > 0; text: actionEditor.feedback; color: actionEditor.feedbackError ? theme.warning : theme.success; font.pixelSize: 14; wrapMode: Text.Wrap }
            Label { objectName: "applicationManagementFeedback"; Layout.fillWidth: true; visible: text.length > 0; text: page.managementFeedback; color: theme.success; font.pixelSize: 14; wrapMode: Text.Wrap }
            Label { objectName: "selectedGestureDescription"; Layout.fillWidth: true; visible: !actionEditor.editable && page.selectedGesture.length > 0; text: page.description(page.selectedGesture) + (actionEditor.sharedSceneNavigation ? " · 浏览器各场景共用，请在默认配置中修改转圈手势" : ""); color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
            Label { objectName: "savedMenuBinding"; Layout.fillWidth: true; visible: Boolean(actionEditor.saved); text: "已保存：" + (actionEditor.saved ? actionEditor.saved.label + (actionEditor.actionHint(actionEditor.saved) ? " · " + actionEditor.actionHint(actionEditor.saved) : "") : ""); color: theme.text; font.pixelSize: 14; wrapMode: Text.Wrap }
            Label { objectName: "savedMenuBindingStatus"; Layout.fillWidth: true; visible: Boolean(actionEditor.saved); text: actionEditor.savedStatus; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
            Label { objectName: "globalBindingEffect"; Layout.fillWidth: true; visible: !page.applicationScope && page.selectedGesture.length > 0; text: actionEditor.dirty && actionEditor.draft.effect ? actionEditor.draft.effect.replace("选择后", "保存后") : actionEditor.globalEffect; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
            PermissionNotice { objectName: "applicationVoiceOverrideNotice"; Layout.fillWidth: true; message: page.applicationScope ? page.catalog.voiceOverrideNotice : ""; actionText: "" }
            PermissionNotice { objectName: "applicationGlobalConflictNotice"; Layout.fillWidth: true; message: page.applicationScope ? page.catalog.globalConflictNotice : ""; actionText: "" }
            FigmaAction { id: detailsToggle; objectName: "gestureDetailsButton"; text: checked ? "收起配置说明" : "配置说明"; quiet: true; checkable: true; implicitHeight: 32 }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 8; visible: detailsToggle.checked
            Label {
                objectName: "gestureSceneHint"
                Layout.fillWidth: true; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
                text: page.catalog.isBrowser
                    ? "连接 Chrome 或 Safari 扩展并授权网站后，自动识别网页视频和音频。浏览器 PDF、图片场景暂不自动识别。顺时针切换下一个标签页，逆时针切换上一个；所有场景共用默认配置中的转圈绑定。输入框内优先执行输入功能。"
                    : !page.catalog.isBrowser && page.catalog.supportsPresentation && page.catalog.selectedScene === "regular"
                    ? "默认配置用于未进入放映时的应用快捷键，例如响指开始放映；放映中的翻页、结束动作请在「放映」中设置。"
                    : page.sceneScope
                    ? "仅在此应用前台处于「" + page.catalog.selectedSceneLabel + "」且未输入文字时生效；场景手势独立于常规配置。"
                      + (["video", "music"].indexOf(page.catalog.selectedScene) >= 0 ? "播放暂停时仍可使用播放控制。" : "")
                    : "仅与输入手势重叠时，输入框内优先执行输入功能；其他手势不受输入焦点影响。未配置的应用使用全局配置；已配置的应用优先，未绑定的手势沿用全局配置。"
            }
            Label {
                objectName: "gestureSceneObservation"
                visible: page.sceneScope && page.catalog.isBrowser; Layout.fillWidth: true
                text: page.catalog.sceneHint; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
            }
            UiNotice {
                Layout.fillWidth: true
                visible: page.catalog.defaultMappingNotice.length > 0
                text: page.catalog.defaultMappingNotice
                font.pixelSize: 14; wrapMode: Text.Wrap
            }

                Label { objectName: "applicationMenuReadTime"; Layout.fillWidth: true; visible: page.applicationScope; text: page.catalog.menuReadAt ? "最近读取 " + page.catalog.menuReadAt + " · 菜单状态以该次读取为准" : "刷新快捷键以查看菜单状态"; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
                Label { objectName: "menuReadMessage"; Layout.fillWidth: true; visible: page.applicationScope; text: page.catalog.message; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
                FigmaAction { objectName: "openWorkflowRecommendations"; text: "查看推荐"; enabled: page.controller.agent.workflowPipelineEnabled || page.controller.agent.historyRecommendations.enabled; onClicked: page.controller.agent.openRecommendations() }
            }
        }
    }
}
