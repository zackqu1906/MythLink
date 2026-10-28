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
    required property var voiceController
    property bool voiceScope: false
    required property string gesture
    required property string gestureTitle
    required property string currentDescription
    required property bool applicationScope
    onApplicationScopeChanged: if (applicationScope) voiceScope = false
    FigmaTheme { id: theme }
    readonly property var catalog: service.catalog
    readonly property var history: voiceController.gestureMappingHistory
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
    readonly property var saved: voiceScope ? currentVoiceAction : applicationScope ? (catalog.bindings[bundle] || {})[gesture] || null
        : globalOptions.filter(function(a) { return editor.globalController.globalBindings[a.id] === editor.gesture })[0] || null
    readonly property var currentVoiceAction: {
        var bindings = voiceController.gestureBindings
        var action = voiceController.voiceGestureAction(gesture)
        return action.id ? action : null
    }
    readonly property string pendingDefault: applicationScope && !voiceScope ? catalog.pendingBindings[gesture] || "" : ""
    readonly property string savedId: saved ? saved.id : ""
    readonly property string contextKey: voiceScope ? "voice/" + gesture : applicationScope ? bundle + "/" + catalog.selectedScene + "/" + gesture : "global/" + gesture
    readonly property bool voiceLocked: catalog.voiceGestures.indexOf(gesture) >= 0
    readonly property bool editable: {
        var bindings = service.profiles // Refresh when voice/source settings change.
        var scene = catalog.selectedScene
        var occupied = catalog.globalOccupancy
        return gesture.length > 0 && (voiceScope || (applicationScope ? bundle.length > 0 && service.supported && catalog.canBind(gesture) : globalOptions.length > 0))
    }
    readonly property string applicationName: {
        var app = catalog.apps.filter(function(a) { return a.value === editor.bundle })[0]
        return app ? app.label : "尚未添加应用"
    }
    readonly property var bindingActions: applicationScope ? catalog.actions : globalOptions
    readonly property var availableActions: bindingActions.concat(ActionPresentation.systemShortcuts(Qt.platform.os).filter(function(preset) {
        return !editor.bindingActions.some(function(action) { return ActionPresentation.presetFor(action, Qt.platform.os)?.id === preset.id })
    })).concat(applicationScope ? [] : voiceController.voiceGestureActions)
    readonly property string scopeDescription: applicationScope
        ? "生效范围：" + applicationName + " · " + catalog.selectedScopeLabel + "。选择系统组合键也不会改成全局绑定。"
        : "生效范围：全局默认。应用或场景中的独立配置优先；共用手势在输入框内优先执行输入功能。"
    function actionDescription(action) { return ActionPresentation.description(action, applicationScope, applicationName, Qt.platform.os) }
    function isChosen(action) {
        if (action.category) return false
        if (selectedActionId === action.id) return true
        var selection = saved
        return Boolean(action.keyboardPreset && selection && ActionPresentation.presetFor(selection, Qt.platform.os)?.id === action.id)
    }
    readonly property var filteredActions: availableActions.filter(function(a) {
        var query = search.text.trim().toLowerCase()
        var inCategory = matchesCategory(a)
        return inCategory && (a.label + " " + a.path + " " + a.shortcut + " " + editor.actionDescription(a)).toLowerCase().indexOf(query) >= 0
    })
    readonly property var savedAction: saved ? availableActions.filter(function(a) { return a.id === editor.savedId })[0] || null : null
    readonly property string globalEffect: {
        if (voiceScope) return "输入框内优先执行语音操作；应用和全局配置独立保留。"
        var action = globalOptions.filter(function(a) { return a.id === editor.savedId })[0]
        return action ? action.effect : "选择后自动保存并生效。"
    }
    readonly property string savedStatus: {
        if (!saved) return ""
        if (voiceScope) return "语音输入 · 与应用、全局映射独立保存"
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
    property string feedback: ""
    property bool feedbackError: false
    readonly property string selectedActionId: savedId
    property string category: "all"
    readonly property bool searching: search.text.trim().length > 0
    readonly property var menuRows: {
        if (searching) return filteredActions
        if (category !== "all") return availableActions.filter(function(a) { return a.id && editor.matchesCategory(a) })
        var rows = [{id:"",label:"未配置",path:applicationScope ? "移除应用覆盖，沿用默认配置" : "解除此手势的全局绑定",shortcut:""}]
        rows.push({category:applicationScope ? "application" : "system",label:applicationScope ? "应用快捷键" : "系统功能"})
        if (!applicationScope) rows.push({category:"voice",label:"语音操作"})
        rows.push({category:"shortcut",label:"键盘快捷键"})
        return rows
    }
    readonly property bool compact: width < 800
    objectName: "gestureActionEditor"
    radius: 12; color: theme.surface; border.color: theme.line
    implicitHeight: (compact ? 134 : 82) + (feedbackError ? saveError.implicitHeight + 12 : 0)
    function matchesCategory(action) {
        if (category === "all") return true
        if (category === "voice") return Boolean(action.voiceAction)
        if (action.voiceAction) return false
        if (category === "system") return !ActionPresentation.isKeyboard(action) || action.systemShortcut === true || Boolean(ActionPresentation.presetFor(action, Qt.platform.os)) || ActionPresentation.isSystemMenu(action)
        return category === "shortcut" ? ActionPresentation.isKeyboard(action) : !ActionPresentation.isKeyboard(action)
    }
    function actionEnabled(action) {
        if (action.category) return true
        if (action.voiceAction) return !applicationScope
        return !applicationScope || (!sharedSceneNavigation && (!catalog.busy || action.custom === true || action.preset === true || action.keyboardPreset === true || !action.id))
    }
    function actionHint(action) {
        if (!action) return ""
        if (ActionPresentation.isBrowserControl(action)) return ""
        return action.shortcut || ""
    }
    function report(success) {
        feedbackError = !success
        feedback = success ? "已自动保存" : history.error || "此手势当前不可用，请重新选择"
        return success
    }
    function saveAction(action) {
        return report(history.apply(voiceScope ? "voice" : applicationScope ? "application" : "global",
                                    bundle, catalog.selectedScene, gesture, action))
    }
    function undoLastChange() {
        if (report(history.undo(contextKey))) feedback = "已撤销上一步"
    }
    function choose(actionId) {
        if (!gesture.length) return
        var action = availableActions.filter(function(a) { return a.id === actionId })[0]
        if (!action && actionId !== "") { feedbackError = true; feedback = "此动作已不在当前菜单中，原绑定已保留。请刷新快捷键后重试。"; return }
        if (action && !actionEnabled(action)) return
        if (actionId) voiceScope = Boolean(action.voiceAction)
        if (!editable) return
        saveAction(action && action.keyboardPreset ? Object.assign({}, action, {newCustom:true}) : action || {id:"", label:"未配置"})
        mappingMenu.close()
    }
    function chooseCustom(action) {
        voiceScope = false
        if (!editable || !action.id) return false
        if (!saveAction(Object.assign({}, action, {newCustom:true}))) return false
        mappingMenu.close()
        return true
    }
    onContextKeyChanged: { search.text = ""; category = "all"; feedback = ""; feedbackError = false; customDialog.close(); mappingMenu.close() }
    onVisibleChanged: if (!visible) { mappingMenu.close(); customDialog.close() }
    CustomShortcutDialog {
        id: customDialog
        parent: Overlay.overlay
        service: editor.service; globalScope: !editor.applicationScope; scopeDescription: editor.scopeDescription
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
            ToolTip.visible: hovered && Boolean(editor.saved)
            ToolTip.delay: 450
            ToolTip.text: {
                var action = editor.saved
                return action ? action.label + (editor.actionHint(action) ? " · " + editor.actionHint(action) : "") + "\n" + editor.actionDescription(action) : ""
            }
            onClicked: if (editor.gesture.length) { search.text = ""; editor.category = editor.voiceScope ? "voice" : "all"; mappingMenu.open() }
            background: Rectangle { radius: 8; color: mappingField.down ? theme.selection : mappingField.hovered && editor.editable ? theme.subtle : theme.background; border.color: mappingMenu.visible || mappingField.visualFocus ? theme.primary : theme.line }
            contentItem: RowLayout {
                spacing: 8
                Label { Layout.fillWidth: true; text: editor.saved ? editor.saved.label : editor.pendingDefault || "未配置"; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium; color: theme.text; elide: Text.ElideRight }
                Image { Layout.preferredWidth: 16; Layout.preferredHeight: 16; source: "../assets/figma/redesign-567-33097-caret-down.svg" }
            }
            Basic.Popup {
                id: mappingMenu
                objectName: "gestureMappingMenu"
                popupType: Popup.Item; parent: mappingField
                width: 296
                height: Math.min(editor.Window.height - 80, editor.category === "shortcut" ? 196 : editor.category === "all" && !editor.searching ? (editor.applicationScope ? 244 : 292) : menuBody.implicitHeight + 24)
                // The mapping field sits at the bottom of the workbench. Open above it
                // whenever the available window space cannot contain the anchored menu.
                y: mappingField.mapToItem(null, 0, mappingField.height).y + 8 + height <= editor.Window.height - 12 ? mappingField.height + 8 : -height - 8
                padding: 12; margins: 12; focus: true
                closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                background: Rectangle {
                    radius: 12; color: theme.surface; border.color: theme.line
                    layer.enabled: true
                    layer.effect: MultiEffect { shadowEnabled: true; shadowBlur: 0.5; shadowOpacity: 0.25; shadowColor: "#7B8494"; shadowVerticalOffset: 0; shadowHorizontalOffset: 0 }
                }
                enter: Transition { NumberAnimation { property:"opacity"; from:0; to:1; duration:100 } }
                exit: Transition { NumberAnimation { property:"opacity"; from:1; to:0; duration:80 } }
                onAboutToHide: { inlineShortcut.focus = false; editor.service.recording = false }
                contentItem: ColumnLayout {
                    id: menuBody
                    spacing: 8
                    Basic.Button {
                        id: backButton
                        objectName: "gestureMenuBack"; visible: editor.category !== "all"
                        Layout.fillWidth: true; Layout.preferredHeight: 36; Layout.maximumHeight: 36
                        padding: 12; topPadding: 0; bottomPadding: 0; topInset: 0; bottomInset: 0; hoverEnabled: true
                        text: "返回全部功能"
                        background: Rectangle { radius: 8; color: backButton.down ? theme.selection : backButton.hovered ? theme.subtle : theme.surface }
                        contentItem: Label { text: backButton.text; color: theme.text; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium; verticalAlignment: Text.AlignVCenter }
                        onClicked: { editor.category="all"; search.text="" }
                    }
                    Label {
                        objectName: "gestureMappingScopeDescription"
                        visible: false
                        Layout.fillWidth: true; text: editor.scopeDescription
                        color: theme.muted; font.family: theme.family; font.pixelSize: 13; wrapMode: Text.Wrap
                    }
                    UiTextField {
                        id: search
                        objectName: "menuActionSearch"
                        visible: editor.category === "all" || editor.category === "application"
                        Layout.fillWidth: true; implicitHeight: 40
                        hintText: editor.category === "application" ? "搜索应用功能或组合键" : editor.category === "shortcut" ? "搜索系统或录制快捷键" : "搜索功能或快捷键"; placeholderText: hintText; font.family: theme.family; font.pixelSize: 16
                        leftPadding:16; color: theme.text
                        background: Rectangle { radius:8; color:theme.background; border.color: search.activeFocus ? theme.primary : theme.line }
                    }
                    ListView {
                        id: actions
                        objectName: "menuActionList"
                        visible: editor.category !== "shortcut"
                        Layout.fillWidth: true; Layout.preferredHeight: Math.min(332, contentHeight); Layout.minimumHeight: count ? 44 : 0
                        clip: true; spacing: 4; model: editor.menuRows
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        delegate: Basic.Button {
                            id: actionRow
                            required property var modelData
                            objectName: modelData.category ? "gestureCategory_" + modelData.category : "menuAction_" + modelData.id
                            width: actions.width; height: 44; padding:12; topPadding:0; bottomPadding:0
                            topInset:0; bottomInset:0; hoverEnabled:true
                            enabled: editor.actionEnabled(modelData)
                            opacity: enabled ? 1 : 0.45
                            checked: modelData.voiceAction && !editor.voiceScope ? Boolean(editor.currentVoiceAction && editor.currentVoiceAction.id === modelData.id) : editor.isChosen(modelData)
                            Accessible.name: modelData.label + " " + editor.actionHint(modelData) + " " + editor.actionDescription(modelData)
                            ToolTip.visible: hovered; ToolTip.delay:450
                            ToolTip.text: modelData.label + (editor.actionHint(modelData) ? " · " + editor.actionHint(modelData) : "") + "\n" + editor.actionDescription(modelData)
                            onClicked: {
                                if (modelData.category) { search.text = ""; editor.category = modelData.category }
                                else editor.choose(modelData.id)
                            }
                            background: Rectangle { radius:8; color: actionRow.down || actionRow.checked ? theme.selection : actionRow.hovered ? theme.subtle : theme.surface; border.width: actionRow.visualFocus ? 1 : 0; border.color:theme.primary }
                            contentItem: ColumnLayout {
                                id: rowContent
                                spacing: 4
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 12
                                    Label { Layout.fillWidth: true; text: modelData.label === "无" ? "未配置" : modelData.label; color: theme.text; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium; elide: Text.ElideRight }
                                    Label { objectName: "menuActionShortcut_" + (modelData.id || modelData.category); Layout.maximumWidth: 150; visible: !modelData.category && text.length > 0; text: editor.actionHint(modelData); color: theme.muted; font.family: theme.family; font.pixelSize: 14; elide: Text.ElideRight }
                                    FigmaIcon { visible: Boolean(modelData.category); symbol: "chevron-right"; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                                }
                                Label {
                                    objectName: "menuActionDescription_" + (modelData.id || modelData.category)
                                    visible: false
                                    Layout.fillWidth: true; text: editor.actionDescription(modelData)
                                    color: theme.muted; font.family: theme.family; font.pixelSize: 13; wrapMode: Text.Wrap
                                }
                            }
                        }
                    }
                    Label {
                        visible: editor.category === "shortcut"
                        text: "键盘快捷键"; font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium; color: theme.text
                    }
                    ShortcutField {
                        id: inlineShortcut
                        objectName: "gestureShortcutRecorder"
                        visible: editor.category === "shortcut"
                        Layout.fillWidth: true; implicitHeight: 40
                        service: editor.service
                        sequence: editor.saved && editor.saved.shortcut ? editor.saved.shortcut : ""
                        hintText: "点击录制组合键"; font.family: theme.family; font.pixelSize: 14; leftPadding: 16
                        color: theme.text
                        background: Rectangle { radius: 8; color: theme.background; border.color: inlineShortcut.activeFocus ? theme.primary : theme.line }
                        onRecorded: function(value) {
                            var action = editor.catalog.customAction(value, value)
                            if (action.id) editor.chooseCustom(action)
                        }
                    }
                    Label {
                        visible: editor.category === "shortcut"
                        Layout.fillWidth: true; text: editor.service.recordingError || (editor.feedbackError ? editor.feedback : "录制完成后自动保存。") ; font.family: theme.family; font.pixelSize: 14; color: theme.muted; wrapMode: Text.Wrap
                    }
                    FigmaAction {
                        objectName: "customShortcutButton"
                        visible: editor.category === "all" && editor.searching
                        Layout.fillWidth: true; text: "录制自己的快捷键"; onClicked: { mappingMenu.close(); customDialog.open() }
                    }
                    FigmaAction { objectName:"refreshMenuActionsButton"; visible:editor.applicationScope && editor.category === "application"; Layout.fillWidth:true; text:catalog.busy ? "读取中…" : "刷新快捷键"; quiet:true; enabled:!catalog.busy; onClicked:catalog.refreshMenu() }
                    Label {
                        visible: editor.category === "all" || (editor.searching && !actions.count)
                        Layout.fillWidth: true; color:theme.muted; font.family:theme.family; font.pixelSize:14; wrapMode:Text.Wrap
                        text: editor.searching && !actions.count ? "没有匹配的功能或快捷键" : "按类别展开，或直接搜索"
                    }
                }
            }
        }
        FigmaAction {
            objectName:"undoMenuMappingButton"; Layout.preferredWidth:140; Layout.preferredHeight:40
            font.pixelSize:16; text:"撤销上一步"
            enabled: editor.history.canUndo && editor.history.contextKey === editor.contextKey
            ToolTip.visible: hovered; ToolTip.text: "撤销当前手势最近一次修改，不会恢复默认配置。"
            onClicked: editor.undoLastChange()
        }
        Label {
            objectName: "gestureAutoSaveStatus"; Layout.preferredWidth: 100
            text: editor.feedbackError ? "未保存" : editor.feedback || "自动保存"
            color: editor.feedbackError ? theme.warning : theme.muted; font.family: theme.family; font.pixelSize: 14
        }
        Item { visible:!editor.compact; Layout.fillWidth:true }
    }
    Label {
        id: saveError; objectName: "gestureAutoSaveError"
        visible: editor.feedbackError; x: 21; y: editor.compact ? 118 : 70; width: parent.width - 42
        text: editor.feedback; wrapMode: Text.Wrap; color: theme.warning; font.family: theme.family; font.pixelSize: 14
    }
}
