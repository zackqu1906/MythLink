import QtQuick

Item {
    objectName: "ringProductImage"
    implicitWidth: 126
    implicitHeight: 140
    Image {
        anchors.fill: parent
        source: "../assets/figma/ring-product.png"
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
    }
}
