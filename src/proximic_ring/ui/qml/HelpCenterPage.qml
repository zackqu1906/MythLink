import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    objectName: "helpCenterPage"
    signal backRequested()
    readonly property bool compact: scroll.availableHeight < 460
    spacing: compact ? 10 : 16
    FigmaTheme { id: theme }

    FigmaAction {
        objectName: "helpCenterBackButton"
        text: "返回帮助"; navigation: true
        onClicked: pane.backRequested()
    }
    UiScrollView {
        id: scroll
        objectName: "helpCenterScroll"
        Layout.fillWidth: true; Layout.fillHeight: true
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: scroll.availableWidth
            spacing: pane.compact ? 10 : 16
            ColumnLayout {
                Layout.fillWidth: true
                spacing: pane.compact ? 4 : 6
                Label {
                    Layout.fillWidth: true
                    text: "Mythlink · 戒指交互平台"
                    color: theme.text; font.pixelSize: 18; font.weight: Font.Medium
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    text: "融合语音输入、鼠标控制、手势识别与场景联动，在一个平台中管理戒指的交互功能。"
                    color: theme.muted; font.pixelSize: 14
                    lineHeightMode: Text.FixedHeight; lineHeight: pane.compact ? 20 : 22
                    wrapMode: Text.Wrap
                }
            }
            Repeater {
                model: [
                    {symbol: "mic", title: "语音输入", description: "连接 Ring 并开启语音识别。点入文本框后，使用当前配置的语音手势开始输入；语音输入页可查看记录。"},
                    {symbol: "pointer", title: "鼠标控制", description: "在触摸板页开启鼠标控制，通过戒指触摸板移动指针和轻触点击，并调整指针速度。"},
                    {symbol: "gesture", title: "手势控制", description: "默认双击 Tap 开始语音、单击 Tap 结束语音。仅与输入手势重叠时，输入框内优先执行输入功能；其他手势按应用优先、全局兜底执行。语音处理期间仍可取消本次操作。"}
                ]
                Control {
                    required property var modelData
                    required property int index
                    objectName: "helpCenterSection" + index
                    Layout.fillWidth: true
                    padding: pane.compact ? 12 : 16
                    background: Rectangle { radius: 12; color: theme.surface; border.color: theme.line }
                    contentItem: RowLayout {
                        spacing: 12
                        Rectangle {
                            Layout.preferredWidth: 36; Layout.preferredHeight: 36
                            Layout.alignment: Qt.AlignTop
                            radius: 8; color: theme.subtle
                            FigmaIcon { anchors.centerIn: parent; width: 20; height: 20; symbol: modelData.symbol }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: pane.compact ? 2 : 4
                            Label {
                                Layout.fillWidth: true
                                text: modelData.title
                                color: theme.text; font.pixelSize: 16; font.weight: Font.Medium
                                wrapMode: Text.Wrap
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData.description
                                color: theme.muted; font.pixelSize: 14
                                lineHeightMode: Text.FixedHeight; lineHeight: pane.compact ? 20 : 22
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }
            Label {
                objectName: "helpCenterPermissionsHint"
                Layout.fillWidth: true
                text: "输入法安装与系统授权统一在“设置 → 系统与权限”中管理。"
                color: theme.muted; font.pixelSize: 14
                lineHeightMode: Text.FixedHeight; lineHeight: pane.compact ? 20 : 22
                wrapMode: Text.Wrap
            }
        }
    }
}
