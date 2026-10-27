import QtQuick

// 1022 MythLink: desktop foundations. Shared by the workbench and gesture workspace.
QtObject {
    readonly property color background: "#FFFFFF"
    readonly property color sidebar: "#EEF1F7"
    readonly property color surface: "#FCFCFE"
    readonly property color subtle: "#F6F7FD"
    readonly property color line: "#E3E7EF"
    readonly property color primary: "#0225BA"
    readonly property color primaryHover: "#314CC8"
    readonly property color primaryPressed: "#00214E"
    readonly property color selection: "#D9DFF9"
    readonly property color text: "#11151C"
    readonly property color muted: "#59616D"
    readonly property color placeholder: "#A7AFBF"
    readonly property color success: "#16703F"
    readonly property color warning: "#76520B"
    readonly property color warningSurface: "#FFFAEF"
    readonly property color warningBorder: "#F0E2C2"
    // The bundled CFF faces can resolve to incorrect CJK glyphs in the native
    // Windows font engine. Use the OS UI face there; keep bundled fonts on Mac.
    readonly property bool useSystemFont: Qt.platform.os === "windows"
    readonly property FontLoader regularFont: FontLoader { source: Qt.platform.os === "windows" ? "" : "../assets/fonts/SourceHanSansSC-Regular.otf" }
    readonly property FontLoader mediumFont: FontLoader { source: Qt.platform.os === "windows" ? "" : "../assets/fonts/SourceHanSansSC-Medium.otf" }
    readonly property string family: useSystemFont ? "Microsoft YaHei UI" : regularFont.name
}
