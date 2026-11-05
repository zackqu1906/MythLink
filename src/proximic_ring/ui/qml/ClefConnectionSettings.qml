import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaFormGroup {
    id: pane
    required property var connection
    flat: true
    title: "自动路由服务器"
    description: "通过 SSH 连接已部署 Clef 的服务器。连接信息保存在本机，私钥不上传。"
    FigmaTheme { id: theme }
    Component.onCompleted: {
        const saved = connection.config
        host.text = saved.host; port.text = String(saved.port)
        user.text = saved.user; key.text = saved.key_file
        servicePort.text = String(saved.service_port)
    }
    FigmaFormRow {
        preferenceStyle: true; title: "服务器"
        controlWidth: pane.width >= 760 ? 340 : 220
        RowLayout {
            Layout.fillWidth: true
            FigmaTextField {
                id: host; objectName: "clefSshHost"; Layout.fillWidth: true
                hintText: "域名或 IP"
                Accessible.name: "SSH 服务器地址"
                enabled: !pane.connection.busy
            }
            FigmaTextField {
                id: port; objectName: "clefSshPort"; Layout.preferredWidth: 80
                hintText: "22"
                validator: IntValidator { bottom: 1; top: 65535 }
                Accessible.name: "SSH 端口"; enabled: !pane.connection.busy
            }
        }
    }
    FigmaFormRow {
        preferenceStyle: true; title: "账号"
        controlWidth: pane.width >= 760 ? 340 : 220
        FigmaTextField {
            id: user; objectName: "clefSshUser"; Layout.fillWidth: true
            hintText: "SSH 用户名"
            Accessible.name: "SSH 用户名"; enabled: !pane.connection.busy
        }
    }
    FigmaFormRow {
        preferenceStyle: true; title: "私钥"
        description: "选择登录用的私钥（不是 .pub）。留空则使用默认私钥或 SSH agent。"
        controlWidth: pane.width >= 760 ? 340 : 220
        RowLayout {
            Layout.fillWidth: true
            FigmaTextField {
                id: key; objectName: "clefSshKey"; Layout.fillWidth: true
                hintText: "~/.ssh/id_ed25519"
                Accessible.name: "SSH 私钥路径"; enabled: !pane.connection.busy
            }
            FigmaAction {
                text: "选择"; enabled: !pane.connection.busy
                onClicked: {
                    const selected = pane.connection.choosePrivateKey()
                    if (selected.length) key.text = selected
                }
            }
        }
    }
    FigmaFormRow {
        preferenceStyle: true; title: "服务端口"
        description: "服务器内部的 Clef 端口，通常为 30000，无需公网 HTTP。"
        controlWidth: pane.width >= 760 ? 340 : 220
        FigmaTextField {
            id: servicePort; objectName: "clefServicePort"; Layout.fillWidth: true
            validator: IntValidator { bottom: 1; top: 65535 }
            Accessible.name: "Clef 服务端口"; enabled: !pane.connection.busy
        }
    }
    FigmaFormRow {
        preferenceStyle: true; title: pane.connection.status
        description: "首次连接会记住服务器身份；之后自动连接。语音识别与文本模型沿用各自的服务设置。"
        FigmaAction {
            objectName: "clefConnectButton"; text: pane.connection.busy ? "连接中…" : "保存并测试"
            enabled: !pane.connection.busy; primary: true
            onClicked: pane.connection.connectServer({host: host.text, port: port.text,
                user: user.text, key_file: key.text, service_port: servicePort.text})
        }
    }
}
