import QtQuick

QtObject {
    readonly property color background: "#F8F9FD"
    readonly property color sidebar: "#EFF0F7"
    readonly property color surface: "#FFFFFF"
    readonly property color subtle: "#F5F7FD"
    readonly property color line: "#E1E5F0"
    readonly property color primary: "#082ACB"
    readonly property color selection: "#DCE1FC"
    readonly property color text: "#171C28"
    readonly property color muted: "#687286"
    readonly property color placeholder: "#98A1B2"
    readonly property color success: "#16875C"
    readonly property color warning: "#8A611C"
    readonly property color warningSurface: "#FFFAEF"
    readonly property color warningBorder: "#F0E2C2"
    readonly property string family: Qt.platform.os === "osx" ? ".AppleSystemUIFont" : "Microsoft YaHei UI"
}
