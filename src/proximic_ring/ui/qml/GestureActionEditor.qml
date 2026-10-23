import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import QtQuick.Window
import QtQuick.Effects
import "GestureActionPresentation.js" as ActionPresentation

Rectangle {
    id: editor
    required property var service
    required property var globalController
    required property string gesture
    required property string gestureTitle
    required property string currentDescription
    required property bool applicationScope
    FigmaTheme { id: theme }
    readonly property var catalog: service.catalog
    readonly property string bundle: catalog.selectedApp
    readonly property bool sceneScope: applicationScope && catalog.selectedScene !== "regular"
    readonly property bool sharedNavigation: applicationScope && bundle.length > 0 && catalog.isBrowser && ["circle-clockwise", "circle-counterclockwise"].indexOf(gesture) >= 0
    readonly property bool sharedSceneNavigation: sharedNavigation && sceneScope
    readonly property bool anchorGesture: Boolean(catalog.globalOccupancy[gesture])
    readonly property var globalOptions: {
        var bindings = globalController.globalBindings
        var voiceBindings = service.profiles
        return globalController.globalActionOptions(gesture)
    }
    readonly property var saved: applicationScope ? (catalog.bindings[bundle] || {})[gesture] || null
        : globalOptions.filter(function(a) { return editor.globalController.globalBindings[a.id] === editor.gesture })[0] || null
    readonly property string pendingDefault: applicationScope ? catalog.pendingBindings[gesture] || "" : ""
    readonly property string savedId: saved ? saved.id : ""
    readonly property string contextKey: applicationScope ? bundle + "/" + catalog.selectedScene + "/" + gesture : "global/" + gesture
    readonly property bool voiceLocked: catalog.voiceGestures.indexOf(gesture) >= 0
    readonly property bool editable: {
        var bindings = service.profiles // Refresh when voice/source settings change.
        var scene = catalog.selectedScene
        var occupied = catalog.globalOccupancy
        return gesture.length > 0 && (applicationScope ? bundle.length > 0 && service.supported && catalog.canBind(gesture) : globalOptions.length > 0)
    }
    readonly property string applicationName: {
        var app = catalog.apps.filter(function(a) { return a.value === editor.bundle })[0]
        return app ? app.label : "尚未添加应用"
    }
    readonly property var bindingActions: applicationScope ? catalog.actions : globalOptions
    readonly property var availableActions: bindingActions.concat(ActionPresentation.systemShortcuts(Qt.platform.os).filter(function(preset) {
        return !editor.bindingActions.some(function(action) { return ActionPresentation.presetFor(action, Qt.platform.os)?.id === preset.id })
    }))
    readonly property string scopeDescription: applicationScope
        ? "生效范围：" + applicationName + " · " + catalog.selectedScopeLabel + "。选择系统组合键也不会改成全局绑定。"
        : "生效范围：全局默认。应用或场景中的独立配置优先；共用手势在输入框内优先执行输入功能。"
    function actionDescription(action) { return ActionPresentation.description(action, applicationScope, applicationName, Qt.platform.os) }
    function isChosen(action) {
        if (action.category) return false
        if (selectedActionId === action.id) return true
        var selection = draft || saved
        return Boolean(action.keyboardPreset && selection && ActionPresentation.presetFor(selection, Qt.platform.os)?.id === action.id)
    }
    readonly property var filteredActions: availableActions.filter(function(a) {
        var query = search.text.trim().toLowerCase()
        var inCategory = category === "all" || (category === "shortcut" ? ActionPresentation.isKeyboard(a) : !ActionPresentation.isKeyboard(a))
        return inCategory && (a.label + " " + a.path + " " + a.shortcut + " " + editor.actionDescription(a)).toLowerCase().indexOf(query) >= 0
    })
    readonly property var savedAction: saved ? availableActions.filter(function(a) { return a.id === editor.savedId })[0] || null : null
    readonly property string globalEffect: {
        var action = globalOptions.filter(function(a) { return a.id === editor.savedId })[0]
        return action ? action.effect : "选择功能后，点击保存更改。"
    }
    readonly property string savedStatus: {
        if (!saved) return ""
        if (sharedNavigation) return "浏览器所有场景共用 · 在默认配置中修改"
        if (!applicationScope) return "应用映射优先 · 输入焦点仅影响冲突手势"
        if (savedAction && savedAction.preset === true) return savedAction.path + " · 可按应用设置自定义"
        if (ActionPresentation.presetFor(saved, Qt.platform.os)) return "macOS 系统组合键 · 如已改键，请录入实际组合键"
        if (savedId.indexOf("custom:") === 0) return "自定义快捷键 · 按当前应用与场景范围生效"
        if (catalog.busy) return "正在核对快捷键…"
        if (catalog.menuState === "error" || catalog.menuState === "idle") return "尚未确认可用性 · 原绑定已保留"
        if (savedAction) return savedAction.available === true ? "上次读取时菜单可用"
            : savedAction.available === false ? "上次读取时菜单不可用" : "上次读取未能确认菜单状态"
        return catalog.menuState === "partial" ? "菜单未读全，暂未找到此快捷键" : "本次未找到此快捷键"
    }
    signal voiceSettingsRequested()
    property string feedback: ""
    property bool feedbackError: false
    // Drafts belong to an application/scene/gesture, so browsing never applies them.
    property var drafts: ({})
    readonly property var draft: drafts[contextKey] || null
    readonly property string selectedActionId: draft ? draft.id : savedId
    readonly property bool dirty: Boolean(draft) && (draft.id !== savedId || Boolean(pendingDefault) || draft.newCustom === true)
    property string category: "all"
    readonly property bool searching: search.text.trim().length > 0
    readonly property var menuRows: {
        if (searching) return filteredActions
        if (category !== "all") return availableActions.filter(function(a) {
            return a.id && (editor.category === "shortcut" ? ActionPresentation.isKeyboard(a) : !ActionPresentation.isKeyboard(a))
        })
        var rows = [{id:"",label:"未配置",path:applicationScope ? "移除应用覆盖，沿用默认配置" : "解除此手势的全局绑定",shortcut:""}]
        rows.push({category:applicationScope ? "application" : "system",label:applicationScope ? "应用快捷键" : "系统功能"})
        if (!applicationScope) rows.push({category:"voice",label:"语音操作"})
        rows.push({category:"shortcut",label:"键盘快捷键"})
        return rows
    }
    readonly property bool compact: width < 800
    objectName: "gestureActionEditor"
    radius: 12; color: theme.surface; border.color: theme.line
    implicitHeight: compact ? 134 : 82
    function actionHint(action) {
        if (!action) return ""
        var id = action.id || ""
        return catalog && catalog.isBrowser && (id.indexOf("web-video:") === 0 || id.indexOf("scene-default:music:") === 0)
            ? "播放器控制" : (action.shortcut || "")
    }
    function replaceDraft(value) {
        var next = Object.assign({}, drafts)
        if (value) next[contextKey] = value
        else delete next[contextKey]
        drafts = next
    }
    function resetDraft() { replaceDraft(null); feedback = ""; feedbackError = false }
    function report(success) {
        feedbackError = !success
        feedback = success ? "已保存更改" : (applicationScope ? catalog.message : service.notice || "此手势当前不可用，请重新选择")
        return success
    }
    function choose(actionId) {
        if (!editable) return
        var action = availableActions.filter(function(a) { return a.id === actionId })[0]
        if (!action && actionId !== "") { feedbackError = true; feedback = "此动作已不在当前菜单中，原绑定已保留。请刷新快捷键后重试。"; return }
        replaceDraft(action && action.keyboardPreset ? Object.assign({}, action, {newCustom:true}) : action || {id:"", label:"未配置"})
        feedback = ""; feedbackError = false
        mappingMenu.close()
    }
    function chooseCustom(action) {
        if (!editable || !action.id) return false
        replaceDraft(Object.assign({}, action, {newCustom:true}))
        feedback = ""; feedbackError = false
        mappingMenu.close()
        return true
    }
    function saveDraft() {
        if (!editable || !dirty) return false
        var value = draft
        var success = value.newCustom
            ? (applicationScope ? catalog.setCustomBinding(bundle, gesture, value.label, value.shortcut)
                                : globalController.setGlobalCustomBinding(gesture, value.label, value.shortcut))
            : (applicationScope ? catalog.setBinding(bundle, gesture, value.id)
                                : globalController.setGlobalGestureAction(gesture, value.id))
        if (report(success)) replaceDraft(null)
        return success
    }
    Connections {
        target: editor.catalog
        function onApplicationBindingsCleared(application) {
            var next = Object.assign({}, editor.drafts)
            Object.keys(next).forEach(function(key) { if (key.indexOf(application + "/") === 0) delete next[key] })
            editor.drafts = next
            if (application === editor.bundle) editor.feedback = ""
        }
    }
    onContextKeyChanged: { search.text = ""; category = "all"; feedback = ""; customDialog.close(); mappingMenu.close() }
    onVisibleChanged: if (!visible) { mappingMenu.close(); customDialog.close() }
    CustomShortcutDialog {
        id: customDialog
        parent: Overlay.overlay
        service: editor.service; globalScope: !editor.applicationScope; deferredSave: true; scopeDescription: editor.scopeDescription
        width: Math.min(440, parent ? parent.width - 40 : 440)
        x: parent ? (parent.width - width) / 2 : 0
        y: parent ? Math.max(20, (parent.height - height) / 2) : 0
        onActionChosen: function(action) {
            if (editor.chooseCustom(action)) customDialog.close()
            else customDialog.errorText = editor.feedback
        }
    }
    Label {
        id: selectedTitle
        objectName: "selectedGestureTitle"
        x: 21; y: editor.compact ? 16 : 25; height: 32
        text: editor.gestureTitle || "请选择"; color: theme.text
        font.family: theme.family; font.pixelSize: 22; font.weight: Font.Medium
        verticalAlignment: Text.AlignVCenter
    }
    RowLayout {
        id: fields
        x: editor.compact ? 21 : selectedTitle.x + selectedTitle.width + 8
        y: editor.compact ? 70 : 21
        width: editor.width - x - 21; height: 40; spacing: 12
        Basic.Button {
            id: mappingField
            objectName: "gestureMappingDropdown"
            Layout.preferredWidth: 296; Layout.fillWidth: editor.compact; Layout.minimumWidth: 160; Layout.preferredHeight: 40
            hoverEnabled: true; padding: 16; topPadding: 0; bottomPadding: 0
            topInset: 0; bottomInset: 0
            Accessible.name: "选择手势映射"
            ToolTip.visible: hovered && Boolean(editor.draft || editor.saved)
            ToolTip.delay: 450
            ToolTip.text: {
                var action = editor.draft || editor.saved
                return action ? action.label + (editor.actionHint(action) ? " · " + editor.actionHint(action) : "") + "\n" + editor.actionDescription(action) : ""
            }
            onClicked: if (editor.editable) { search.text = ""; editor.category = "all"; mappingMenu.open() }
            background: Rectangle { radius: 8; color: mappingField.down ? theme.selection : mappingField.hovered && editor.editable ? theme.subtle : theme.background; border.color: mappingMenu.visible || mappingField.visualFocus ? theme.primary : theme.line }
            contentItem: RowLayout {
                spacing: 8
                Label { Layout.fillWidth: true; text: editor.draft ? (editor.draft.id ? editor.draft.label : "未配置") : editor.saved ? editor.saved.label : editor.pendingDefault || "未配置"; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium; color: theme.text; elide: Text.ElideRight }
                Image { Layout.preferredWidth: 16; Layout.preferredHeight: 16; source: "../assets/figma/redesign-567-33097-caret-down.svg" }
            }
            Basic.Popup {
                id: mappingMenu
                objectName: "gestureMappingMenu"
                popupType: Popup.Item; parent: mappingField
                width: Math.min(440, editor.Window.width - 48); height: Math.min(520, editor.Window.height - 80, menuBody.implicitHeight + 24)
                // The mapping field sits at the bottom of the workbench. Open above it
                // whenever the available window space cannot contain the anchored menu.
                y: -height - 8
                padding: 12; margins: 12; focus: true
                closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                background: Rectangle {
                    radius: 12; color: theme.surface; border.color: theme.line
                    layer.enabled: true
                    layer.effect: MultiEffect { shadowEnabled: true; shadowBlur: 0.5; shadowOpacity: 0.25; shadowColor: "#7B8494"; shadowVerticalOffset: 0; shadowHorizontalOffset: 0 }
                }
                enter: Transition { NumberAnimation { property:"opacity"; from:0; to:1; duration:100 } }
                exit: Transition { NumberAnimation { property:"opacity"; from:1; to:0; duration:80 } }
                contentItem: ColumnLayout {
                    id: menuBody
                    spacing: 8
                    FigmaAction { objectName: "gestureMenuBack"; visible: editor.category !== "all"; Layout.fillWidth: true; implicitHeight: 36; quiet: true; text:"返回全部功能"; onClicked: { editor.category="all"; search.text="" } }
                    Label {
                        objectName: "gestureMappingScopeDescription"
                        Layout.fillWidth: true; text: editor.scopeDescription
                        color: theme.muted; font.family: theme.family; font.pixelSize: 13; wrapMode: Text.Wrap
                    }
                    UiTextField {
                        id: search
                        objectName: "menuActionSearch"
                        visible: true
                        Layout.fillWidth: true; implicitHeight: 40
                        hintText: editor.category === "application" ? "搜索应用功能或组合键" : editor.category === "shortcut" ? "搜索系统或录制快捷键" : "搜索功能或快捷键"; placeholderText: hintText; font.family: theme.family; font.pixelSize: 16
                        leftPadding:16; color: theme.text
                        background: Rectangle { radius:8; color:theme.background; border.color: search.activeFocus ? theme.primary : theme.line }
                    }
                    ListView {
                        id: actions
                        objectName: "menuActionList"
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredHeight: editor.category === "all" && !editor.searching ? contentHeight : Math.min(288, contentHeight); Layout.minimumHeight: count ? 76 : 0
                        clip: true; spacing: 4; model: editor.menuRows
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        delegate: Basic.Button {
                            id: actionRow
                            required property var modelData
                            objectName: modelData.category ? "gestureCategory_" + modelData.category : "menuAction_" + modelData.id
                            width: actions.width; height: Math.max(76, rowContent.implicitHeight + 20); padding:12; topPadding:10; bottomPadding:10
                            topInset:0; bottomInset:0; hoverEnabled:true
                            enabled: Boolean(modelData.category) || !editor.applicationScope || !catalog.busy || modelData.custom === true || modelData.preset === true || modelData.keyboardPreset === true || !modelData.id
                            opacity: enabled ? 1 : 0.45
                            checked: editor.isChosen(modelData)
                            Accessible.name: modelData.label + " " + editor.actionHint(modelData) + " " + editor.actionDescription(modelData)
                            ToolTip.visible: hovered; ToolTip.delay:450
                            ToolTip.text: modelData.label + (editor.actionHint(modelData) ? " · " + editor.actionHint(modelData) : "") + "\n" + editor.actionDescription(modelData)
                            onClicked: {
                                if (modelData.category === "voice") { mappingMenu.close(); editor.voiceSettingsRequested() }
                                else if (modelData.category) editor.category = modelData.category
                                else editor.choose(modelData.id)
                            }
                            background: Rectangle { radius:8; color: actionRow.down || actionRow.checked ? theme.selection : actionRow.hovered ? theme.subtle : theme.surface; border.width: actionRow.visualFocus ? 1 : 0; border.color:theme.primary }
                            contentItem: ColumnLayout {
                                id: rowContent
                                spacing: 4
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 12
                                    Label { Layout.fillWidth: true; text: modelData.label === "无" ? "未配置" : modelData.label; color: theme.text; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium; wrapMode: Text.Wrap }
                                    Label { objectName: "menuActionShortcut_" + (modelData.id || modelData.category); visible: !modelData.category && text.length > 0; text: editor.actionHint(modelData); color: theme.primary; font.family: theme.family; font.pixelSize: 14; font.weight: Font.Medium }
                                    FigmaIcon { visible: Boolean(modelData.category); symbol: "chevron-right"; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                                }
                                Label {
                                    objectName: "menuActionDescription_" + (modelData.id || modelData.category)
                                    Layout.fillWidth: true; text: editor.actionDescription(modelData)
                                    color: theme.muted; font.family: theme.family; font.pixelSize: 13; wrapMode: Text.Wrap
                                }
                            }
                        }
                    }
                    FigmaAction {
                        objectName: "customShortcutButton"
                        visible: editor.category === "shortcut" || (editor.category === "all" && editor.searching)
                        Layout.fillWidth: true; text: "录制自己的快捷键"; onClicked: { mappingMenu.close(); customDialog.open() }
                    }
                    FigmaAction { objectName:"refreshMenuActionsButton"; visible:editor.applicationScope && editor.category === "application"; Layout.fillWidth:true; text:catalog.busy ? "读取中…" : "刷新快捷键"; quiet:true; enabled:!catalog.busy; onClicked:catalog.refreshMenu() }
                    Label {
                        Layout.fillWidth: true; color:theme.muted; font.family:theme.family; font.pixelSize:14; wrapMode:Text.Wrap
                        text: editor.searching && !actions.count ? (editor.category === "application" ? "没有匹配的应用功能。可刷新菜单，或返回后选择「键盘快捷键」自行录制。" : "没有匹配的功能，可录入自己的快捷键。") : editor.category === "shortcut" ? "Cmd = ⌘ · Ctrl = ⌃ · Alt = ⌥ · Shift = ⇧\n选择或录入后，点击保存更改生效。" : editor.category === "all" ? "按类别展开，或搜索名称、组合键和说明。" : editor.applicationScope ? catalog.message : "选择后点击保存更改"
                    }
                }
            }
        }
        FigmaAction { objectName:"resetMenuMappingButton"; Layout.preferredWidth:112; Layout.preferredHeight:40; font.pixelSize:16; text:"还原"; onClicked:editor.resetDraft() }
        FigmaAction { objectName:"saveMenuMappingButton"; Layout.preferredWidth:172; Layout.preferredHeight:40; font.pixelSize:16; primary:true; text:"保存更改"; enabled:editor.editable && editor.dirty; onClicked:editor.saveDraft() }
        Item { visible:!editor.compact; Layout.fillWidth:true }
    }
}
