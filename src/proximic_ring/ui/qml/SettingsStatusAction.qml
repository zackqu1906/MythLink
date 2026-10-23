import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: control
    property string statusText: ""
    property bool statusReady: false
    property string statusObjectName: ""
    property string statusHint: ""
    property bool preferenceStyle: false
    property alias buttonObjectName: action.objectName
    property alias text: action.text
    property alias buttonEnabled: action.enabled
    signal clicked()
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
    spacing: 12
    FigmaTheme { id: theme }

    Label {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        objectName: control.statusObjectName
        text: control.statusText
        color: control.statusReady ? theme.success : theme.warning
        font.pixelSize: control.preferenceStyle ? 14 : 13
        wrapMode: Text.Wrap
        horizontalAlignment: Text.AlignRight
        HoverHandler { id: statusHover }
        ToolTip.visible: statusHover.hovered && control.statusHint.length > 0
        ToolTip.text: control.statusHint
    }
    FigmaAction {
        id: action
        font.pixelSize: control.preferenceStyle ? 16 : 14
        Layout.minimumWidth: implicitWidth
        Layout.maximumWidth: implicitWidth
        onClicked: control.clicked()
    }
}
