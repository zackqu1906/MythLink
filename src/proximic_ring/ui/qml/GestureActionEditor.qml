import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: editor
    required property var service
    required property var globalController
    required property string gesture
    required property string gestureTitle
    required property string currentDescription
    required property bool applicationScope
    UiTheme { id: theme }
    readonly property var catalog: service.catalog
    readonly property string bundle: catalog.selectedApp
    readonly property bool sceneScope: applicationScope && catalog.selectedScene !== "regular"
    readonly property bool anchorGesture: Boolean(catalog.globalOccupancy[gesture])
    readonly property var globalOptions: {
        var bindings = globalController.globalBindings
        var voiceBindings = service.profiles
        return globalController.globalActionOptions(gesture)
    }
    readonly property var saved: applicationScope ? (catalog.bindings[bundle] || {})[gesture] || null
        : globalOptions.filter(function(a) { return editor.globalController.globalBindings[a.id] === editor.gesture })[0] || null
    readonly property string savedId: saved ? saved.id : ""
    readonly property string contextKey: applicationScope ? bundle + "/" + catalog.selectedScene + "/" + catalog.selectedWebsite + "/" + gesture : "global/" + gesture
    readonly property string savedVersion: applicationScope ? savedId : JSON.stringify(globalController.globalBindings)
    // Drafts survive a gesture/app/page switch, but never alter live bindings.
    property var drafts: ({})
    readonly property string draftId: drafts[contextKey] ? drafts[contextKey].id : savedId
    readonly property var customDraft: drafts[contextKey] ? drafts[contextKey].custom || null : null
    readonly property bool dirty: draftId !== savedId
    readonly property bool stale: Boolean(drafts[contextKey] && drafts[contextKey].base !== savedVersion)
    readonly property bool voiceLocked: catalog.voiceGestures.indexOf(gesture) >= 0
    readonly property bool editable: {
        var bindings = service.profiles // Refresh when voice/source settings change.
        var scene = catalog.selectedScene
        var occupied = catalog.globalOccupancy
        return applicationScope ? bundle.length > 0 && service.supported && catalog.canBind(gesture) : globalOptions.length > 0
    }
    readonly property string applicationName: {
        var app = catalog.apps.filter(function(a) { return a.value === editor.bundle })[0]
        return app ? app.label : "尚未添加应用"
    }
    readonly property var availableActions: applicationScope ? catalog.actions : globalOptions
    readonly property var filteredActions: (customDraft && !availableActions.some(function(a) { return a.id === editor.draftId })
        ? availableActions.concat([customDraft]) : availableActions).filter(function(a) {
        var query = search.text.trim().toLowerCase()
        return (a.label + " " + a.path + " " + a.shortcut).toLowerCase().indexOf(query) >= 0
    })
    readonly property bool selectedAvailable: (applicationScope && (!draftId || Boolean(customDraft))) || availableActions.some(function(a) { return a.id === editor.draftId })
    readonly property bool selectedCustom: draftId.indexOf("custom:") === 0
    readonly property bool selectedPreset: catalog.actions.some(function(a) { return a.id === editor.draftId && a.preset === true })
    readonly property var savedAction: saved ? availableActions.filter(function(a) { return a.id === editor.savedId })[0] || null : null
    readonly property bool canSave: editable && dirty && !stale && selectedAvailable
        && (!applicationScope || !catalog.busy || selectedCustom || selectedPreset || !draftId)
    readonly property string globalEffect: {
        var action = globalOptions.filter(function(a) { return a.id === editor.draftId })[0]
        return action ? action.effect : "选择要绑定的全局功能，保存后生效。"
    }
    readonly property string savedStatus: {
        if (!saved) return ""
        if (!applicationScope) return "全局生效 · 应用内不可覆盖此手势"
        if (savedAction && savedAction.preset === true) return savedAction.path + " · 可按应用设置自定义"
        if (savedId.indexOf("custom:") === 0) return "自定义快捷键 · 请与目标应用中的设置保持一致"
        if (catalog.busy) return "正在核对快捷键…"
        if (catalog.menuState === "error" || catalog.menuState === "idle") return "尚未确认可用性 · 原绑定已保留"
        if (savedAction) return savedAction.available === true ? "上次读取时菜单可用"
            : savedAction.available === false ? "上次读取时菜单不可用" : "上次读取未能确认菜单状态"
        return catalog.menuState === "partial" ? "菜单未读全，暂未找到此快捷键" : "本次未找到此快捷键"
    }
    property string feedback: ""
    property bool feedbackError: false
    objectName: "gestureActionEditor"
    radius: 14; color: theme.surface; border.color: theme.line
    implicitHeight: Math.max(664, body.implicitHeight + 40)
    function choose(actionId) {
        if (!editable || stale) return
        if (actionId === draftId) return
        if (actionId === savedId) { discard(); return }
        var next = Object.assign({}, drafts)
        next[contextKey] = {id: actionId, base: savedVersion}
        drafts = next
        feedback = ""
    }
    function discard() {
        var next = Object.assign({}, drafts)
        delete next[contextKey]
        drafts = next
        feedback = ""
    }
    function chooseCustom(action) {
        if (!applicationScope || !editable || stale || !action.id) return
        if (action.id === savedId) { discard(); return }
        var next = Object.assign({}, drafts)
        next[contextKey] = {id: action.id, base: savedVersion, custom: action}
        drafts = next
        feedback = ""
    }
    function draftCount(application) {
        return Object.keys(drafts).filter(function(key) { return key.indexOf(application + "/") === 0 }).length
    }
    function discardApplication(application) {
        var next = Object.assign({}, drafts)
        Object.keys(next).forEach(function(key) {
            if (key.indexOf(application + "/") === 0) delete next[key]
        })
        drafts = next
        if (application === bundle) feedback = ""
    }
    Connections {
        target: editor.catalog
        function onApplicationBindingsCleared(application) { editor.discardApplication(application) }
        function onWebsiteBindingsCleared(application, website) {
            var next = Object.assign({}, editor.drafts)
            var prefix = application + "/video/" + website + "/"
            Object.keys(next).forEach(function(key) { if (key.indexOf(prefix) === 0) delete next[key] })
            editor.drafts = next
        }
        function onDiscoveryChanged() {
            if (editor.applicationScope && (editor.catalog.busy || editor.catalog.menuState === "error" || editor.catalog.menuState === "partial"))
                editor.feedback = ""
        }
    }
    function save() {
        if (!canSave) return
        var success = !applicationScope ? globalController.setGlobalGestureAction(gesture, draftId)
            : customDraft ? catalog.setCustomBinding(bundle, gesture, customDraft.label, customDraft.shortcut)
            : catalog.setBinding(bundle, gesture, draftId)
        if (success) {
            discard()
            feedbackError = false
            feedback = !applicationScope ? "已保存，全局手势和应用占用状态已更新"
                : sceneScope ? "已保存，识别到此应用前台处于「" + catalog.selectedScopeLabel + "」后生效" : "已保存，仅在此应用位于前台时生效"
        } else {
            feedbackError = true
            feedback = applicationScope ? catalog.message : service.notice || "此手势当前不可用，请重新选择"
        }
    }
    onContextKeyChanged: { search.text = ""; feedback = ""; customDialog.close() }
    onApplicationScopeChanged: { feedback = ""; customDialog.close() }

    CustomShortcutDialog {
        id: customDialog
        parent: Overlay.overlay
        service: editor.service
        width: Math.min(440, parent ? parent.width - 40 : 440)
        x: parent ? (parent.width - width) / 2 : 0
        y: parent ? Math.max(20, (parent.height - height) / 2) : 0
        onActionChosen: function(action) { editor.chooseCustom(action) }
    }

    ColumnLayout {
        id: body
        anchors.fill: parent; anchors.margins: 20; spacing: 14
        RowLayout {
            Layout.fillWidth: true
            Label { text: "编辑映射"; font.pixelSize: 21; font.weight: Font.DemiBold; color: theme.text }
            Item { Layout.fillWidth: true }
            Label { visible: editor.dirty; text: "未保存"; font.pixelSize: 11; color: "#986A15" }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 4
            Label { text: editor.gestureTitle; font.pixelSize: 19; font.weight: Font.DemiBold; color: theme.text }
            Label {
                Layout.fillWidth: true
                text: "作用范围：" + (editor.applicationScope ? editor.applicationName + " · " + catalog.selectedScopeLabel : "全局默认")
                font.pixelSize: 12; color: theme.muted; elide: Text.ElideRight
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: editor.editable
            spacing: 10
            Label { text: "绑定映射"; font.pixelSize: 14; font.weight: Font.DemiBold; color: theme.text }
            UiTextField {
                id: search
                objectName: "menuActionSearch"
                Layout.fillWidth: true; implicitHeight: 40
                leftPadding: 38; rightPadding: 12
                hintText: editor.applicationScope ? "搜索动作或快捷键" : "搜索全局功能"
                font.pixelSize: 13; color: theme.text
                background: Rectangle { radius: 7; color: theme.surface; border.color: search.activeFocus ? theme.primary : "#D2DBEB" }
                UiIcon { x: 12; anchors.verticalCenter: parent.verticalCenter; width: 17; height: 17; symbol: "search"; ink: theme.muted }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: editor.applicationScope
                Label { text: "应用快捷键"; color: theme.primary; font.pixelSize: 12 }
                Label { text: catalog.actions.length + " 个快捷键"; color: theme.muted; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                UiAction {
                    objectName: "customShortcutButton"; text: "自定义"; quiet: true; implicitHeight: 30
                    enabled: !editor.stale
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: "为未列出的动作录入快捷键"
                    onClicked: customDialog.open()
                }
                UiAction { objectName: "refreshMenuActionsButton"; text: catalog.busy ? "读取中…" : "刷新"; quiet: true; implicitHeight: 30; enabled: !catalog.busy; onClicked: catalog.refreshMenu() }
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: Math.min(252, Math.max(124, actions.count * 59 + 8))
                radius: 8; color: theme.surface; border.color: "#D2DBEB"
                ListView {
                    id: actions
                    objectName: "menuActionList"
                    anchors.fill: parent; anchors.margins: 4
                    clip: true; spacing: 3
                    model: editor.applicationScope ? [{id: "", label: "无", path: editor.sceneScope ? "移除此场景的动作绑定" : "移除此手势的应用覆盖", shortcut: "", available: true}].concat(editor.filteredActions) : editor.filteredActions
                    ScrollBar.vertical: ScrollBar { }
                    delegate: AbstractButton {
                        id: actionRow
                        required property var modelData
                        objectName: "menuAction_" + modelData.id
                        width: actions.width; height: 56
                        hoverEnabled: true
                        enabled: !editor.stale && (!editor.applicationScope || !catalog.busy || modelData.custom === true || modelData.preset === true || !modelData.id)
                        checked: editor.draftId === modelData.id
                        Accessible.name: modelData.path + " " + modelData.shortcut
                        Accessible.role: Accessible.RadioButton
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: modelData.path + (modelData.preset === true ? "（应用标准快捷键，可按实际设置自定义）" : modelData.custom === true ? "（以目标应用内的快捷键设置为准）" : modelData.available === false ? "（上次读取时菜单不可用，仍可保存绑定）"
                            : modelData.available !== true ? "（菜单状态尚未确认）" : "")
                        onClicked: editor.choose(modelData.id)
                        background: Rectangle { radius: 7; color: actionRow.checked ? "#EDF3FF" : actionRow.hovered ? theme.subtle : "transparent" }
                        contentItem: RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; spacing: 10
                            Rectangle {
                                width: 18; height: 18; radius: 9
                                color: actionRow.checked ? theme.primary : "white"
                                border.color: actionRow.checked ? theme.primary : "#8A98AE"
                                Rectangle { anchors.centerIn: parent; width: 6; height: 6; radius: 3; color: "white"; visible: actionRow.checked }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 3
                                Label { Layout.fillWidth: true; text: modelData.label; color: theme.text; font.pixelSize: 13; elide: Text.ElideRight }
                                Label { Layout.fillWidth: true; text: modelData.custom === true || modelData.preset === true ? modelData.path : modelData.available === false ? "菜单暂不可用 · " + modelData.path : modelData.available !== true ? "状态待确认 · " + modelData.path : modelData.path; color: theme.muted; font.pixelSize: 10; elide: Text.ElideRight }
                            }
                            Label { text: modelData.shortcut; color: theme.muted; font.pixelSize: 11 }
                        }
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                visible: (!editor.applicationScope || !catalog.busy) && editor.filteredActions.length === 0 && search.text.length > 0
                text: editor.applicationScope ? "没有匹配的动作或快捷键" : "没有匹配的全局功能"; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
            }
            Label {
                objectName: "menuReadMessage"
                visible: editor.applicationScope
                Layout.fillWidth: true; text: catalog.message
                color: catalog.menuState === "error" || catalog.menuState === "partial" ? "#A35527" : theme.muted
                font.pixelSize: 11; wrapMode: Text.Wrap
            }
        }
        Rectangle {
            Layout.fillWidth: true
            visible: !editor.editable
            implicitHeight: readOnlyContent.implicitHeight + 28
            radius: 10; color: theme.subtle
            ColumnLayout {
                id: readOnlyContent
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14; spacing: 10
                Label {
                    Layout.fillWidth: true
                    text: !editor.applicationScope ? (editor.voiceLocked ? "语音手势组" : "保留手势")
                        : editor.anchorGesture ? "已被全局占用" : "尚未添加应用"
                    font.weight: Font.DemiBold; color: theme.text; font.pixelSize: 14
                }
                Label {
                    objectName: "selectedGestureDescription"
                    Layout.fillWidth: true; text: editor.currentDescription; wrapMode: Text.Wrap; font.pixelSize: 13; color: theme.muted
                }
                Label {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 12; color: theme.muted
                    text: !editor.applicationScope ? (editor.voiceLocked
                        ? "Tap 和上下左右滑保留给语音输入，不能绑定全局功能。可在应用中覆盖；覆盖任意一个，该应用常规状态下停用整个语音手势组。"
                        : "此手势用于切换输入法，不能绑定全局功能。")
                        : editor.anchorGesture ? "当前用于「" + catalog.globalOccupancy[editor.gesture] + "」。请先在全局默认中更换该功能的手势，再设置应用绑定。"
                        : "在上方栏目选择应用，查看它的菜单快捷键。"
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true; visible: editor.editable && Boolean(editor.saved)
            implicitHeight: savedDetails.implicitHeight + 24
            radius: 8; color: theme.subtle
            ColumnLayout {
                id: savedDetails
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 12; spacing: 6
                Label {
                    objectName: "savedMenuBinding"
                    Layout.fillWidth: true
                    text: "已保存：" + (editor.saved ? editor.saved.label + (editor.saved.shortcut ? " · " + editor.saved.shortcut : "") : "")
                    font.pixelSize: 12; color: theme.text; wrapMode: Text.Wrap
                }
                Label {
                    objectName: "savedMenuBindingStatus"
                    Layout.fillWidth: true; text: editor.savedStatus
                    font.pixelSize: 11; wrapMode: Text.Wrap
                    color: (!editor.applicationScope || !catalog.busy) && editor.savedAction && editor.savedAction.available === true ? theme.success : theme.muted
                }
                Label {
                    Layout.fillWidth: true
                    visible: editor.applicationScope && editor.savedId.indexOf("custom:") !== 0 && !(editor.savedAction && editor.savedAction.preset === true) && !catalog.busy && (!editor.savedAction || editor.savedAction.available !== true)
                    text: "原绑定仍保留。可在目标应用中打开相关窗口或菜单后刷新，也可重新选择动作。"
                    font.pixelSize: 11; color: theme.muted; wrapMode: Text.Wrap
                }
            }
        }
        Label {
            objectName: "menuMappingFeedback"
            Layout.fillWidth: true; visible: text.length > 0
            text: editor.stale ? "绑定已在其他位置更改。请取消草稿后重新选择。"
                : editor.dirty && !editor.selectedAvailable && (!editor.applicationScope || !catalog.busy) ? "所选功能不在当前列表中，请重新选择。" : editor.feedback
            color: editor.stale || editor.feedbackError ? "#A35527" : theme.success
            font.pixelSize: 12; wrapMode: Text.Wrap
        }
        Item { Layout.fillHeight: true; Layout.minimumHeight: 0 }
        Label {
            objectName: "globalBindingEffect"
            Layout.fillWidth: true; visible: !editor.applicationScope && editor.editable
            text: editor.globalEffect; color: editor.dirty ? "#986A15" : theme.muted
            font.pixelSize: 12; wrapMode: Text.Wrap
        }
        Label { Layout.fillWidth: true; visible: editor.editable; text: !editor.applicationScope ? "全局功能优先生效，应用内会自动禁用被占用的手势。Tap 和四向滑动保留为语音组。" : editor.sceneScope ? "仅在前台识别到「" + catalog.selectedSceneLabel + "」、焦点不在文本框且语音空闲时生效。" : editor.voiceLocked ? "保存此覆盖后，该应用常规状态下停用整个语音手势组。其他应用不受影响。" : "输入模式与操作模式均可用，仅在目标应用位于前台时生效"; color: theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
        RowLayout {
            Layout.alignment: Qt.AlignRight; visible: editor.editable; spacing: 10
            UiAction {
                objectName: "saveMenuMappingButton"
                primary: true; symbol: "save"; text: "保存更改"
                enabled: editor.canSave
                onClicked: editor.save()
            }
            UiAction { objectName: "cancelMenuMappingButton"; text: "取消修改"; enabled: editor.dirty || editor.stale; onClicked: editor.discard() }
        }
    }
}
