import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    width: 300; height: 140
    property bool nativeMaterial: false
    radius: 10
    color: nativeMaterial ? "#5420242b" : "#e3222630"
    border.width: 0
    HoverHandler {}

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 10
        spacing: 5
        RowLayout {
            Layout.fillWidth: true
            Rectangle { width: 6; height: 6; radius: 3; color: "#6e9eff" }
            Text { text: "猜你想做"; color: "#bec7d8"; font.pixelSize: 11; textFormat: Text.PlainText }
            Text {
                text: recommendation.age
                color: "#8e99ac"; font.pixelSize: 10
                Layout.fillWidth: true
                textFormat: Text.PlainText
            }
            Text {
                text: recommendation.count ? recommendation.position + " / " + recommendation.count : ""
                color: "#bec7d8"; font.pixelSize: 11; textFormat: Text.PlainText
            }
            ToolButton {
                objectName: "historyRecommendationClose"
                text: "×"; implicitWidth: 22; implicitHeight: 22
                focusPolicy: Qt.NoFocus
                Accessible.name: "关闭推荐"
                contentItem: Text { text: "×"; color: "#bec7d8"; font.pixelSize: 18; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 5; color: parent.hovered ? "#20ffffff" : "transparent" }
                onClicked: recommendation.dismiss()
            }
        }
        Text {
            objectName: "historyRecommendationTitle"
            text: recommendation.title
            color: "#f6f8fc"; font.pixelSize: 14; font.weight: Font.DemiBold
            Layout.fillWidth: true; maximumLineCount: 1; elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        Text {
            id: planText
            objectName: "historyRecommendationPlan"
            text: recommendation.plan
            color: "#bec7d8"; font.pixelSize: 11
            Layout.fillWidth: true; Layout.fillHeight: true
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
            textFormat: Text.PlainText
            HoverHandler { id: planHover }
            ToolTip {
                id: planTip
                objectName: "historyRecommendationPlanTooltip"
                parent: planText
                visible: recommendation.visible && recommendation.count > 0 && planHover.hovered
                delay: 500
                width: 300
                text: recommendation.plan
                contentItem: Text {
                    text: planTip.text
                    color: "#f6f8fc"; font.pixelSize: 11
                    textFormat: Text.PlainText; wrapMode: Text.Wrap
                }
                background: Rectangle { color: "#ee222630"; radius: 7 }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text {
                text: recommendation.refreshing ? "后台更新中…" : recommendation.interactionHint
                color: "#8e99ac"; font.pixelSize: 10
                Layout.fillWidth: true; textFormat: Text.PlainText
            }
            Button {
                id: executeButton
                objectName: "historyRecommendationExecute"
                text: recommendation.actionLabel; enabled: recommendation.count > 0
                implicitHeight: 26; implicitWidth: 92; focusPolicy: Qt.NoFocus
                Accessible.name: recommendation.actionLabel + "：" + recommendation.title
                contentItem: Text { text: parent.text; color: parent.enabled ? "#ffffff" : "#8390a6"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 7; color: !parent.enabled ? "#304463" : parent.hovered ? "#3568d5" : "#2456c4" }
                ToolTip {
                    id: threadTip
                    objectName: "historyRecommendationThreadTooltip"
                    parent: executeButton
                    visible: executeButton.hovered && recommendation.visible && recommendation.threadTitle.length > 0
                    delay: 500; width: 300
                    text: recommendation.threadTitle
                    contentItem: Text {
                        text: threadTip.text; textFormat: Text.PlainText; wrapMode: Text.Wrap
                        color: "#f6f8fc"; font.pixelSize: 11
                    }
                    background: Rectangle { color: "#ee222630"; radius: 7 }
                }
                onClicked: recommendation.execute()
            }
        }
    }
    WheelHandler {
        property double lastStep: 0
        onWheel: event => {
            const now = Date.now()
            if (event.angleDelta.y !== 0 && now - lastStep > 180) {
                recommendation.step(event.angleDelta.y < 0 ? 1 : -1)
                lastStep = now
            }
            event.accepted = true
        }
    }
}
