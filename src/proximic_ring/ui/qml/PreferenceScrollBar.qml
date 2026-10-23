import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.ScrollBar {
    id: bar
    padding: 0; implicitWidth: 4; minimumSize: 0.08
    policy: Basic.ScrollBar.AsNeeded
    opacity: size < 1 ? 1 : 0
    contentItem: Rectangle { implicitWidth: 4; radius: 2; color: bar.pressed ? "#59616D" : "#A7AFBF" }
    background: Item { }
}
