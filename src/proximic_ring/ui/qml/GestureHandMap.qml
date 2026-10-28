import QtQuick

Item {
    id: map
    required property string selectedGesture
    required property var describeGesture
    property var describeVoiceGesture: function(gesture) { return "" }
    property bool voiceOverview: false
    property bool voiceActive: false
    property bool voiceEditable: true
    property var gestureHint: describeGesture
    property Component cornerContent
    signal gestureChosen(string gesture)
    signal mappingChosen(string gesture, bool voice)
    objectName: "gestureMappingPanel"
    readonly property real canvasScale: Math.min(1, width / 932)
    implicitHeight: 494 * canvasScale
    readonly property var definitions: [
        {key:"circle-counterclockwise",title:"逆时针",x:0,y:0,width:156,asset:"counterclockwise",iw:15,ih:13.8,dx:0.6,outline:"#0F6D78"},
        {key:"circle-clockwise",title:"顺时针",x:183,y:0,width:156,asset:"clockwise",iw:15,ih:13.8,outline:"#0F6D78"},
        {key:"index-pinch",doubleKey:"double-index-pinch",title:"食拇捏合",x:0,y:121,width:195,asset:"index-pinch",iw:6.6,ih:11.4,outline:"#AF832C"},
        {key:"middle-pinch",doubleKey:"double-middle-pinch",title:"中拇捏合",x:0,y:331,width:195,asset:"middle-pinch",iw:11.4,ih:10.2,outline:"#02A1BA"},
        {key:"clench",title:"握拳",x:0,y:418,width:156,asset:"fist",iw:13.9804,ih:13.9804,dx:0.09,dy:0.09},
        {key:"snap",title:"响指",x:183,y:418,width:156,asset:"snap",iw:16.4998,ih:16.4998,dx:0.15,dy:0.15},
        {key:"swipe-up",title:"上滑",x:455,y:122,width:156,asset:"up",iw:19.2,ih:19.2,touch:true},
        {key:"swipe-left",title:"左滑",x:455,y:331,width:156,asset:"left",iw:19.2,ih:19.2,touch:true},
        {key:"tap",doubleKey:"double-tap",title:"点击",x:455,y:0,width:156,asset:"tap",iw:16.2,ih:16.2,touch:true},
        {key:"swipe-right",title:"右滑",x:455,y:418,width:156,asset:"right",iw:19.2,ih:19.2,touch:true},
        {key:"swipe-down",title:"下滑",x:455,y:209,width:156,asset:"down",iw:19.2,ih:19.2,touch:true}
    ]
    Item {
        id: canvas
        x: (map.width - width * scale) / 2; width: 932; height: 494
        scale: map.canvasScale; transformOrigin: Item.TopLeft
        Image {
            objectName: "gestureHandImage"
            x: 130; y: 61; width: 432; height: 432
            source: "../assets/figma/redesign-567-33097-hand.png"
            mirror: true; fillMode: Image.PreserveAspectCrop; smooth: true
        }
        Repeater {
            model: map.definitions
            GestureHandChip {
                required property var modelData
                definition: modelData; x: modelData.x
                // Keep the v5 canvas height while making space for two function rows.
                y: !map.voiceOverview ? modelData.y : ({122:110, 209:205, 331:315, 418:410}[modelData.y] ?? modelData.y)
                selectedGesture: map.selectedGesture
                mappingLabel: map.describeGesture(currentGesture)
                voiceMappingLabel: map.describeVoiceGesture(currentGesture)
                voiceActive: map.voiceActive
                voiceEditable: map.voiceEditable
                hint: map.gestureHint(currentGesture)
                onGestureChosen: function(gesture) { map.gestureChosen(gesture) }
                onMappingChosen: function(gesture, voice) { map.mappingChosen(gesture, voice) }
            }
        }
        Repeater {
            model: [{x:348,y:138,asset:"touch"},{x:159,y:74,asset:"rotate"},{x:197,y:281,asset:"middle"},{x:190,y:104,asset:"index"},{x:322,y:289,asset:"fist"}]
            Image {
                required property var modelData
                objectName: "gestureMarker_" + modelData.asset
                x: modelData.x - 5; y: modelData.y - 5; width: 30; height: 30
                source: "../assets/figma/redesign-544-30358-marker-" + modelData.asset + ".svg"
                smooth: true
            }
        }
        Loader {
            // v5 leaves 77 px between the full hand image and the voice card.
            x: 639; y: 355; width: 293; height: 139
            sourceComponent: map.cornerContent
        }
    }
}
