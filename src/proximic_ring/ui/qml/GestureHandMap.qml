import QtQuick
import QtQuick.Shapes

Item {
    id: map
    required property string selectedGesture
    required property var describeGesture
    property var describeVoiceGesture: function(gesture) { return "" }
    property bool voiceActive: false
    property bool voiceEditable: true
    property var gestureHint: describeGesture
    property Component cornerContent
    signal gestureChosen(string gesture)
    signal mappingChosen(string gesture, bool voice)
    objectName: "gestureMappingPanel"
    readonly property real canvasScale: Math.min(1, width / 932)
    property string hoveredGesture: ""
    readonly property string highlightedGesture: hoveredGesture || selectedGesture
    readonly property real cardHeight: 76
    readonly property real cardGap: 12
    readonly property real touchColumn: 148 + cardGap
    readonly property real touchLeft: 932 - 3 * 148 - 2 * cardGap
    implicitHeight: 494 * canvasScale
    readonly property var definitions: [
        {key:"circle-counterclockwise",title:"逆时针",x:0,y:0,width:148,asset:"counterclockwise",iw:15,ih:13.8,dx:0.6,outline:"#0F6D78"},
        {key:"circle-clockwise",title:"顺时针",x:164,y:0,width:148,asset:"clockwise",iw:15,ih:13.8,outline:"#0F6D78"},
        {key:"index-pinch",doubleKey:"double-index-pinch",title:"食拇捏合",x:0,y:170,width:184,asset:"index-pinch",iw:6.6,ih:11.4,outline:"#AF832C"},
        {key:"middle-pinch",doubleKey:"double-middle-pinch",title:"中拇捏合",x:0,y:306,width:184,asset:"middle-pinch",iw:11.4,ih:10.2,outline:"#02A1BA"},
        {key:"clench",title:"握拳",x:0,y:418,width:148,asset:"fist",iw:13.9804,ih:13.9804,dx:0.09,dy:0.09},
        {key:"snap",title:"响指",x:164,y:418,width:148,asset:"snap",iw:16.4998,ih:16.4998,dx:0.15,dy:0.15},
        {key:"swipe-up",title:"上滑",x:touchLeft+touchColumn,y:0,width:148,asset:"up",iw:19.2,ih:19.2,touch:true},
        {key:"swipe-left",title:"左滑",x:touchLeft,y:cardHeight+cardGap,width:148,asset:"left",iw:19.2,ih:19.2,touch:true},
        {key:"tap",doubleKey:"double-tap",title:"点击",x:touchLeft+touchColumn,y:cardHeight+cardGap,width:148,asset:"tap",iw:16.2,ih:16.2,touch:true},
        {key:"swipe-right",title:"右滑",x:touchLeft+2*touchColumn,y:cardHeight+cardGap,width:148,asset:"right",iw:19.2,ih:19.2,touch:true},
        {key:"swipe-down",title:"下滑",x:touchLeft+touchColumn,y:2*(cardHeight+cardGap),width:148,asset:"down",iw:19.2,ih:19.2,touch:true}
    ]
    readonly property var markers: [{x:218,y:77,asset:"touch"},{x:29,y:13,asset:"rotate"},{x:67,y:220,asset:"middle"},{x:60,y:43,asset:"index"},{x:192,y:228,asset:"fist"}]
    function markerCenter(name) {
        var point = markers.filter(function(marker) { return marker.asset === name })[0]
        return Qt.point(hand.x + (point.x + 10) * hand.width / 432,
                        hand.y + (point.y + 10) * hand.height / 432)
    }
    // Rounded leaders live between the illustration and the cards, never in hit areas.
    function route(points) {
        var path = "M " + points[0][0] + " " + points[0][1]
        for (var i = 1; i < points.length - 1; ++i) {
            var a = points[i - 1], b = points[i], c = points[i + 1]
            var before = Math.hypot(b[0] - a[0], b[1] - a[1])
            var after = Math.hypot(c[0] - b[0], c[1] - b[1])
            if (!before || !after) continue
            var r = Math.min(4, before / 2, after / 2)
            path += " L " + (b[0] + (a[0] - b[0]) * r / before) + " " + (b[1] + (a[1] - b[1]) * r / before)
            path += " Q " + b[0] + " " + b[1] + " " + (b[0] + (c[0] - b[0]) * r / after) + " " + (b[1] + (c[1] - b[1]) * r / after)
        }
        return path + " L " + points[points.length - 1][0] + " " + points[points.length - 1][1]
    }
    readonly property var connectors: {
        var rotate = markerCenter("rotate"), index = markerCenter("index")
        var middle = markerCenter("middle"), fist = markerCenter("fist"), touch = markerCenter("touch")
        var center = touchLeft + touchColumn, bus = center - cardGap / 2
        var middleY = cardHeight + cardGap + cardHeight / 2, lowerY = 2 * (cardHeight + cardGap) - cardGap / 2
        var rightBus = center + touchColumn - cardGap / 2
        return [
            {name:"rotate",keys:["circle-clockwise","circle-counterclockwise"],color:"#0F6D78",
                path:route([[rotate.x,rotate.y-10],[rotate.x,92],[74,92],[74,cardHeight]]) + " " + route([[rotate.x,92],[238,92],[238,cardHeight]])},
            {name:"index",keys:["index-pinch","double-index-pinch"],color:"#AF832C",
                path:route([[index.x-10,index.y],[220,index.y],[220,208],[184,208]])},
            {name:"middle",keys:["middle-pinch","double-middle-pinch"],color:"#02A1BA",
                path:route([[middle.x-10,middle.y],[222,middle.y],[222,344],[184,344]])},
            {name:"fist",keys:["clench"],color:"#949CA9",
                path:route([[fist.x,fist.y+10],[fist.x,398],[74,398],[74,418]])},
            {name:"touch",keys:["tap","double-tap","swipe-up","swipe-down","swipe-left","swipe-right"],color:"#0225BA",
                path:route([[touch.x+10,touch.y],[436,touch.y],[436,lowerY],[bus,lowerY],[bus,cardHeight/2],[center,cardHeight/2]])
                    + " " + route([[touchLeft+148,middleY],[center,middleY]])
                    + " " + route([[bus,lowerY],[bus,2*(cardHeight+cardGap)+cardHeight/2],[center,2*(cardHeight+cardGap)+cardHeight/2]])
                    + " " + route([[bus,lowerY],[rightBus,lowerY],[rightBus,middleY],[center+touchColumn,middleY]])}
        ]
    }
    Item {
        id: canvas
        x: (map.width - width * scale) / 2; width: 932; height: 494
        scale: map.canvasScale; transformOrigin: Item.TopLeft
        Image {
            id: hand
            objectName: "gestureHandImage"
            x: 196; y: 94; width: 390; height: 390
            source: "../assets/figma/redesign-567-33097-hand.png"
            mirror: true; fillMode: Image.PreserveAspectCrop; smooth: true
        }
        Repeater {
            model: map.connectors
            Shape {
                required property var modelData
                objectName: "gestureConnector_" + modelData.name
                readonly property bool highlighted: modelData.keys.indexOf(map.highlightedGesture) >= 0
                anchors.fill: parent
                opacity: highlighted ? 0.85 : 0.28
                ShapePath {
                    strokeColor: modelData.color; strokeWidth: 1.25
                    fillColor: "transparent"; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                    PathSvg { path: modelData.path }
                }
                Behavior on opacity { NumberAnimation { duration: 120 } }
            }
        }
        Repeater {
            model: map.definitions
            GestureHandChip {
                required property var modelData
                definition: modelData; x: modelData.x
                y: modelData.y; height: map.cardHeight
                selectedGesture: map.selectedGesture
                mappingLabel: map.describeGesture(currentGesture)
                voiceMappingLabel: map.describeVoiceGesture(currentGesture)
                voiceActive: map.voiceActive
                voiceEditable: map.voiceEditable
                hint: map.gestureHint(currentGesture)
                onHoveredChanged: {
                    if (hovered) map.hoveredGesture = currentGesture
                    else if (map.hoveredGesture === currentGesture) map.hoveredGesture = ""
                }
                onCurrentGestureChanged: if (hovered) map.hoveredGesture = currentGesture
                onGestureChosen: function(gesture) { map.gestureChosen(gesture) }
                onMappingChosen: function(gesture, voice) { map.mappingChosen(gesture, voice) }
            }
        }
        Repeater {
            // Original v6 markers stay registered to the hand when it moves.
            model: map.markers
            Image {
                required property var modelData
                objectName: "gestureMarker_" + modelData.asset
                x: hand.x + (modelData.x + 10) * hand.width / 432 - width / 2
                y: hand.y + (modelData.y + 10) * hand.height / 432 - height / 2
                width: 30; height: 30
                source: "../assets/figma/redesign-567-33097-marker-" + modelData.asset + ".svg"
                smooth: true
            }
        }
        Loader {
            anchors.right: parent.right; anchors.bottom: parent.bottom
            width: 293; height: 139
            sourceComponent: map.cornerContent
        }
    }
}
