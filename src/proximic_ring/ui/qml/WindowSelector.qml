import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property bool presented: false
    property bool nativeMaterial: false
    property int iconRevision: 0
    readonly property bool expandsSelection: windowSelector.atApps
        && windowSelector.selected >= 0 && windowSelector.selected < windowSelector.cards.length
        && windowSelector.cards[windowSelector.selected].windowCount > 1
    property int columns: Math.max(1, Math.min(4, Math.floor((content.width + 24) / 290)))
    onColumnsChanged: windowSelector.setColumns(columns)
    onPresentedChanged: {
        windowSelector.setColumns(columns)
        if (presented) {
            entrance.restart()
            Qt.callLater(grid.revealSelection)
        }
    }
    Rectangle {
        anchors.fill: parent; color: root.nativeMaterial ? "#18000000" : "#ee202020"
        MouseArea { anchors.fill: parent; onClicked: windowSelector.cancel() }
    }
    Item {
        id: content
        width: Math.min(1500, parent.width - 96)
        height: parent.height - 108
        anchors.centerIn: parent
        opacity: 0; scale: 0.985
        ParallelAnimation {
            id: entrance
            NumberAnimation { target: content; property: "opacity"; from: 0.45; to: 1; duration: 120 }
            NumberAnimation { target: content; property: "scale"; from: 0.985; to: 1; duration: 160; easing.type: Easing.OutCubic }
        }
        Column {
            spacing: 8
            Text { text: "RING  /  WINDOW SELECTOR"; color: "#a9a9a9"; font.pixelSize: 11; font.letterSpacing: 2.5 }
            Row {
                spacing: 16
                Button {
                    id: backButton
                    objectName: "windowSelectorBack"
                    visible: !windowSelector.atApps
                    width: 112; height: 36
                    anchors.verticalCenter: parent.verticalCenter
                    text: "‹ 返回应用"; focusPolicy: Qt.NoFocus; hoverEnabled: true
                    contentItem: Text {
                        text: backButton.text; color: "#eeeeee"; font.pixelSize: 13
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 18; color: backButton.hovered ? "#555555" : "#383838"
                        border.color: "#50ffffff"
                    }
                    onClicked: windowSelector.back()
                }
                Text {
                    objectName: "windowSelectorHeading"
                    text: windowSelector.heading; textFormat: Text.PlainText
                    width: Math.min(implicitWidth, Math.max(140, content.width - 320))
                    elide: Text.ElideRight
                    color: "#f5f5f5"; font.pixelSize: 30; font.weight: Font.DemiBold
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: windowSelector.phase === "loading" ? "正在读取窗口…" : windowSelector.cards.length + (windowSelector.atApps ? " 个应用" : " 个窗口")
                    color: "#a0a0a0"; font.pixelSize: 14
                }
            }
            Row {
                spacing: 14
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: windowPreviews.status !== "permission"
                    text: windowSelector.phase === "loading" ? "当前屏幕与桌面" : windowPreviews.hint
                    color: "#b0b0b0"; font.pixelSize: 13
                }
                Text { anchors.verticalCenter: parent.verticalCenter; text: "部分应用未提供窗口"; color: "#bcbcbc"; font.pixelSize: 12; visible: windowSelector.partial }
            }
        }
        Rectangle {
            width: 36; height: 36; radius: 18
            anchors.right: parent.right; anchors.top: parent.top
            color: closeArea.containsMouse ? "#30ffffff" : "#16ffffff"
            Text { anchors.centerIn: parent; text: "×"; font.pixelSize: 24; color: "#dcdcdc" }
            MouseArea { id: closeArea; anchors.fill: parent; hoverEnabled: true; onClicked: windowSelector.cancel() }
        }
        BusyIndicator {
            anchors.centerIn: parent; running: root.presented && windowSelector.phase === "loading"
            visible: running; width: 44; height: 44
            palette.highlight: "#eeeeee"; palette.windowText: "#eeeeee"
        }
        GridView {
            id: grid
            objectName: "windowSelectorGrid"
            anchors { left: parent.left; right: parent.right; top: parent.top; bottom: permissionNotice.visible ? permissionNotice.top : footer.top; topMargin: 122; bottomMargin: 20 }
            clip: true
            cellWidth: width / root.columns
            cellHeight: Math.round((cellWidth - 48) * 0.625) + 86
            model: windowSelector.cards
            boundsBehavior: Flickable.StopAtBounds
            function updateVisible() {
                if (root.presented && windowSelector.phase === "ready")
                    windowPreviews.setVisibleRange(Math.max(0, Math.floor(contentY / cellHeight) * root.columns),
                                                  Math.ceil((contentY + height) / cellHeight) * root.columns)
            }
            function revealSelection() {
                positionViewAtIndex(windowSelector.selected, GridView.Contain)
                updateVisible()
            }
            onContentYChanged: updateVisible()
            onHeightChanged: updateVisible()
            onCountChanged: Qt.callLater(revealSelection)
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }
            delegate: Item {
                id: delegateRoot
                required property var modelData
                required property int index
                width: grid.cellWidth; height: grid.cellHeight
                readonly property bool chosen: index === windowSelector.selected
                Rectangle {
                    id: card
                    objectName: "windowPreviewCard"
                    x: 8; y: 8; width: parent.width - 24; height: parent.height - 24
                    radius: 15
                    color: chosen ? "#e83a3a3a" : (cardMouse.containsMouse ? "#dd343434" : "#cc292929")
                    border.color: chosen ? "#eeeeee" : "#40ffffff"; border.width: chosen ? 2 : 1
                    scale: chosen ? 1.018 : 1
                    Behavior on scale { NumberAnimation { duration: 130; easing.type: Easing.OutCubic } }
                    Behavior on color { ColorAnimation { duration: 130 } }
                    Rectangle { anchors.fill: parent; anchors.margins: -3; radius: 18; color: "transparent"; border.color: chosen ? "#28ffffff" : "transparent"; border.width: 1 }
                    Rectangle {
                        id: viewport
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
                        height: card.height - 72
                        color: "#141414"; radius: 8; clip: true
                        Image {
                            id: thumbnail
                            objectName: "windowThumbnail"
                            property string previewKey: modelData.id
                            onPreviewKeyChanged: source = ""
                            anchors.fill: parent; anchors.margins: 2
                            cache: false; fillMode: Image.PreserveAspectFit; smooth: true
                        }
                        Column {
                            anchors.centerIn: parent; spacing: 8
                            visible: thumbnail.source.toString().length === 0
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.app; textFormat: Text.PlainText; color: "#bcbcbc"; font.pixelSize: 17 }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: windowPreviews.status === "permission" ? "预览未开启" :
                                      (!(modelData.number || 0) || windowPreviews.status === "unavailable") ? "该窗口暂不能预览" : "正在连接预览…"
                                color: "#888888"; font.pixelSize: 12
                            }
                        }
                        Connections {
                            target: windowPreviews
                            function onFrameReady(identifier, url) { if (identifier === modelData.id) thumbnail.source = url }
                            function onCleared() { thumbnail.source = "" }
                        }
                    }
                    Row {
                        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 15; rightMargin: 42; bottomMargin: 12 }
                        spacing: 10; height: 38
                        Rectangle {
                            width: 30; height: 30; radius: 8; anchors.verticalCenter: parent.verticalCenter; color: "#464646"
                            Text { anchors.centerIn: parent; text: modelData.app.slice(0, 1); color: "#dddddd"; font.pixelSize: 17 }
                            Image { anchors.fill: parent; source: root.presented && root.iconRevision > 0 ? "image://windowApps/" + modelData.pid + "/" + root.iconRevision : ""; cache: false; fillMode: Image.PreserveAspectFit }
                        }
                        Column {
                            width: parent.width - 40; spacing: 3
                            Text { width: parent.width; text: modelData.app; textFormat: Text.PlainText; color: "#eeeeee"; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight }
                            Text { width: parent.width; text: modelData.title; textFormat: Text.PlainText; color: "#b0b0b0"; font.pixelSize: 12; elide: Text.ElideRight }
                        }
                    }
                    Text { anchors { right: parent.right; bottom: parent.bottom; margins: 18 }
                        text: chosen ? (windowSelector.atApps && modelData.windowCount > 1 ? "›" : "↗") : ""; color: "#eeeeee"; font.pixelSize: 21 }
                    MouseArea { id: cardMouse; anchors.fill: parent; hoverEnabled: true; onClicked: windowSelector.pick(index) }
                }
            }
            Connections {
                target: windowSelector
                function onSelectionChanged() { Qt.callLater(grid.revealSelection) }
                function onPageChanged() {
                    grid.contentY = 0
                    entrance.restart()
                    Qt.callLater(grid.revealSelection)
                }
            }
        }
        RowLayout {
            id: permissionNotice
            objectName: "windowSelectorPermissionNotice"
            anchors { left: parent.left; right: parent.right; bottom: footer.top; bottomMargin: 12; leftMargin: 12; rightMargin: 12 }
            visible: windowPreviews.status === "permission" && windowSelector.phase === "ready"
            spacing: 12
            UiNotice {
                objectName: "windowSelectorPermissionText"
                noticeBackground: "#403521"
                noticeBorder: "#665330"
                Layout.fillWidth: true
                text: "未开启屏幕录制权限，暂时无法显示窗口预览。仍可选择和切换窗口。"
                color: "#F2C66D"; font.pixelSize: 13; wrapMode: Text.Wrap
            }
            Button {
                id: permissionButton
                objectName: "windowSelectorPermissionButton"
                text: "去开启"; focusPolicy: Qt.NoFocus
                implicitWidth: 80; implicitHeight: 36
                padding: 8; hoverEnabled: true
                background: Rectangle {
                    radius: 10
                    color: permissionButton.down ? "#55472D" : permissionButton.hovered ? "#493D28" : "#403521"
                    border.color: "#665330"
                }
                contentItem: Text {
                    text: permissionButton.text
                    color: "#F2C66D"; font.pixelSize: 13
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: windowPreviews.requestPermission()
            }
        }
        Rectangle {
            id: footer
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: Math.max(54, gestureHints.implicitHeight + 28)
            radius: 16; color: "#661c1c1c"; border.color: "#28ffffff"
            Flow {
                id: gestureHints
                width: parent.width - 170
                anchors { left: parent.left; leftMargin: 22; verticalCenter: parent.verticalCenter }
                spacing: 18
                Text { text: windowSelector.atApps ? "↑ ↓ ← →  选择应用" : "↑ ↓ ← →  选择窗口"; color: "#cccccc"; font.pixelSize: 13 }
                Text { visible: !windowSelector.atApps && Boolean(windowSelector.globalLabels.switch_mode); text: windowSelector.globalLabels.switch_mode + "  返回上级"; color: "#cccccc"; font.pixelSize: 13 }
                Text { text: root.expandsSelection ? "Tap  展开" : "Tap  进入窗口"; color: "#f5f5f5"; font.pixelSize: 13 }
                Text { visible: Boolean(windowSelector.globalLabels.window_selector); text: windowSelector.globalLabels.window_selector + "  取消窗口选择"; color: "#cccccc"; font.pixelSize: 13 }
                Text { visible: Boolean(windowSelector.globalLabels.show_menu); text: windowSelector.globalLabels.show_menu + "  提示环"; color: "#cccccc"; font.pixelSize: 13 }
            }
            Text {
                anchors { right: parent.right; rightMargin: 22; verticalCenter: parent.verticalCenter }
                text: windowSelector.phase === "loading" ? (windowSelector.globalLabels.window_selector ? windowSelector.globalLabels.window_selector + "可取消" : "Esc 可取消") : windowSelector.remaining + "s 后退出"
                color: windowSelector.remaining <= 3 ? "#eeeeee" : "#a0a0a0"; font.pixelSize: 13
            }
        }
    }
}
