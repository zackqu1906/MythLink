import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Templates as T

Rectangle {
    id: panel
    required property var service
    property bool dragging: false
    property string draggedId: ""
    property string draggedWord: ""
    property real draggedWidth: 80
    property real dragAreaHeight: 128
    readonly property int chipHeight: 40
    readonly property int chipGap: 12
    readonly property int actionOverhang: 18
    property int hoverIndex: -1
    property int dragIndex: -1
    property int pressIndex: -1
    property real pressX: 0
    property real pressY: 0
    property real grabX: 0
    property real grabY: 0
    property bool suppressRelease: false
    property real pointerX: 0
    property real pointerY: 0
    property var initialOrder: []
    readonly property var hoveredTag: hoverIndex >= 0 && hoverIndex < tags.count ? tags.itemAt(hoverIndex) : null
    readonly property bool searching: search.text.trim().length > 0
    signal closeRequested()
    implicitHeight: content.implicitHeight + 40
    radius: 12
    color: theme.surface
    border.color: theme.line
    FigmaTheme { id: theme }
    ListModel { id: vocabulary }

    function ids() {
        let result = []
        for (let i = 0; i < vocabulary.count; ++i) result.push(vocabulary.get(i).wordId)
        return result
    }
    function sync(force) {
        if (dragging || ((pointer.pressed || editButton.pressed || deleteButton.pressed) && !force)) return
        const hoveredId = hoveredTag ? hoveredTag.wordId : ""
        const query = search.text.trim().toLowerCase()
        const entries = service.entries
        let target = 0
        for (let i = 0; i < entries.length; ++i) {
            const e = entries[i]
            if (query && !e.word.toLowerCase().includes(query)) continue
            const data = {wordId: e.id, word: e.word, source: e.source,
                wordEnabled: e.enabled, selected: e.selected, position: i + 1}
            let found = -1
            for (let j = target; j < vocabulary.count; ++j)
                if (vocabulary.get(j).wordId === e.id) { found = j; break }
            if (found < 0) vocabulary.insert(target, data)
            else {
                if (found !== target) vocabulary.move(found, target, 1)
                vocabulary.set(target, data)
            }
            ++target
        }
        if (vocabulary.count > target) vocabulary.remove(target, vocabulary.count - target)
        hoverIndex = -1
        for (let i = 0; i < vocabulary.count; ++i)
            if (vocabulary.get(i).wordId === hoveredId) { hoverIndex = i; break }
    }
    function updateHover(x, y) {
        if (dragging || renameDialog.visible || editButton.pressed || deleteButton.pressed) return
        if (hoverActions.containsPoint(x, y)) { hoverExit.stop(); return }
        const index = words.indexAt(x, y + words.contentY)
        if (index >= 0) { hoverExit.stop(); hoverIndex = index; return }
        if (hoverActions.containsInteractionPoint(x, y)) { hoverExit.stop(); return }
        if (!hoverExit.running) hoverExit.start()
    }
    function preview() {
        const active = service.previewSelection(ids())
        for (let i = 0; i < vocabulary.count; ++i)
            vocabulary.setProperty(i, "selected", active.indexOf(vocabulary.get(i).word) >= 0)
    }
    function beginDrag(index) {
        if (searching || index < 0 || index >= vocabulary.count) return
        initialOrder = ids()
        dragIndex = index
        words.currentIndex = index
        draggedWidth = tags.itemAt(index).width
        dragAreaHeight = words.height
        draggedId = vocabulary.get(index).wordId
        draggedWord = vocabulary.get(index).word
        const rect = words.rects()[index]
        grabX = Math.max(0, Math.min(draggedWidth, pressX - rect.x))
        grabY = Math.max(0, Math.min(chipHeight, pressY + words.contentY - rect.y))
        dragging = true
        hoverIndex = -1
        pointer.forceActiveFocus()
    }
    function moveDrag() {
        if (!dragging || pointerY < 0 || pointerY > words.height) return
        let target = words.insertionIndex(pointerX, pointerY + words.contentY)
        if (target >= 0 && target !== dragIndex) {
            vocabulary.move(dragIndex, target, 1)
            dragIndex = target
            preview()
        }
    }
    function finishDrag(save) {
        if (!dragging) return
        const id = draggedId
        const next = dragIndex + 1 < vocabulary.count ? vocabulary.get(dragIndex + 1).wordId : ""
        const moved = ids().join() !== initialOrder.join()
        dragging = false
        draggedId = ""
        hoverIndex = -1
        if (save && moved) service.move(id, next)
        sync(true)
    }
    function cancelDrag() { if (dragging) suppressRelease = true; finishDrag(false) }
    function editWord(index) {
        if (index < 0 || index >= vocabulary.count) return
        const e = vocabulary.get(index)
        renameDialog.entryId = e.wordId
        renameField.text = e.word
        renameEnabled.checked = e.wordEnabled
        hoverIndex = -1
        renameDialog.open()
    }
    Component.onCompleted: sync()
    onVisibleChanged: if (!visible) cancelDrag()
    Connections { target: panel.service; function onChanged() { panel.sync() } }

    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 20
        spacing: 14
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            Label { text: "热词库"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
            Image { source: "../assets/figma/redesign-567-32842-usage-indicator.svg" }
            Label { text: "已激活"; color: theme.muted; font.pixelSize: 14 }
            Image { source: "../assets/figma/redesign-567-32842-usage-indicator1.svg" }
            Label { text: "候选"; color: theme.muted; font.pixelSize: 14 }
            Item { Layout.fillWidth: true }
            Label { text: "自动识别添加"; color: theme.text; font.pixelSize: 16 }
            FigmaSwitch { objectName: "hotwordAutoLearnToggle"; checked: panel.service.autoLearn; Accessible.name: "从纠正中自动学习热词"; onToggled: panel.service.autoLearn = checked }
            FigmaAction { objectName: "hotwordCollapseButton"; symbol: "close"; quiet: true; implicitWidth: 24; implicitHeight: 32; Accessible.name: "关闭热词库"; onClicked: { panel.cancelDrag(); panel.closeRequested() } }
        }
        Label { text: "添加热词"; color: theme.text; font.pixelSize: 16 }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            FigmaTextField {
                id: newWord
                objectName: "hotwordAddField"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                hintText: "添加人名、产品名或专业词语"
                maximumLength: 40; Accessible.name: "添加热词"
                enabled: !panel.dragging
                onAccepted: if (panel.service.add(text)) text = ""
            }
            FigmaAction {
                objectName: "hotwordAddButton"
                text: "添加热词"; primary: true; font.pixelSize: 16; implicitWidth: 108
                enabled: newWord.text.trim().length > 0 && !panel.dragging
                onClicked: if (panel.service.add(newWord.text)) newWord.text = ""
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            FigmaTextField {
                id: search
                objectName: "hotwordSearchField"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                hintText: "搜索热词"; Accessible.name: "搜索热词"
                enabled: !panel.dragging
                onTextChanged: { panel.cancelDrag(); panel.hoverIndex = -1; panel.sync() }
            }
        }
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: panel.dragging ? panel.dragAreaHeight : Math.min(176, Math.max(56, tagFlow.implicitHeight + 8))
            Flickable {
                id: words
                objectName: "hotwordList"
                property int currentIndex: 0
                readonly property int count: vocabulary.count
                anchors.fill: parent
                contentWidth: width
                contentHeight: Math.max(height, tagFlow.implicitHeight + 8)
                clip: true
                interactive: !panel.dragging
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { width: 5 }
                function rects() {
                    let result = [], x = 4, y = 4
                    for (let i = 0; i < tags.count; ++i) {
                        const tag = tags.itemAt(i)
                        if (!tag) continue
                        if (x > 4 && x + tag.width > tagFlow.width + 4) { x = 4; y += panel.chipHeight + panel.actionOverhang + panel.chipGap }
                        result.push({x: x, y: y + panel.actionOverhang, width: tag.width, height: panel.chipHeight})
                        x += tag.width + panel.chipGap
                    }
                    return result
                }
                function indexAt(x, y) {
                    for (let i = 0; i < tags.count; ++i) {
                        const r = tagRect(i)
                        if (r && x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height) return i
                    }
                    return -1
                }
                function tagRect(index) {
                    const tag = tags.itemAt(index)
                    if (!tag) return null
                    return {x: tagFlow.x + tag.x, y: tagFlow.y + tag.y + panel.actionOverhang, width: tag.width, height: panel.chipHeight}
                }
                function insertionIndex(x, y) {
                    const positions = rects()
                    let rowY = 0, distance = Infinity
                    for (const r of positions) {
                        const d = Math.abs(y - r.y - r.height / 2)
                        if (d < distance) { rowY = r.y; distance = d }
                    }
                    let before = positions.length
                    for (let i = 0; i < positions.length; ++i) {
                        const r = positions[i]
                        if (r.y !== rowY) continue
                        before = i + 1
                        if (x < r.x + r.width / 2) { before = i; break }
                    }
                    // Insertion slots include the current placeholder. Remove it
                    // from the index so drops work before/after every visible tag.
                    return Math.max(0, Math.min(positions.length - 1, before > panel.dragIndex ? before - 1 : before))
                }
                function reveal(index) {
                    const r = rects()[index]
                    if (!r) return
                    if (r.y < contentY) contentY = r.y
                    else if (r.y + r.height > contentY + height) contentY = r.y + r.height - height
                }
                Flow {
                    id: tagFlow
                    x: 4; y: 4; width: words.width - 12
                    spacing: panel.chipGap
                    move: Transition { NumberAnimation { properties: "x,y"; duration: 160; easing.type: Easing.OutCubic } }
                    Repeater {
                        id: tags
                        model: vocabulary
                        delegate: Item {
                            id: tile
                            required property int index
                            required property string wordId
                            required property string word
                            required property string source
                            required property bool wordEnabled
                            required property bool selected
                            required property int position
                            objectName: "hotwordCard_" + wordId
                            width: Math.min(tagFlow.width, Math.max(50, Math.ceil(metrics.advanceWidth) + 38))
                            height: panel.actionOverhang + panel.chipHeight
                            TextMetrics { id: metrics; text: tile.word; font: tagLabel.font }
                            Accessible.role: Accessible.Button
                            Accessible.name: word + "，" + (selected ? "下句使用" : wordEnabled ? "超出预算" : "已停用")
                            Accessible.description: "拖动调整顺序；悬停修改或删除；点选后按 Alt 加方向键调整顺序"
                            Accessible.onPressAction: panel.editWord(index)
                            Rectangle {
                                id: face
                                objectName: "hotwordTag_" + tile.wordId
                                y: panel.actionOverhang; width: parent.width; height: panel.chipHeight
                                radius: 8
                                color: panel.draggedId === tile.wordId || (pointer.pressed && panel.pressIndex === tile.index) ? theme.subtle : tile.selected ? theme.selection : theme.subtle
                                border.width: pointer.activeFocus && words.currentIndex === tile.index ? 2 : 1
                                border.color: pointer.activeFocus && words.currentIndex === tile.index ? theme.primary : panel.hoverIndex === tile.index ? theme.placeholder : theme.line
                                opacity: tile.wordEnabled ? 1 : 0.55
                                Behavior on opacity { NumberAnimation { duration: 120 } }
                                Behavior on border.color { ColorAnimation { duration: 100 } }
                                Image { x: 12; anchors.verticalCenter: parent.verticalCenter; source: tile.selected ? "../assets/figma/redesign-567-32842-usage-indicator.svg" : "../assets/figma/redesign-567-32842-usage-indicator1.svg"; visible: panel.draggedId !== tile.wordId }
                                Label {
                                    id: tagLabel
                                    objectName: "hotwordLabel_" + tile.wordId
                                    anchors.left: parent.left; anchors.leftMargin: 26
                                    anchors.right: parent.right; anchors.rightMargin: 12
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: tile.word; textFormat: Text.PlainText; elide: Text.ElideRight
                                    color: tile.wordEnabled ? theme.text : theme.muted; font.pixelSize: 16; font.weight: Font.Medium
                                    font.strikeout: !tile.wordEnabled
                                    opacity: panel.draggedId === tile.wordId ? 0 : 1
                                    Behavior on opacity { NumberAnimation { duration: 100 } }
                                }
                                ToolTip.visible: panel.hoverIndex === tile.index && pointer.containsMouse && !panel.dragging
                                ToolTip.delay: 600
                                ToolTip.text: "第 " + tile.position + " 位 · " + tile.source + " · " + (tile.selected ? "下句使用" : tile.wordEnabled ? "超出预算，移到前面可优先使用" : "已停用") + "\n" + tile.word
                            }
                        }
                    }
                }
                Label {
                    anchors.centerIn: parent; width: parent.width - 24
                    visible: vocabulary.count === 0
                    text: panel.searching ? "没有匹配的热词" : "添加常用的名字与术语，让下一次识别更准确。"
                    horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                    color: theme.muted; font.pixelSize: 14
                }
            }
            MouseArea {
                id: pointer
                objectName: "hotwordDragSurface"
                anchors.fill: words; anchors.rightMargin: 8
                acceptedButtons: Qt.LeftButton
                hoverEnabled: true
                onEntered: panel.updateHover(mouseX, mouseY)
                onExited: hoverExit.restart()
                preventStealing: true
                cursorShape: panel.dragging ? Qt.ClosedHandCursor : panel.hoverIndex >= 0 && !panel.searching ? Qt.OpenHandCursor : Qt.ArrowCursor
                onPressed: function(mouse) {
                    panel.suppressRelease = false
                    panel.pointerX = mouse.x; panel.pointerY = mouse.y
                    panel.pressX = mouse.x; panel.pressY = mouse.y
                    panel.pressIndex = words.indexAt(mouse.x, mouse.y + words.contentY)
                    if (panel.pressIndex >= 0) { words.currentIndex = panel.pressIndex; forceActiveFocus() }
                }
                onPositionChanged: function(mouse) {
                    panel.pointerX = mouse.x; panel.pointerY = mouse.y
                    panel.updateHover(mouse.x, mouse.y)
                    if (pressed && !panel.dragging && !panel.suppressRelease
                            && Math.hypot(mouse.x - panel.pressX, mouse.y - panel.pressY) >= 8)
                        panel.beginDrag(panel.pressIndex)
                    panel.moveDrag()
                }
                onReleased: function(mouse) {
                    if (panel.suppressRelease) panel.suppressRelease = false
                    else if (panel.dragging) panel.finishDrag(mouse.x >= 0 && mouse.x <= width && mouse.y >= 0 && mouse.y <= height)
                    Qt.callLater(panel.sync)
                }
                onCanceled: { panel.finishDrag(false); Qt.callLater(panel.sync) }
                Keys.onEscapePressed: function(event) { panel.cancelDrag(); event.accepted = true }
                Keys.onPressed: function(event) {
                    if (panel.dragging || !vocabulary.count) return
                    const index = Math.max(0, Math.min(vocabulary.count - 1, words.currentIndex))
                    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                        panel.editWord(index); event.accepted = true; return
                    }
                    if (panel.searching || !(event.modifiers & Qt.AltModifier)) return
                    const step = event.key === Qt.Key_Left || event.key === Qt.Key_Up ? -1
                        : event.key === Qt.Key_Right || event.key === Qt.Key_Down ? 1 : 0
                    if (!step) return
                    const target = Math.max(0, Math.min(vocabulary.count - 1, index + step))
                    const id = vocabulary.get(index).wordId
                    vocabulary.move(index, target, 1)
                    panel.service.move(id, target + 1 < vocabulary.count ? vocabulary.get(target + 1).wordId : "")
                    words.currentIndex = target
                    Qt.callLater(function() { words.reveal(target) })
                    event.accepted = true
                }
            }
            Timer {
                id: hoverExit
                interval: 180
                onTriggered: {
                    if (editButton.hovered || deleteButton.hovered || editButton.pressed || deleteButton.pressed) return
                    if (pointer.containsMouse && hoverActions.containsInteractionPoint(pointer.mouseX, pointer.mouseY)) return
                    panel.hoverIndex = -1
                }
            }
            Item {
                id: hoverActions
                objectName: "hotwordHoverActions"
                readonly property var tile: panel.hoveredTag
                x: tile ? Math.max(0, Math.min(words.width - width - 8, tile.x + tagFlow.x + tile.width - width + 1)) : 0
                y: tile ? tile.y + tagFlow.y + panel.actionOverhang - height / 2 - words.contentY : 0
                width: 36; height: 24
                visible: tile !== null && opacity > 0 && y >= 0 && y + height <= words.height
                opacity: tile && !panel.dragging && !renameDialog.visible ? 1 : 0
                enabled: tile !== null && !panel.dragging
                Behavior on opacity { NumberAnimation { duration: 100 } }
                function containsPoint(px, py) {
                    // Hit testing must work during the first frame of the fade-in.
                    return tile !== null && enabled && !renameDialog.visible
                        && px >= x && px <= x + width && py >= y && py <= y + height
                }
                function containsInteractionPoint(px, py) {
                    if (!tile || !enabled || renameDialog.visible) return false
                    const r = words.tagRect(panel.hoverIndex)
                    if (!r) return false
                    return px >= Math.min(r.x, x) - 3 && px <= Math.max(r.x + r.width, x + width) + 3
                        && py >= Math.min(r.y - words.contentY, y) - 3
                        && py <= r.y - words.contentY + r.height + 3
                }
                Row {
                    anchors.centerIn: parent; spacing: 0
                    TagAction {
                        id: editButton
                        objectName: "hotwordEditButton"
                        symbol: "edit"; text: "修改热词"
                        Accessible.name: "修改热词" + (hoverActions.tile ? hoverActions.tile.word : "")
                        onClicked: panel.editWord(panel.hoverIndex)
                    }
                    TagAction {
                        id: deleteButton
                        objectName: "hotwordDeleteButton"
                        symbol: "minus"; text: "删除热词"; destructive: true
                        Accessible.name: "删除热词" + (hoverActions.tile ? hoverActions.tile.word : "")
                        onClicked: {
                            if (!hoverActions.tile) return
                            const id = hoverActions.tile.wordId
                            panel.hoverIndex = -1
                            panel.service.remove(id)
                        }
                    }
                }
            }
            Rectangle {
                objectName: "hotwordDragPreview"
                visible: panel.dragging
                x: Math.max(0, Math.min(words.width - width, panel.pointerX - panel.grabX))
                y: Math.max(0, Math.min(words.height - height, panel.pointerY - panel.grabY))
                width: panel.draggedWidth; height: panel.chipHeight; radius: 8
                color: theme.selection; border.color: theme.primary
                scale: 1.04
                Rectangle { z: -1; x: 0; y: 3; width: parent.width; height: parent.height; radius: 8; color: "#14254C99" }
                Label { anchors.centerIn: parent; width: parent.width - 24; text: panel.draggedWord; textFormat: Text.PlainText; elide: Text.ElideRight; horizontalAlignment: Text.AlignHCenter; color: theme.primary; font.pixelSize: 14; font.weight: Font.Medium }
            }
            Timer {
                interval: 16; repeat: true; running: panel.dragging
                onTriggered: {
                    const edge = 24
                    const amount = panel.pointerY < edge ? -8 : panel.pointerY > words.height - edge ? 8 : 0
                    if (amount) {
                        const next = Math.max(0, Math.min(Math.max(0, words.contentHeight - words.height), words.contentY + amount))
                        if (next !== words.contentY) { words.contentY = next; panel.moveDrag() }
                    }
                }
            }
        }
        Label { Layout.fillWidth: true; text: panel.service.summary; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
        Label {
            Layout.fillWidth: true
            text: panel.searching ? "清空搜索后可拖动排序。" : panel.dragging ? "拖到任意位置，松手保存 · Esc 取消本次拖动" : "拖动调整优先级 · 悬停修改或删除 · Alt + 方向键排序"
            color: panel.dragging ? theme.primary : theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap
        }
        Label { Layout.fillWidth: true; text: "自动添加仅收录明确纠正的词；撤销编辑时撤回本次学习。"; color: theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
        Label { objectName: "hotwordMessage"; Layout.fillWidth: true; visible: text.length > 0; text: panel.service.message; textFormat: Text.PlainText; color: theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
    }
    component TagAction: T.Button {
        id: action
        property string symbol
        property bool destructive: false
        width: 18; height: 24
        implicitWidth: 18; implicitHeight: 24
        // Tiny controls must not inherit a style's full-size button padding.
        topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
        topPadding: 7; bottomPadding: 7; leftPadding: 4; rightPadding: 4
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        onHoveredChanged: { if (hovered) hoverExit.stop(); else hoverExit.restart() }
        onPressedChanged: { if (pressed) hoverExit.stop(); else Qt.callLater(panel.sync) }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        ToolTip.visible: hovered
        ToolTip.delay: 550
        ToolTip.text: text
        background: Item {
            Rectangle {
                objectName: action.objectName + "Face"
                width: 14; height: 14; anchors.centerIn: parent
                radius: width / 2
                color: action.destructive
                    ? (action.down ? "#FFACA5" : action.hovered ? "#FFC7C1" : "#FFE1DD")
                    : (action.down ? "#AEC4FA" : action.hovered ? "#CAD9FF" : "#E0E9FF")
                border.color: action.activeFocus ? theme.primary : action.destructive ? "#F0ADA7" : "#ACC1F3"
                Behavior on color { ColorAnimation { duration: 100 } }
            }
        }
        contentItem: UiIcon {
            objectName: action.objectName + "Icon"
            symbol: action.symbol; tint: true
            ink: action.destructive ? "#AD3540" : "#3155AB"
        }
    }
    FigmaDialog {
        id: renameDialog
        objectName: "hotwordRenameDialog"
        property string entryId: ""
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(420, parent ? parent.width - 40 : 420)
        title: "修改热词"; modal: true; padding: 24
        function save() {
            if (panel.service.edit(entryId, renameField.text, renameEnabled.checked)) close()
        }
        background: Rectangle { color: theme.surface; radius: 12; border.color: theme.line }
        onAboutToShow: panel.service.clearMessage()
        onOpened: renameField.forceActiveFocus()
        contentItem: ColumnLayout {
            spacing: 14
            FigmaTextField {
                id: renameField
                objectName: "hotwordRenameField"
                Layout.fillWidth: true; maximumLength: 40; Accessible.name: "修改后的热词"
                onAccepted: renameDialog.save()
            }
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: "参与语音识别"; color: theme.text; font.pixelSize: 14 }
                FigmaSwitch { id: renameEnabled; objectName: "hotwordEnabledToggle"; Accessible.name: "参与语音识别" }
            }
            Label { Layout.fillWidth: true; text: panel.service.message; visible: text.length > 0; wrapMode: Text.Wrap; color: theme.muted }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                FigmaAction { text: "取消"; quiet: true; onClicked: renameDialog.close() }
                FigmaAction { objectName: "hotwordRenameSaveButton"; text: "保存"; primary: true; enabled: renameField.text.trim().length > 0; onClicked: renameDialog.save() }
            }
        }
    }
}
