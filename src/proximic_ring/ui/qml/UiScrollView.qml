import QtQuick
import QtQuick.Controls

ScrollView {
    id: view
    // Preserve ScrollView's single-child implicit sizing without counting
    // the transparent drag area as a second content item.
    readonly property var measuredItems: {
        let items = []
        for (let i = 0; i < contentChildren.length; ++i)
            if (contentChildren[i] !== windowDragArea) items.push(contentChildren[i])
        return items
    }
    contentWidth: measuredItems.length === 1 ? measuredItems[0].implicitWidth : -1
    contentHeight: measuredItems.length === 1 ? measuredItems[0].implicitHeight : -1
    // A Flickable consumes otherwise unhandled presses. Put the fallback
    // inside its content, below buttons and text fields.
    UiWindowDragArea {
        id: windowDragArea
        parent: view.contentItem ? view.contentItem.contentItem : null
        z: -1
        width: Math.max(view.availableWidth, view.contentWidth)
        height: Math.max(view.availableHeight, view.contentHeight)
    }
}
