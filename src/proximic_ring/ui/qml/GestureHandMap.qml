import QtQuick

Item {
    id: map
    required property string selectedGesture
    required property var describeGesture
    signal gestureChosen(string gesture)
    objectName: "gestureMappingPanel"
    readonly property real canvasScale: Math.min(1, width / 932)
    implicitHeight: 494 * canvasScale
    readonly property var definitions: [
        {key:"circle-counterclockwise",title:"逆时针",x:0,y:0,width:110,asset:"counterclockwise",iw:15,ih:13.8,dx:0.6,outline:"#0F6D78"},
        {key:"circle-clockwise",title:"顺时针",x:122,y:0,width:110,asset:"clockwise",iw:15,ih:13.8,outline:"#0F6D78"},
        {key:"index-pinch",doubleKey:"double-index-pinch",title:"食拇捏合",x:0,y:121,width:195,asset:"index-pinch",iw:6.6,ih:11.4,outline:"#AF832C"},
        {key:"middle-pinch",doubleKey:"double-middle-pinch",title:"中拇捏合",x:0,y:331,width:195,asset:"middle-pinch",iw:11.4,ih:10.2,outline:"#02A1BA"},
        {key:"clench",title:"握拳",x:0,y:418,width:92,asset:"fist",iw:13.9804,ih:13.9804,dx:0.09,dy:0.09},
        {key:"snap",title:"响指",x:104,y:418,width:92,asset:"snap",iw:16.4998,ih:16.4998,dx:0.15,dy:0.15},
        {key:"swipe-up",title:"上滑",x:704,y:0,width:92,asset:"up",iw:19.2,ih:19.2,touch:true},
        {key:"swipe-left",title:"左滑",x:568,y:56,width:92,asset:"left",iw:19.2,ih:19.2,touch:true},
        {key:"tap",doubleKey:"double-tap",title:"点击",x:672,y:56,width:156,asset:"tap",iw:16.2,ih:16.2,touch:true},
        {key:"swipe-right",title:"右滑",x:840,y:56,width:92,asset:"right",iw:19.2,ih:19.2,touch:true},
        {key:"swipe-down",title:"下滑",x:702,y:112,width:92,asset:"down",iw:19.2,ih:19.2,touch:true}
    ]
    Item {
        id: canvas
        x: (map.width - width * scale) / 2; width: 932; height: 494
        scale: map.canvasScale; transformOrigin: Item.TopLeft
        Image {
            objectName: "gestureHandImage"
            x: 220; y: 61; width: 432; height: 432
            source: "../assets/figma/redesign-567-33097-hand.png"
            mirror: true; fillMode: Image.PreserveAspectCrop; smooth: true
        }
        Repeater {
            model: map.definitions
            GestureHandChip {
                required property var modelData
                definition: modelData; x: modelData.x; y: modelData.y
                selectedGesture: map.selectedGesture
                hint: map.describeGesture(currentGesture)
                onGestureChosen: function(gesture) { map.gestureChosen(gesture) }
            }
        }
        Repeater {
            model: [{x:438,y:138,asset:"touch"},{x:249,y:74,asset:"rotate"},{x:287,y:281,asset:"middle"},{x:280,y:104,asset:"index"},{x:412,y:289,asset:"fist"}]
            Image {
                required property var modelData
                objectName: "gestureMarker_" + modelData.asset
                x: modelData.x - 5; y: modelData.y - 5; width: 30; height: 30
                source: "../assets/figma/redesign-567-33097-marker-" + modelData.asset + ".svg"
                smooth: true
            }
        }
    }
}
