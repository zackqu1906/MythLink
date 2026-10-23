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
    readonly property FontLoader regularFont: FontLoader { source: "../assets/fonts/SourceHanSansSC-Regular.otf" }
    readonly property FontLoader mediumFont: FontLoader { source: "../assets/fonts/SourceHanSansSC-Medium.otf" }
    readonly property string family: regularFont.name
}
