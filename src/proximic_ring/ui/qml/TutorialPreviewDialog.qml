import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

FigmaDialog {
    id: dialog
    required property var controller
    readonly property var practice: controller.voicePractice
    readonly property var tutorial: controller.voiceTutorial
    readonly property bool currentStepDone: demonstration ? stepDone : tutorial.stepComplete
    property int step: 0
    property bool demonstration: false
    property bool stepDone: false
    property string demoText: ""
    signal homeRequested()
    signal settingsRequested()
    signal voiceRequested()
    signal gesturesRequested()
    objectName: "tutorialPreviewDialog"
    parent: Overlay.overlay; anchors.centerIn: parent; popupType: Popup.Item
    width: Math.min(980, parent.width - 48); height: Math.min(780, parent.height - 48)
    modal: true
    title: ["欢迎使用 MythLink", "开始之前", "第 1 步 · 说出一句话", "第 2 步 · 润色当前句", "第 3 步 · 语音修改", demonstration ? "你已看完操作示意" : "你已完成语音练习"][step]
    onAboutToShow: { practice.cancel(); tutorial.stop(); if (step >= 2) step = 0 }
    onClosed: { practice.cancel(); tutorial.stop() }
    FigmaTheme { id: theme }
    function begin(demo) {
        practice.cancel()
        if (!demo && !tutorial.start()) return
        demonstration = demo
        stepDone = false
        demoText = ""
        realInput.text = ""
        step = 2
        if (!demo) Qt.callLater(function() { realInput.forceActiveFocus() })
    }
    function advance() {
        if (!demonstration) tutorial.advance()
        stepDone = false
        step++
        if (step === 5) tutorial.stop()
    }
    function exercise() {
        if (!demonstration) return
        demoText = step === 2 ? "今天下午三点我们一起聊一下新的那个设计方案。" : step === 3 ? "今天下午三点，我们一起讨论新的设计方案。" : "今天下午四点，我们一起讨论新的设计方案。"
        stepDone = true
    }
    contentItem: UiScrollView {
        id: viewport
        clip: true; contentWidth: availableWidth
        ColumnLayout {
            width: viewport.availableWidth; spacing: 20
            Label {
                Layout.fillWidth: true; color: theme.muted; font.pixelSize: 16; wrapMode: Text.Wrap
                text: ["先用一句话，学会语音输入", "连接 Ring，并完成语音输入准备", "把想表达的话变成文字", "让刚才的表达更简洁、通顺", "用一句修改指令编辑原文", dialog.demonstration ? "操作示意不计为真实设备测试" : "现在可以回到日常应用中使用"][dialog.step]
            }
            Rectangle {
                visible: dialog.step === 0
                Layout.fillWidth: true; implicitHeight: invitation.implicitHeight + 64
                radius: 12; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    id: invitation
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 32 }
                    spacing: 20
                    Label { text: "先用一句话，学会语音输入"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                    Label { Layout.fillWidth: true; text: "把光标放在练习框中，跟随提示体验正常语音输入、润色和编辑。"; color: theme.muted; font.pixelSize: 16; wrapMode: Text.Wrap }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 24
                        Repeater {
                            model: [{title:"准备好 Ring",detail:"连接设备与权限"}, {title:"完成一句输入",detail:"开启与定稿"}, {title:"润色和纠字",detail:"练习语音修改"}]
                            Rectangle {
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true; Layout.preferredWidth: 273; implicitHeight: 124
                                color: theme.surface; radius: 12; border.color: theme.line
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 16; spacing: 8
                                    Label { text: "0" + (index + 1); font.pixelSize: 14; color: theme.primary }
                                    Label { text: modelData.title; font.pixelSize: 18; font.weight: Font.Medium; color: theme.text }
                                    Label { Layout.fillWidth: true; text: modelData.detail; font.pixelSize: 16; color: theme.muted; wrapMode: Text.Wrap }
                                }
                            }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: 12
                        FigmaAction { objectName: "tutorialPrepareButton"; text: "连接并开始学习"; primary: true; font.pixelSize: 16; onClicked: dialog.step = 1 }
                        FigmaAction { objectName: "tutorialDemoButton"; text: "先看操作示意"; font.pixelSize: 16; onClicked: dialog.begin(true) }
                        FigmaAction { text: "稍后学习"; navigation: true; onClicked: dialog.close() }
                    }
                }
            }
            RowLayout {
                visible: dialog.step === 1
                Layout.fillWidth: true; spacing: 24
                ColumnLayout {
                    Layout.fillWidth: true; Layout.preferredWidth: 580; spacing: 16
                    Label { text: "完成使用准备"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                    Repeater {
                        model: [
                            {title:"Ring 与语音输入",ready:dialog.controller.connected && dialog.controller.recognitionEnabled,
                             detail:!dialog.controller.connected ? "连接 Ring 后，即可使用手势开始语音输入。" : "Ring 已连接，请开启语音输入。",
                             action:dialog.controller.connected ? "开启语音输入" : "连接 Ring",destination:"home"},
                            {title:"系统权限",ready:dialog.controller.inlineInput.permissions.allPermissionsGranted,
                             detail:"当前状态：" + dialog.controller.inlineInput.permissions.authorizationStatusText,
                             action:"前往设置",destination:"settings"},
                            {title:"Ring 输入法",ready:!dialog.controller.inlineInput.installing && dialog.controller.inlineInput.inputMethodVersion.current,
                             detail:dialog.controller.inlineInput.installing ? "正在更新输入法…" : dialog.controller.inlineInput.inputMethodVersion.text,
                             action:"前往设置",destination:"settings"}
                        ]
                        Rectangle {
                            required property var modelData
                            required property int index
                            objectName: "tutorialPreparationRow" + index
                            Layout.fillWidth: true; implicitHeight: prep.implicitHeight + 32; color: theme.surface; radius: 12; border.color: theme.line
                            ColumnLayout {
                                id: prep
                                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
                                spacing: 8
                                Label { text: modelData.title; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 8
                                    Rectangle { visible: modelData.ready; width: 7; height: 7; radius: 4; color: theme.success }
                                    Label {
                                        objectName: "tutorialPreparationStatus" + index
                                        Layout.fillWidth: true
                                        text: modelData.ready ? "已就绪，可以使用" : modelData.detail
                                        color: modelData.ready ? theme.success : theme.muted
                                        font.pixelSize: 14; wrapMode: Text.Wrap
                                    }
                                }
                                FigmaAction {
                                    objectName: "tutorialPreparationAction" + index
                                    visible: !modelData.ready; text: modelData.action; navigation: true
                                    onClicked: modelData.destination === "home" ? dialog.homeRequested() : dialog.settingsRequested()
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    Layout.preferredWidth: 280; Layout.alignment: Qt.AlignTop; implicitHeight: inputCheck.implicitHeight + 32
                    color: theme.surface; radius: 12; border.color: theme.line
                    ColumnLayout {
                        id: inputCheck
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
                                spacing: 16
                        Label { text: "先检查收音是否有效"; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                        FigmaSelect {
                            Layout.fillWidth: true
                            model: dialog.controller.audioInputs; textRole: "label"; valueRole: "value"
                            currentIndex: dialog.controller.audioInputs.map(function(item) { return item.value }).indexOf(dialog.controller.audioInputSelection)
                            onActivated: dialog.controller.selectAudioInput(currentValue)
                            onDownChanged: if (down) dialog.controller.refreshMicrophones()
                        }
                        VoiceAudioTest { Layout.fillWidth: true; practice: dialog.practice }
                        FigmaAction { objectName: "tutorialBeginButton"; Layout.fillWidth: true; text: "开始真实练习"; primary: true; enabled: dialog.controller.connected && dialog.controller.recognitionEnabled; onClicked: dialog.begin(false) }
                        Label { Layout.fillWidth: true; visible: text.length > 0; text: dialog.tutorial.message; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
                        FigmaAction { text: "先看操作示意"; navigation: true; onClicked: dialog.begin(true) }
                        Label { Layout.fillWidth: true; text: "示意学习不计为真实设备测试。"; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
                    }
                }
            }
            RowLayout {
                visible: dialog.step >= 2 && dialog.step <= 4
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: ["1 · 听写", "2 · 润色", "3 · 修改", "4 · 完成"]
                    Rectangle {
                        required property string modelData
                        required property int index
                        Layout.fillWidth: true; implicitHeight: 32; radius: 4
                        color: index === dialog.step - 2 ? theme.selection : theme.subtle
                        Behavior on color { ColorAnimation { duration: 120 } }
                        Label { anchors.centerIn: parent; text: modelData; color: theme.text; font.pixelSize: 14 }
                    }
                }
            }
            RowLayout {
                visible: dialog.step >= 2 && dialog.step <= 4
                Layout.fillWidth: true; spacing: 24
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredWidth: 580; implicitHeight: 358
                    color: theme.surface; radius: 12; border.color: theme.line
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 20; spacing: 12
                        Label { text: dialog.step === 2 ? "说出一句话" : dialog.step === 3 ? "润色当前句" : "用语音纠正具体词语"; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium }
                        Label { Layout.fillWidth: true; text: dialog.step === 2 ? "例如：今天下午三点我们一起聊一下新的那个设计方案。" : dialog.step === 3 ? "试着让刚才的表达更清楚、更自然。" : "例如：把下午三点改成下午四点。"; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
                        Rectangle {
                            visible: !dialog.demonstration
                            Layout.fillWidth: true; Layout.fillHeight: true
                            color: theme.background; radius: 8; border.color: realInput.activeFocus ? theme.primary : theme.line
                            ScrollView {
                                anchors.fill: parent; anchors.margins: 12; clip: true; contentWidth: availableWidth
                                Basic.TextArea {
                                    id: realInput
                                    objectName: "tutorialPracticeEditor"
                                    readonly property bool ownsFocus: activeFocus && dialog.visible && !dialog.demonstration && dialog.step >= 2 && dialog.step <= 4 && (Window.window ? Window.window.active : false)
                                    onOwnsFocusChanged: dialog.tutorial.setEditorFocused(ownsFocus)
                                    placeholderText: dialog.tutorial.hint.id.indexOf("speak-") === 0
                                                     ? (dialog.step === 4 ? "正在听，请说出修改指令……" : "正在听，请说出一句话……")
                                                     : "把光标放在这里，跟随右侧提示操作……"
                                    font.family: theme.family; font.pixelSize: 18
                                    color: theme.text; placeholderTextColor: theme.muted
                                    wrapMode: TextEdit.Wrap; selectByMouse: true; padding: 0
                                    topInset: 0; bottomInset: 0; background: null
                                }
                            }
                        }
                        Rectangle {
                            visible: dialog.demonstration; Layout.fillWidth: true; Layout.fillHeight: true
                            color: theme.background; radius: 8; border.color: theme.line
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 16
                                Label { text: "练习输入框 · 操作示意"; color: theme.muted; font.pixelSize: 14 }
                                Label { Layout.fillWidth: true; Layout.fillHeight: true; text: dialog.demoText || "点击下方按钮，查看这一步的操作结果。"; color: theme.text; font.pixelSize: 18; wrapMode: Text.Wrap }
                            }
                        }
                        Label { Layout.fillWidth: true; text: dialog.demonstration ? "这是操作示意，没有采集音频或调用语音服务。" : "把光标留在练习框，跟随浮窗完成操作。练习内容会保存到语音历史。"; color: theme.muted; font.pixelSize: 14; wrapMode: Text.Wrap }
                    }
                }
                TutorialHintCard {
                    objectName: "tutorialHintCard"
                    Layout.preferredWidth: 280; Layout.alignment: Qt.AlignTop
                    hint: dialog.demonstration ? {id:"demo",title:"操作示意",text:"真实教学会随输入状态提示当前操作。",gestures:[],functionAsset:"",tone:"normal"} : dialog.tutorial.hint
                }
            }
            Flow {
                visible: dialog.step >= 2 && dialog.step <= 4
                Layout.fillWidth: true; spacing: 12
                FigmaAction {
                    objectName: "tutorialExerciseButton"
                    visible: dialog.demonstration
                    primary: true; font.pixelSize: 16
                    text: ["模拟定稿", "模拟下滑润色", "模拟完成纠正"][dialog.step - 2] || ""
                    onClicked: dialog.exercise()
                }
                FigmaAction { objectName: "tutorialNextButton"; text: dialog.step === 4 ? "完成教学" : "下一步"; focusPolicy: Qt.NoFocus; enabled: dialog.currentStepDone && (dialog.demonstration || !dialog.tutorial.busy); onClicked: dialog.advance() }
                FigmaAction { text: "返回学习概览"; onClicked: { dialog.practice.cancel(); dialog.tutorial.stop(); dialog.step = 0 } }
                FigmaAction { text: "跳过教学"; navigation: true; onClicked: dialog.close() }
            }
            Rectangle {
                visible: dialog.step === 5
                Layout.fillWidth: true; implicitHeight: completion.implicitHeight + 64
                radius: 12; color: theme.surface; border.color: theme.line
                ColumnLayout {
                    id: completion
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 32 }
                    spacing: 20
                    Label { Layout.fillWidth: true; text: dialog.demonstration ? "输入、润色、修改，操作示意已看完" : "输入、润色、修改，你已经试过了"; font.pixelSize: 22; font.weight: Font.Medium; color: theme.text; wrapMode: Text.Wrap }
                    Label { Layout.fillWidth: true; text: dialog.demonstration ? "连接设备后，还可以进行一次真实的语音练习。" : "回到常用应用，把焦点放进文本框，再使用 Ring 开始输入。"; font.pixelSize: 16; color: theme.muted; wrapMode: Text.Wrap }
                    Label { text: (dialog.controller.speechControlMode === "proximity" ? "靠近 Ring 开启" : dialog.controller.confirmGestureHint + "开启") + "；" + dialog.controller.endGestureHint + "确认定稿"; font.pixelSize: 16; color: theme.text }
                    Label { Layout.fillWidth: true; text: "下滑润色当前句；说出修改指令，在未定稿时" + dialog.controller.gestureButtonHints.switch_mode + "转换为编辑。"; font.pixelSize: 16; color: theme.text; wrapMode: Text.Wrap }
                    Flow {
                        Layout.fillWidth: true; spacing: 12
                        FigmaAction { text: "回到首页"; primary: true; onClicked: dialog.homeRequested() }
                        FigmaAction { text: "日常输入测试"; onClicked: dialog.voiceRequested() }
                        FigmaAction { text: "查看手势控制"; navigation: true; onClicked: dialog.gesturesRequested() }
                    }
                }
            }
        }
    }
}
