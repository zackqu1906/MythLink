import QtQuick
import QtQuick.Window

// Place behind content: controls handle their presses before this fallback.
MouseArea {
    property point pressPosition
    enabled: Qt.platform.os === "osx" && Window.window !== null
             && Window.window.visibility !== Window.FullScreen
    acceptedButtons: Qt.LeftButton
    preventStealing: true
    onWheel: function(wheel) { wheel.accepted = false }
    onPressed: function(mouse) { pressPosition = Qt.point(mouse.x, mouse.y) }
    onPositionChanged: function(mouse) {
        if (pressed && Math.hypot(mouse.x - pressPosition.x, mouse.y - pressPosition.y)
                >= Qt.styleHints.startDragDistance)
            Window.window.startSystemMove()
    }
}
