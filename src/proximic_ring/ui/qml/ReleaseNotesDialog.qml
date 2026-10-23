import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaDialog {
    id: dialog
    required property string appVersion
    parent: Overlay.overlay; anchors.centerIn: parent
    width: Math.min(680, parent.width - 80); height: Math.min(560, parent.height - 80)
    modal: true; title: "版本说明"
    FigmaTheme { id: theme }
    contentItem: UiScrollView {
        id: scroll
        clip: true; contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: scroll.availableWidth; spacing: 20
            Label { objectName: "releaseNotesVersion"; text: "Mythlink V" + dialog.appVersion; font.pixelSize: 22; font.weight: Font.Medium; color: theme.text }
            Label { text: "本次更新"; font.pixelSize: 18; font.weight: Font.Medium; color: theme.text }
            Repeater {
                model: [
                    "界面更新：重做首页、听写与编辑、历史记录、平面触控、自动锁屏和偏好设置，统一图标与交互样式。",
                    "语音体验：新增听写、润色、修改教学，以及录音测试与回放。",
                    "历史记录：首页默认展示三条，支持滚动查看；可复制、删除和查看记录详情。",
                    "账户展示：填写用户名和邮箱即可进入，信息保存在本机。",
                    "通用设置：增加开机启动开关，将运行日志入口移入设置。"
                ]
                Label { required property string modelData; Layout.fillWidth: true; text: modelData; wrapMode: Text.Wrap; font.pixelSize: 16; lineHeightMode: Text.FixedHeight; lineHeight: 26; color: theme.muted }
            }
        }
    }
}
