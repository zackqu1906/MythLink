import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Window

UiScrollView {
    id: page
    required property var controller
    signal browserSetupRequested(string browser)
    FigmaTheme { id: theme }
    objectName: "gesturesPage"
    property bool applicationScope: false
    property string selectedGesture: ""
    readonly property var catalog: controller.appGestures.catalog
    readonly property string selectedApp: catalog.selectedApp
    readonly property string extensionBrowser: selectedApp.toLowerCase() === "com.google.chrome" ? "chrome"
                                               : selectedApp.toLowerCase() === "com.apple.safari" ? "safari" : ""
    readonly property bool browserConnected: extensionBrowser === "chrome" ? controller.browserExtensions.chromeConnected
                                            : extensionBrowser === "safari" && controller.browserExtensions.safariConnected
    // Catalog state/menu refreshes must not recreate a pressed application tab.
    readonly property string applicationTabsJson: JSON.stringify(catalog.apps)
    readonly property var applicationTabsModel: JSON.parse(applicationTabsJson)
    // Presentation-only filtering: retain every saved scene and runtime mapping.
    function sceneVisible(value) { return value === "regular" || value === "video" || value === "presentation" }
    readonly property var visibleAddableScenes: catalog.addableScenes.filter(function(scene) { return page.sceneVisible(scene.value) })
    readonly property bool sceneScope: applicationScope && catalog.selectedScene !== "regular"
    readonly property bool showVoiceMappings: controller.recognitionEnabled
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
        if (!sceneVisible(catalog.selectedScene)) catalog.selectScene("regular")
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
        return regularDescription(key) || "未配置映射"
    }
    function voiceDescription(key) {
        var bindings = controller.gestureBindings
        return controller.voiceGestureAction(key).label || ""
    }
    function regularDescription(key) {
        if (applicationScope) {
            var binding = (catalog.bindings[selectedApp] || {})[key]
            if (binding) return binding.label
            if (catalog.pendingBindings[key]) return catalog.pendingBindings[key] + " · 等待快捷键"
            var regular = sceneScope && (catalog.regularBindings[selectedApp] || {})[key]
            if (regular) return "沿用应用 · " + regular.label
        }
        var fallback = catalog.globalOccupancy[key]
        return fallback ? (applicationScope ? "沿用全局 · " : "") + fallback : ""
    }
    function mappingHint(key) {
        var ordinary = regularDescription(key)
        var voice = showVoiceMappings ? voiceDescription(key) : ""
        var ordinaryHint = ordinary ? ordinary + " · " + sourceLabel(key) : "未配置普通功能"
        if (!voice) return ordinaryHint
        return (controller.recognitionEnabled ? "输入框内或语音操作中：" : "语音绑定（已关闭）：") + voice
            + (ordinary ? "\n其他时候：" + ordinaryHint : "")
            + "\n语音与普通功能独立保存，同一次手势只执行其中一个功能。"
            + "\n语音绑定在此处只读，请到语音页的「配置手势」中修改。"
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
    // Keep app, scene and editing controls in view; the hand map fits the rest.
    contentHeight: availableHeight
    Binding { target: page.contentItem; property: "interactive"; value: false }
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AlwaysOff
    FigmaDialog {
        id: priorityRules
        objectName: "voiceGesturePriorityRules"
        parent: Overlay.overlay; anchors.centerIn: parent
        title: "语音手势规则"
        width: Math.min(460, page.Window.width - 48)
        padding: 24; topPadding: 4
        modal: true; popupType: Popup.Item
        background: Rectangle { radius: 16; color: theme.background; border.color: theme.line }
        contentItem: ColumnLayout {
            spacing: 12
            Label {
                Layout.fillWidth: true
                text: "同一次手势只执行一个功能，原有映射保留。"
                wrapMode: Text.Wrap; color: theme.muted; font.family: theme.family; font.pixelSize: 14
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: inputRule.implicitHeight + 28
                radius: 12; color: theme.subtle; border.color: theme.line
                ColumnLayout {
                    id: inputRule
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 14; spacing: 6
                    RowLayout {
                        spacing: 8
                        FigmaIcon { symbol: "microphone"; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                        Label { text: "输入框内或语音操作中"; color: theme.text; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "与语音绑定重叠时，优先执行语音功能。未重叠的手势继续使用应用或全局配置。"
                        wrapMode: Text.Wrap; color: theme.muted; font.family: theme.family; font.pixelSize: 14
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: defaultRule.implicitHeight + 28
                radius: 12; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    id: defaultRule
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 14; spacing: 6
                    RowLayout {
                        spacing: 8
                        FigmaIcon { symbol: "gesture"; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                        Label { text: "其他时候"; color: theme.text; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "优先使用当前应用的配置；未配置的手势沿用全局默认。"
                        wrapMode: Text.Wrap; color: theme.muted; font.family: theme.family; font.pixelSize: 14
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                text: "语音绑定在「语音输入 → 配置手势」中修改，对所有应用生效。每个语音功能只绑定一个手势。"
                wrapMode: Text.Wrap; color: theme.muted; font.family: theme.family; font.pixelSize: 13
            }
            FigmaAction { objectName: "closeVoiceGesturePriorityRules"; Layout.fillWidth: true; text: "知道了"; primary: true; font.pixelSize: 16; onClicked: priorityRules.close() }
        }
    }
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
            FigmaSelect { id: purposeChoice; objectName: "applicationPurposeChoice"; Layout.fillWidth: true; model: page.visibleAddableScenes; textRole: "label"; valueRole: "value" }
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
        width: page.availableWidth
        height: Math.min(page.availableHeight, implicitHeight)
        spacing: 12
        GestureHandMap {
            id: handMap
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.minimumHeight: 0; Layout.maximumHeight: implicitHeight
            Layout.preferredHeight: implicitHeight
            selectedGesture: page.selectedGesture
            voiceActive: page.controller.recognitionEnabled
            voiceEditable: false
            describeGesture: function(gesture) {
                return page.regularDescription(gesture).replace(/^沿用(应用|全局) · /, "")
            }
            describeVoiceGesture: function(gesture) { return page.showVoiceMappings ? page.voiceDescription(gesture) : "" }
            gestureHint: function(gesture) { return page.mappingHint(gesture) }
            onGestureChosen: function(gesture) { page.selectedGesture = gesture }
            onMappingChosen: function(gesture, voice) { page.selectedGesture = gesture }
            cornerContent: Component {
                Rectangle {
                    objectName: "gestureVoicePriorityCard"
                    radius: 12; color: theme.surface; border.color: theme.line
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: handMap.compact ? 8 : 13
                        spacing: handMap.compact ? 4 : 8
                        Label { Layout.fillWidth: true; text: "语音输入规则"; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium; color: theme.text }
                        Label { visible: !handMap.compact; Layout.fillWidth: true; Layout.fillHeight: true; text: "输入时优先使用语音手势，其他时候使用应用或全局功能。"; font.family: theme.family; font.pixelSize: 14; color: theme.muted; wrapMode: Text.Wrap; verticalAlignment: Text.AlignVCenter }
                        FigmaAction {
                            objectName: "voiceGesturePriorityRulesButton"
                            Layout.fillWidth: true; Layout.preferredHeight: 32
                            text: "查看语音输入规则"; navigation: true; font.pixelSize: 14; leftPadding: 8
                            onClicked: priorityRules.open()
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 44; spacing: 16
            FigmaAction {
                objectName: "openGestureHistoryButton"; text: "手势记录"; quiet: true
                onClicked: page.controller.gestureHistory.show()
            }
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
                        model: page.applicationTabsModel
                        interactive: contentWidth > width
                        currentIndex: {
                            for (var i = 0; i < model.length; ++i)
                                if (page.applicationScope && model[i].value === page.selectedApp) return i
                            return -1
                        }
                        onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
                        ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AlwaysOff }
                        delegate: FigmaAction {
                            id: appTab
                            required property var modelData
                            objectName: "gestureApplication_" + modelData.value
                            width: 44; height: 36; padding: 0
                            checked: page.applicationScope && page.selectedApp === modelData.value
                            Accessible.name: modelData.label + "，应用手势"; Accessible.role: Accessible.PageTab
                            Basic.ToolTip {
                                objectName: "applicationTooltip_" + appTab.modelData.value
                                parent: appTab; popupType: Popup.Item; y: -height - 8
                                visible: (appTab.enabled && appTab.hovered); delay: 350; text: appTab.modelData.label
                                focus: false; modal: false; closePolicy: Popup.NoAutoClose
                            }
                            onClicked: page.selectApplication(modelData.value)
                            background: Rectangle { radius: 8; color: appTab.checked || appTab.down ? theme.selection : (appTab.enabled && appTab.hovered) ? theme.subtle : "transparent"; border.width: appTab.visualFocus ? 1 : 0; border.color: theme.primary }
                            contentItem: Item {
                                ApplicationIcon { anchors.centerIn: parent; width: 28; height: 28; bundle: modelData.value; applicationPath: modelData.path || ""; label: modelData.label }
                            }
                        }
                    }
                    FigmaAction {
                        objectName: "manageApplicationButton"; text: "管理应用"; quiet: true
                        visible: page.applicationScope; Layout.preferredHeight: 32
                        onClicked: applicationMenu.open()
                        FigmaMenu {
                            id: applicationMenu
                            objectName: "applicationManagementMenu"
                            FigmaMenuItem {
                                objectName: "refreshApplicationMenu"
                                text: page.catalog.busy ? "读取中…" : "刷新快捷键"
                                enabled: !page.catalog.busy
                                onTriggered: page.catalog.refreshMenu()
                            }
                            FigmaMenuItem {
                                objectName: "changeApplicationPurpose"
                                text: "添加场景"
                                visible: page.visibleAddableScenes.length > 0
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
                    FigmaAction {
                        objectName: "addGestureApplicationButton"; text: "添加应用"; navigation: true
                        Layout.preferredWidth: 124; Layout.preferredHeight: 32; font.pixelSize: 14
                        enabled: page.controller.appGestures.supported
                        onClicked: page.addApplication()
                    }
                }
            }
        }
        Rectangle {
            id: applicationManagementBar
            objectName: "applicationManagementBar"
            visible: page.applicationScope && Boolean(page.selectedApplication)
            Layout.fillWidth: true; Layout.preferredHeight: implicitHeight
            implicitHeight: modeRow.implicitHeight + 16
            radius: 12; color: theme.subtle
            RowLayout {
                id: modeRow
                anchors.left: parent.left; anchors.right: parent.right
                anchors.top: parent.top; anchors.margins: 8
                spacing: 12
                Flow {
                    id: sceneOptions
                    objectName: "applicationSceneBar"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    Layout.preferredHeight: implicitHeight
                    Layout.alignment: Qt.AlignTop
                    spacing: 6
                    Label {
                        width: Math.min(implicitWidth, 160); height: 32
                        leftPadding: 6; rightPadding: 10
                        text: page.selectedApplication ? page.selectedApplication.label : ""
                        color: theme.muted; font.family: theme.family; font.pixelSize: 13
                        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                    }
                    Repeater {
                        model: page.catalog.availableScenes
                        FigmaAction {
                            required property var modelData
                            objectName: modelData.value + "GestureScene"; text: modelData.label
                            visible: page.sceneVisible(modelData.value)
                            implicitHeight: 32; leftPadding: 12; rightPadding: 12
                            primary: page.catalog.selectedScene === modelData.value
                            quiet: !primary
                            Accessible.role: Accessible.PageTab
                            onClicked: page.catalog.selectScene(modelData.value)
                        }
                    }
                    FigmaAction {
                        objectName: "addApplicationSceneButton"; text: "+ 添加场景"
                        implicitHeight: 32; leftPadding: 12; rightPadding: 12; quiet: true
                        contentColor: theme.primary
                        visible: page.visibleAddableScenes.length > 0
                        onClicked: purposeDialog.open()
                    }
                }
                RowLayout {
                    objectName: "gestureBrowserExtensionEntry"
                    visible: Qt.platform.os === "osx" && page.extensionBrowser.length > 0
                    Layout.alignment: Qt.AlignRight | Qt.AlignTop
                    spacing: 10
                    Label {
                        objectName: "gestureBrowserExtensionHint"
                        text: page.browserConnected ? "插件已连接" : "安装插件，启用网页控制"
                        color: page.browserConnected ? theme.success : theme.muted
                        font.family: theme.family; font.pixelSize: 12
                    }
                    FigmaAction {
                        objectName: "gestureBrowserExtensionButton"
                        implicitHeight: 32; leftPadding: 12; rightPadding: 12
                        text: page.browserConnected ? "管理插件" : "安装插件"
                        primary: !page.browserConnected
                        onClicked: page.browserSetupRequested(page.extensionBrowser)
                    }
                }
            }
        }
        GestureActionEditor {
            id: actionEditor
            Layout.fillWidth: true; Layout.preferredHeight: implicitHeight
            service: page.controller.appGestures; globalController: page.controller.ringGestures; voiceController: page.controller
            gesture: page.selectedGesture; gestureTitle: page.selectedTitle
            currentDescription: page.description(page.selectedGesture); applicationScope: page.applicationScope
        }
        Label {
            objectName: "applicationManagementFeedback"
            Layout.fillWidth: true
            visible: page.managementFeedback.length > 0
            text: page.managementFeedback; color: theme.success; font.pixelSize: 14; wrapMode: Text.Wrap
        }
    }
}
