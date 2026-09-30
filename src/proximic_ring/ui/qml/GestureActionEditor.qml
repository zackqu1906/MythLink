import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: editor
    required property var service
    required property string gesture
    required property string gestureTitle
    required property string currentDescription
    required property bool applicationScope
    UiTheme { id: theme }
    readonly property var catalog: service.catalog
    readonly property string bundle: catalog.selectedApp
    readonly property bool presentationScope: applicationScope && catalog.selectedScene === "presentation"
    readonly property bool anchorGesture: gesture === "index-pinch" || gesture === "middle-pinch"
    readonly property var saved: (catalog.bindings[bundle] || {})[gesture] || null
    readonly property string savedId: saved ? saved.id : ""
    readonly property string contextKey: bundle + "/" + catalog.selectedScene + "/" + gesture
    // Drafts survive a gesture/app/page switch, but never alter live bindings.
    property var drafts: ({})
    readonly property string draftId: drafts[contextKey] ? drafts[contextKey].id : savedId
    readonly property var customDraft: drafts[contextKey] ? drafts[contextKey].custom || null : null
    readonly property bool dirty: draftId !== savedId
    readonly property bool stale: Boolean(drafts[contextKey] && drafts[contextKey].base !== savedId)
    readonly property bool voiceLocked: ["tap", "swipe-left", "swipe-right"].indexOf(gesture) >= 0
    readonly property bool editable: {
        var bindings = service.profiles // Refresh when voice/source settings change.
        var scene = catalog.selectedScene
        return applicationScope && bundle.length > 0 && service.supported && catalog.canBind(gesture)
    }
    readonly property string applicationName: {
        var app = catalog.apps.filter(function(a) { return a.value === editor.bundle })[0]
        return app ? app.label : "尚未添加应用"
    }
    readonly property var filteredActions: (customDraft && !catalog.actions.some(function(a) { return a.id === editor.draftId })
        ? catalog.actions.concat([customDraft]) : catalog.actions).filter(function(a) {
        var query = search.text.trim().toLowerCase()
        return (a.label + " " + a.path + " " + a.shortcut).toLowerCase().indexOf(query) >= 0
    })
    readonly property bool selectedAvailable: !draftId || Boolean(customDraft) || catalog.actions.some(function(a) { return a.id === editor.draftId })
    readonly property bool selectedCustom: draftId.indexOf("custom:") === 0
    readonly property bool selectedPreset: catalog.actions.some(function(a) { return a.id === editor.draftId && a.preset === true })
    readonly property var savedAction: saved ? catalog.actions.filter(function(a) { return a.id === editor.savedId })[0] || null : null
    readonly property string savedStatus: {
        if (!saved) return ""
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
        next[contextKey] = {id: actionId, base: savedId}
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
        if (!editable || stale || !action.id) return
        if (action.id === savedId) { discard(); return }
        var next = Object.assign({}, drafts)
        next[contextKey] = {id: action.id, base: savedId, custom: action}
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
        function onDiscoveryChanged() {
            if (editor.catalog.busy || editor.catalog.menuState === "error" || editor.catalog.menuState === "partial")
                editor.feedback = ""
        }
    }
    function save() {
        if (!editable || !dirty || stale || !selectedAvailable || (catalog.busy && !selectedCustom && !selectedPreset && draftId)) return
        var success = customDraft ? catalog.setCustomBinding(bundle, gesture, customDraft.label, customDraft.shortcut)
            : catalog.setBinding(bundle, gesture, draftId)
        if (success) {
            discard()
            feedbackError = false
            feedback = presentationScope ? "已保存，识别到此应用前台放映后生效" : "已保存，仅在此应用位于前台时生效"
        } else {
            feedbackError = true
            feedback = catalog.message
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
                text: "作用范围：" + (editor.applicationScope ? editor.applicationName + (editor.presentationScope ? " · 放映" : " · 常规") : "全局默认")
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
                hintText: "搜索动作或快捷键"
                font.pixelSize: 13; color: theme.text
                background: Rectangle { radius: 7; color: theme.surface; border.color: search.activeFocus ? theme.primary : "#D2DBEB" }
                UiIcon { x: 12; anchors.verticalCenter: parent.verticalCenter; width: 17; height: 17; symbol: "search"; ink: theme.muted }
            }
            RowLayout {
                Layout.fillWidth: true
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
                    model: [{id: "", label: editor.presentationScope ? "沿用默认" : "无", path: editor.presentationScope ? "移除此场景覆盖，恢复原来的用途" : "不绑定应用快捷键", shortcut: "", available: true}].concat(editor.filteredActions)
                    ScrollBar.vertical: ScrollBar { }
                    delegate: AbstractButton {
                        id: actionRow
                        required property var modelData
                        objectName: "menuAction_" + modelData.id
                        width: actions.width; height: 56
                        hoverEnabled: true
                        enabled: !editor.stale && (!catalog.busy || modelData.custom === true || modelData.preset === true || !modelData.id)
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
                visible: !catalog.busy && editor.filteredActions.length === 0 && search.text.length > 0
                text: "没有匹配的动作或快捷键"; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
            }
            Label {
                objectName: "menuReadMessage"
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
                    text: editor.anchorGesture ? "始终保留" : !editor.applicationScope ? "当前全局用途" : editor.voiceLocked ? "语音默认用途" : !editor.bundle ? "尚未添加应用" : "现有交互 · 只读"
                    font.weight: Font.DemiBold; color: theme.text; font.pixelSize: 14
                }
                Label {
                    objectName: "selectedGestureDescription"
                    Layout.fillWidth: true; text: editor.currentDescription; wrapMode: Text.Wrap; font.pixelSize: 13; color: theme.muted
                }
                Label {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 12; color: theme.muted
                    text: editor.anchorGesture ? "手势提示和切换交互模式在所有场景中保留。"
                        : !editor.applicationScope ? "全局保留现有用途。在上方选择应用，可为支持的场景单独绑定动作。"
                        : !editor.bundle ? "在上方栏目选择应用，查看它的菜单快捷键。"
                        : catalog.supportsPresentation ? "常规场景保留此手势。在上方选择「放映」，可配置放映时的专属动作。"
                        : "常规场景保留此手势。该应用的场景识别暂未接入。"
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
                    text: "已保存：" + (editor.saved ? editor.saved.label + " · " + editor.saved.shortcut : "")
                    font.pixelSize: 12; color: theme.text; wrapMode: Text.Wrap
                }
                Label {
                    objectName: "savedMenuBindingStatus"
                    Layout.fillWidth: true; text: editor.savedStatus
                    font.pixelSize: 11; wrapMode: Text.Wrap
                    color: !catalog.busy && editor.savedAction && editor.savedAction.available === true ? theme.success : theme.muted
                }
                Label {
                    Layout.fillWidth: true
                    visible: editor.savedId.indexOf("custom:") !== 0 && !(editor.savedAction && editor.savedAction.preset === true) && !catalog.busy && (!editor.savedAction || editor.savedAction.available !== true)
                    text: "原绑定仍保留。可在目标应用中打开相关窗口或菜单后刷新，也可重新选择动作。"
                    font.pixelSize: 11; color: theme.muted; wrapMode: Text.Wrap
                }
            }
        }
        Label {
            objectName: "menuMappingFeedback"
            Layout.fillWidth: true; visible: text.length > 0
            text: editor.stale ? "绑定已在其他位置更改。请取消草稿后重新选择。"
                : editor.dirty && !editor.selectedAvailable && !catalog.busy ? "所选快捷键不在当前列表中，请刷新后重新选择。" : editor.feedback
            color: editor.stale || editor.feedbackError ? "#A35527" : theme.success
            font.pixelSize: 12; wrapMode: Text.Wrap
        }
        Item { Layout.fillHeight: true; Layout.minimumHeight: 0 }
        Label { Layout.fillWidth: true; visible: editor.editable; text: editor.presentationScope ? "仅在前台放映、焦点不在文本框且语音空闲时生效。未识别到放映时沿用默认用途。" : "输入模式与操作模式均可用，仅在目标应用位于前台时生效"; color: theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
        RowLayout {
            Layout.fillWidth: true; visible: editor.editable; spacing: 10
            UiAction {
                objectName: "saveMenuMappingButton"
                Layout.fillWidth: true; primary: true; symbol: "save"; text: "保存更改"
                enabled: editor.dirty && !editor.stale && editor.selectedAvailable && (!catalog.busy || editor.selectedCustom || editor.selectedPreset || !editor.draftId)
                onClicked: editor.save()
            }
            UiAction { objectName: "cancelMenuMappingButton"; text: "取消修改"; enabled: editor.dirty || editor.stale; onClicked: editor.discard() }
        }
    }
}
