import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: dialog
    required property var profile
    property bool editing: false
    objectName: "accountPreferencesPage"
    signal closeRequested()
    readonly property string title: editing ? (profile.signedIn ? "编辑账户" : "登录") : "账户"
    function editProfile() {
        nameField.text = profile.displayName
        emailField.text = profile.email
        editing = true
        nameField.forceActiveFocus()
    }
    function saveProfile() {
        profile.save(nameField.text, emailField.text)
        closeRequested()
    }
    function resetDraft() {
        editing = !profile.signedIn
        nameField.text = profile.displayName
        emailField.text = profile.email
    }
    function focusEditor() { if (editing) nameField.forceActiveFocus() }
    FigmaTheme { id: theme }
    ColumnLayout {
        anchors.fill: parent
        spacing: 16
        ColumnLayout {
            visible: dialog.editing
            Layout.fillWidth: true; Layout.leftMargin: 16; Layout.rightMargin: 28; spacing: 24
            Label { text: "填写用户名和邮箱即可进入"; color: theme.muted; font.pixelSize: 16 }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 8
                Label { text: "用户名"; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                FigmaTextField { id: nameField; objectName: "accountNameField"; Layout.fillWidth: true; hintText: "填写用户名"; onAccepted: emailField.forceActiveFocus() }
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 8
                Label { text: "电子邮件"; color: theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                FigmaTextField { id: emailField; objectName: "accountEmailField"; Layout.fillWidth: true; hintText: "填写邮箱"; onAccepted: dialog.saveProfile() }
            }
        }
        ColumnLayout {
            visible: !dialog.editing
            Layout.fillWidth: true; Layout.rightMargin: 12; spacing: 8
            PreferenceRow {
                Layout.fillWidth: true; title: "电子邮件"; description: dialog.profile.email || "未填写"; metadata: true
                FigmaAction { objectName: "accountEditButton"; Layout.fillWidth: true; text: "更换绑定"; font.pixelSize: 16; onClicked: dialog.editProfile() }
            }
            PreferenceRow {
                Layout.fillWidth: true; title: "手机号"; description: "未绑定"; metadata: true
                FigmaAction { Layout.fillWidth: true; text: "去设置"; font.pixelSize: 16; onClicked: pendingPhone.open() }
            }
        }
        Item { Layout.fillHeight: true }
        FigmaAction {
            objectName: "accountSaveButton"; visible: dialog.editing; Layout.fillWidth: true
            text: dialog.profile.signedIn ? "保存" : "登录"; primary: true; font.pixelSize: 18
            onClicked: dialog.saveProfile()
        }
        FigmaAction {
            objectName: "accountSignOutButton"; visible: !dialog.editing; Layout.fillWidth: true
            text: "登出账户"; font.pixelSize: 18
            onClicked: { dialog.profile.signOut(); dialog.closeRequested() }
        }
    }
    FigmaDialog {
        id: pendingPhone
        parent: Overlay.overlay; anchors.centerIn: parent; width: 360; modal: true; title: "手机号"
        contentItem: Label { text: "手机号绑定暂未开放。"; color: theme.muted; wrapMode: Text.Wrap }
    }
}
