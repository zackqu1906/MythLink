import QtQuick

// Figma: 463:26846, History / Record Row v2. Scoped to the history views.
QtObject {
    readonly property color surface: "#FCFCFE"
    readonly property color subtle: "#F6F7FD"
    readonly property color line: "#E3E7EF"
    readonly property color text: "#11151C"
    readonly property color muted: "#59616D"
    readonly property color primary: "#0225BA"
    readonly property color edit: "#6552A3"
    readonly property color warning: "#76520B"
    readonly property color neutral: "#A7AFBF"
    // Fit the standalone Figma page into the existing 1200px application shell.
    readonly property int titleSize: 22
    readonly property int sectionSize: 22
    readonly property int bodySize: 18
    readonly property int detailSize: 16
    readonly property int bodyLineHeight: 28
    readonly property int detailLineHeight: 24
    readonly property int fieldHeight: 40
    readonly property int rowPadding: 20
    readonly property FontLoader regularFont: FontLoader { source: "../assets/fonts/SourceHanSansSC-Regular.otf" }
    readonly property FontLoader mediumFont: FontLoader { source: "../assets/fonts/SourceHanSansSC-Medium.otf" }
    readonly property string family: regularFont.name
}
