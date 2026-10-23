import QtQuick
import QtQuick.Shapes

Item {
    id: icon
    property string action: "send"
    property color ink: "#111111"
    implicitWidth: 22
    implicitHeight: 22

    Image {
        objectName: "agentCompletedImage"
        anchors.fill: parent
        visible: icon.action === "completed"
        source: visible ? "../../../ui/assets/agent-completed.svg" : ""
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
    }

    // All paths share a 24 px canvas and the same centre, independent of fonts.
    Shape {
        visible: icon.action !== "completed"
        anchors.centerIn: parent
        width: 24; height: 24
        transform: Scale {
            origin.x: 12; origin.y: 12
            xScale: icon.width / 24; yScale: icon.height / 24
        }
        ShapePath {
            readonly property bool filled: icon.action === "cancel"
            strokeColor: filled ? "transparent" : icon.ink
            fillColor: filled ? icon.ink : "transparent"
            strokeWidth: filled ? 0 : 1.7
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg {
                path: icon.action === "cancel" ? "M8 6H16Q18 6 18 8V16Q18 18 16 18H8Q6 18 6 16V8Q6 6 8 6Z"
                    : icon.action === "attach" ? "M12 5V19M5 12H19"
                    : icon.action === "link" ? "M10 13A5 5 0 0 0 17.5 13.5L20.5 10.5A5 5 0 0 0 13.5 3.5L11.8 5.2M14 11A5 5 0 0 0 6.5 10.5L3.5 13.5A5 5 0 0 0 10.5 20.5L12.2 18.8"
                    : icon.action === "capture" ? "M8 5L9.5 3H14.5L16 5H20Q22 5 22 7V19H2V7Q2 5 4 5H8M16 12A4 4 0 1 1 8 12A4 4 0 1 1 16 12"
                    : icon.action === "file" ? "M7 3H14L19 8V21H5V3H7M14 3V8H19M8 12H16M8 16H14"
                    : icon.action === "close" ? "M7 7L17 17M17 7L7 17"
                    : icon.action === "running" ? "M12 3A9 9 0 1 1 3 12"
                    : icon.action === "waiting" ? "M21 12A9 9 0 1 1 3 12A9 9 0 1 1 21 12M12 7V12L15 14"
                    : icon.action === "idle" ? "M5 6H19Q21 6 21 8V15Q21 17 19 17H9L5 20V17Q3 17 3 15V8Q3 6 5 6Z"
                    : "M12 20V4M5 11L12 4L19 11"
            }
        }
    }
}
