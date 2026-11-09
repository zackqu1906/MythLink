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
    // A short window keeps the mappings readable in a grid instead of shrinking
    // the full hand illustration and its labels to thumbnail size.
    readonly property bool compact: height < implicitHeight * 0.75
    readonly property real canvasHeight: compact ? 252 : 494
    readonly property real canvasScale: Math.max(0, Math.min(1, width / 932, height / canvasHeight))
    property string hoveredGesture: ""
    readonly property string highlightedGesture: hoveredGesture || selectedGesture
    readonly property real cardHeight: 76
    readonly property real cardGap: 20
    readonly property real groupPadding: 8
    readonly property real touchCardWidth: 144
    readonly property real touchColumnGap: 8
    readonly property real touchColumn: touchCardWidth + touchColumnGap
    readonly property real touchLeft: 932 - 3 * touchCardWidth - 2 * touchColumnGap - groupPadding
    readonly property rect rotateGroup: Qt.rect(0, 0, 312 + 2 * groupPadding, cardHeight + 2 * groupPadding)
    readonly property rect fistGroup: Qt.rect(0, 494 - cardHeight - 2 * groupPadding,
                                             312 + 2 * groupPadding, cardHeight + 2 * groupPadding)
    readonly property rect touchGroup: Qt.rect(touchLeft - groupPadding, 0,
                                              3 * touchCardWidth + 2 * touchColumnGap + 2 * groupPadding,
                                              3 * cardHeight + 2 * cardGap + 2 * groupPadding)
    implicitHeight: 494 * Math.min(1, width / 932)
    readonly property var definitions: [
        {key:"circle-counterclockwise",title:"逆时针",x:groupPadding,y:groupPadding,width:148,asset:"counterclockwise",iw:15,ih:13.8,dx:0.6,outline:"#0F6D78"},
        {key:"circle-clockwise",title:"顺时针",x:164+groupPadding,y:groupPadding,width:148,asset:"clockwise",iw:15,ih:13.8,outline:"#0F6D78"},
        {key:"index-pinch",doubleKey:"double-index-pinch",title:"食拇捏合",x:0,y:170,width:184,asset:"index-pinch",iw:6.6,ih:11.4,outline:"#AF832C"},
        {key:"middle-pinch",doubleKey:"double-middle-pinch",title:"中拇捏合",x:0,y:306,width:184,asset:"middle-pinch",iw:11.4,ih:10.2,outline:"#02A1BA"},
        {key:"clench",title:"握拳",x:groupPadding,y:fistGroup.y+groupPadding,width:148,asset:"fist",iw:13.9804,ih:13.9804,dx:0.09,dy:0.09},
        {key:"snap",title:"响指",x:164+groupPadding,y:fistGroup.y+groupPadding,width:148,asset:"snap",iw:16.4998,ih:16.4998,dx:0.15,dy:0.15},
        {key:"swipe-up",title:"上滑",x:touchLeft+touchColumn,y:groupPadding,width:touchCardWidth,asset:"up",iw:19.2,ih:19.2,touch:true},
        {key:"swipe-left",title:"左滑",x:touchLeft,y:groupPadding+cardHeight+cardGap,width:touchCardWidth,asset:"left",iw:19.2,ih:19.2,touch:true},
        {key:"tap",doubleKey:"double-tap",title:"点击",x:touchLeft+touchColumn,y:groupPadding+cardHeight+cardGap,width:touchCardWidth,asset:"tap",iw:16.2,ih:16.2,touch:true},
        {key:"swipe-right",title:"右滑",x:touchLeft+2*touchColumn,y:groupPadding+cardHeight+cardGap,width:touchCardWidth,asset:"right",iw:19.2,ih:19.2,touch:true},
        {key:"swipe-down",title:"下滑",x:touchLeft+touchColumn,y:groupPadding+2*(cardHeight+cardGap),width:touchCardWidth,asset:"down",iw:19.2,ih:19.2,touch:true}
    ]
    readonly property var markers: [{x:136,y:53,asset:"touch"},{x:29,y:13,asset:"rotate"},{x:67,y:220,asset:"middle"},{x:60,y:43,asset:"index"},{x:192,y:228,asset:"fist"}]
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
            var r = Math.min(12, before / 2, after / 2)
            path += " L " + (b[0] + (a[0] - b[0]) * r / before) + " " + (b[1] + (a[1] - b[1]) * r / before)
            path += " Q " + b[0] + " " + b[1] + " " + (b[0] + (c[0] - b[0]) * r / after) + " " + (b[1] + (c[1] - b[1]) * r / after)
        }
        return path + " L " + points[points.length - 1][0] + " " + points[points.length - 1][1]
    }
    function sideLink(from, to) {
        var bend = (from.x + to.x) / 2
        return "M " + from.x + " " + from.y + " C " + bend + " " + from.y
            + " " + bend + " " + to.y + " " + to.x + " " + to.y
    }
    readonly property var connectors: {
        var rotate = markerCenter("rotate"), index = markerCenter("index")
        var middle = markerCenter("middle"), fist = markerCenter("fist"), touch = markerCenter("touch")
        var rotateX = rotateGroup.x + rotateGroup.width / 2
        var rotateBottom = rotateGroup.y + rotateGroup.height
        var fistX = fistGroup.x + fistGroup.width / 2
        var fistBusY = fistGroup.y - 12
        return [
            {name:"rotate",keys:["circle-clockwise","circle-counterclockwise"],color:"#0F6D78",
                path:"M " + rotate.x + " " + (rotate.y-10) + " C " + rotate.x + " " + (rotateBottom+20)
                    + " " + rotateX + " " + (rotateBottom+20) + " " + rotateX + " " + rotateBottom},
            {name:"index",keys:["index-pinch","double-index-pinch"],color:"#AF832C",
                path:sideLink(Qt.point(index.x-10,index.y),Qt.point(184,208))},
            {name:"middle",keys:["middle-pinch","double-middle-pinch"],color:"#02A1BA",
                path:sideLink(Qt.point(middle.x-10,middle.y),Qt.point(184,344))},
            {name:"fist",keys:["clench","snap"],color:"#949CA9",
                path:route([[fist.x,fist.y+10],[fist.x,fistBusY],[fistX,fistBusY],[fistX,fistGroup.y]])},
            {name:"touch",keys:["tap","double-tap","swipe-up","swipe-down","swipe-left","swipe-right"],color:"#0225BA",
                path:sideLink(Qt.point(touch.x+10,touch.y),Qt.point(touchGroup.x,touchGroup.y+touchGroup.height/2))}
        ]
    }
    Item {
        id: canvas
        x: (map.width - width * scale) / 2; width: 932; height: map.canvasHeight
        scale: map.canvasScale; transformOrigin: Item.TopLeft
        Image {
            id: hand
            objectName: "gestureHandImage"
            visible: !map.compact
            x: 196; y: 94; width: 390; height: 390
            source: "../assets/figma/redesign-567-33097-hand.png"
            mirror: true; fillMode: Image.PreserveAspectCrop; smooth: true
        }
        // Each family shares one light card and one leader to the hand.
        Repeater {
            model: [
                {name:"rotate",bounds:map.rotateGroup,color:"#F8FBFB",border:"#EAF3F3"},
                {name:"touch",bounds:map.touchGroup,color:"#F8F9FD",border:"#EDF0F9"},
                {name:"fist",bounds:map.fistGroup,color:"#F9FAFC",border:"#EEF0F4"}
            ]
            Rectangle {
                required property var modelData
                objectName: "gestureGroup_" + modelData.name
                visible: !map.compact
                z: -1
                x: modelData.bounds.x; y: modelData.bounds.y
                width: modelData.bounds.width; height: modelData.bounds.height
                radius: 18; color: modelData.color; border.color: modelData.border
            }
        }
        Repeater {
            model: map.connectors
            Shape {
                id: connector
                required property var modelData
                objectName: "gestureConnector_" + modelData.name
                visible: !map.compact
                readonly property bool highlighted: modelData.keys.indexOf(map.highlightedGesture) >= 0
                readonly property color lineColor: modelData.color
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: Qt.rgba(connector.lineColor.r, connector.lineColor.g, connector.lineColor.b, 0.3)
                    strokeWidth: 1.5
                    fillColor: "transparent"; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                    PathSvg { path: connector.modelData.path }
                }
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    opacity: connector.highlighted ? 0.85 : 0
                    ShapePath {
                        strokeColor: connector.lineColor; strokeWidth: 1.8
                        fillColor: "transparent"; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                        PathSvg { path: connector.modelData.path }
                    }
                    Behavior on opacity { NumberAnimation { duration: 140 } }
                }
            }
        }
        Repeater {
            model: map.definitions
            GestureHandChip {
                required property int index
                required property var modelData
                definition: modelData
                x: map.compact ? (index % 4) * 236 : modelData.x
                y: map.compact ? Math.floor(index / 4) * 88 : modelData.y
                width: map.compact ? 224 : modelData.width
                height: map.cardHeight
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
            // Markers stay registered to the hand when it moves.
            model: map.markers
            Image {
                required property var modelData
                objectName: "gestureMarker_" + modelData.asset
                visible: !map.compact
                x: hand.x + (modelData.x + 10) * hand.width / 432 - width / 2
                y: hand.y + (modelData.y + 10) * hand.height / 432 - height / 2
                width: 30; height: 30
                source: "../assets/figma/redesign-567-33097-marker-" + modelData.asset + ".svg"
                smooth: true
            }
        }
        Loader {
            anchors.right: parent.right; anchors.bottom: parent.bottom
            width: map.compact ? 224 : 293; height: map.compact ? 76 : 139
            sourceComponent: map.cornerContent
        }
    }
}
