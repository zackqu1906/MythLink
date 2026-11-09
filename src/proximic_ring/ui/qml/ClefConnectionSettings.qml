import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaFormGroup {
    id: pane
    required property var connection
    flat: true
    title: "自动路由服务器"
    description: "通过公网地址直接连接已部署的 Clef 服务，无需账号登录。连接信息保存在本机。"
    Component.onCompleted: {
        const saved = connection.config
        host.text = saved.host; port.text = String(saved.port)
    }
    FigmaFormRow {
        preferenceStyle: true; title: "服务器地址"
        description: "填写域名或 IP；使用 HTTPS 时，在地址前加 https://。"
        controlWidth: pane.width >= 760 ? 340 : 220
        FigmaTextField {
            id: host; objectName: "clefServerHost"; Layout.fillWidth: true
            hintText: "域名或 IP"
            Accessible.name: "Clef 服务器地址"
            enabled: !pane.connection.busy
        }
    }
    FigmaFormRow {
        preferenceStyle: true; title: "公网端口"
        description: "默认 20031；若分配了其他公网端口，请填写实际端口。"
        controlWidth: pane.width >= 760 ? 340 : 220
        FigmaTextField {
            id: port; objectName: "clefPublicPort"; Layout.fillWidth: true
            hintText: "20031"
            validator: IntValidator { bottom: 1; top: 65535 }
            Accessible.name: "Clef 公网端口"; enabled: !pane.connection.busy
        }
    }
    FigmaFormRow {
        preferenceStyle: true; title: pane.connection.status
        description: "保存后，自动路由使用此地址。语音识别与正文模型沿用各自的服务设置。"
        FigmaAction {
            objectName: "clefConnectButton"; text: pane.connection.busy ? "连接中…" : "保存并测试"
            enabled: !pane.connection.busy; primary: true
            onClicked: pane.connection.connectServer({host: host.text, port: port.text})
        }
    }
}
