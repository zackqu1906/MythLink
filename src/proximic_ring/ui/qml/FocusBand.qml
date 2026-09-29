import QtQuick

Item {
    id: root
    property var request: ({})
    property var candidates: []
    property int selected: 0
    property int serial: 0
    property bool deferred: false
    property bool displayed: false
    property bool moving: false
    property bool warning: false
    property bool exiting: false
    property string exitKind: "timeout"
    property real remainingRatio: 1
    property real lightTime: 0
    property real pulseTime: 520
    property real fade: 1
    property real exitTime: 0
    property real bandOpacity: 1
    property real bx: 0
    property real by: 0
    property real bw: 1
    property real bh: 1
    property real gx: 0
    property real gy: 0
    property real gw: 1
    property real gh: 1
    property real ghostOpacity: 0
    property real bounceAmount: 0
    property real bounceX: 0
    property real bounceY: 0
    property int moveDuration: 340
    readonly property color ink: exiting && exitKind === "timeout" ? "#94A3B8" : warning ? "#FBBF24" : "#22D3EE"
    signal landed(int serial)
    signal exitDone()

    function finishMove() {
        moving = false
        pulseTime = 0
        pulseAnimation.restart()
        landed(serial)
    }
    function stopMotion() { motion.stop(); ghostMotion.stop(); bounce.stop(); pulseAnimation.stop() }
    function receive() {
        if (!request.fields || !request.fields.length) return
        var r = request.fields[request.index-1].rect
        candidates = request.fields; selected = request.index-1
        deferred = request.deferred; serial = request.serial
        if (request.motion === "update") {
            bx=r[0]; by=r[1]; bw=r[2]; bh=r[3]
            paint.requestPaint(); return
        }
        stopMotion(); exitAnimation.stop()
        displayed = true; exiting = false; warning = false
        fade = 1; exitTime = 0; bounceAmount = 0; bandOpacity = 1
        moving = true
        if (request.motion === "bounce") {
            bounceX = request.direction === "left" ? -14 : request.direction === "right" ? 14 : 0
            bounceY = request.direction === "up" ? -12 : request.direction === "down" ? 12 : 0
            bounce.start(); return
        }
        if (request.motion === "enter") {
            bx=r[0]; by=r[1]; bw=10; bh=r[3]; bandOpacity=0
            remainingRatio=1; moveDuration=520
        } else {
            gx=bx; gy=by; gw=bw; gh=bh; ghostOpacity=0
            ghostX.to=r[0]; ghostY.to=r[1]; ghostW.to=r[2]; ghostH.to=r[3]
            ghostMotion.start(); moveDuration=340
        }
        moveX.to=r[0]; moveY.to=r[1]; moveW.to=r[2]; moveH.to=r[3]
        motion.start()
    }
    function beginExit() {
        stopMotion(); moving=false; exiting=true; exitTime=0
        exitAnimation.duration = exitKind === "confirm" ? 540 : 320
        exitAnimation.restart()
    }
    onRequestChanged: receive()

    ParallelAnimation {
        id: motion
        NumberAnimation { id: moveX; target: root; property: "bx"; duration: root.moveDuration; easing.type: Easing.BezierSpline; easing.bezierCurve: [.22,1,.28,1,1,1] }
        NumberAnimation { id: moveY; target: root; property: "by"; duration: root.moveDuration; easing.type: Easing.BezierSpline; easing.bezierCurve: [.22,1,.28,1,1,1] }
        NumberAnimation { id: moveW; target: root; property: "bw"; duration: root.moveDuration; easing.type: Easing.BezierSpline; easing.bezierCurve: [.22,1,.28,1,1,1] }
        NumberAnimation { id: moveH; target: root; property: "bh"; duration: root.moveDuration; easing.type: Easing.BezierSpline; easing.bezierCurve: [.22,1,.28,1,1,1] }
        NumberAnimation { target: root; property: "bandOpacity"; to: 1; duration: 220 }
        onFinished: root.finishMove()
    }
    SequentialAnimation {
        id: ghostMotion
        PauseAnimation { duration: 60 }
        ParallelAnimation {
            NumberAnimation { id: ghostX; target: root; property: "gx"; duration: 360; easing.type: Easing.OutQuart }
            NumberAnimation { id: ghostY; target: root; property: "gy"; duration: 360; easing.type: Easing.OutQuart }
            NumberAnimation { id: ghostW; target: root; property: "gw"; duration: 360; easing.type: Easing.OutQuart }
            NumberAnimation { id: ghostH; target: root; property: "gh"; duration: 360; easing.type: Easing.OutQuart }
            SequentialAnimation {
                NumberAnimation { target: root; property: "ghostOpacity"; to: .3; duration: 65 }
                NumberAnimation { target: root; property: "ghostOpacity"; to: 0; duration: 295 }
            }
        }
    }
    SequentialAnimation {
        id: bounce
        NumberAnimation { target: root; property: "bounceAmount"; to: 1; duration: 180; easing.type: Easing.OutCubic }
        NumberAnimation { target: root; property: "bounceAmount"; to: 0; duration: 240; easing.type: Easing.OutCubic }
        onFinished: { root.moving=false; root.landed(root.serial) }
    }
    NumberAnimation { id: pulseAnimation; target: root; property: "pulseTime"; from: 0; to: 520; duration: 520 }
    NumberAnimation { target: root; property: "lightTime"; from: 0; to: 2600; duration: 2600; loops: Animation.Infinite; running: root.displayed }
    NumberAnimation {
        id: exitAnimation
        target: root; property: "exitTime"; from: 0; to: 1
        onFinished: { root.displayed=false; root.exitDone() }
    }

    Item {
        id: scene
        anchors.fill: parent
        opacity: root.exiting ? (root.exitKind === "confirm" ? 1-Math.max(0,(root.exitTime-.52)/.48) : 1-root.exitTime) : 1
        Canvas {
            id: paint
            anchors.fill: parent
            function rounded(x,y,w,h,r) {
                r=Math.min(r,w/2,h/2)
                var p=[[x+w/2,y],[x+w-r,y]]
                var centers=[[x+w-r,y+r,-90],[x+w-r,y+h-r,0],[x+r,y+h-r,90],[x+r,y+r,180]]
                for(var k=0;k<4;k++) {
                    var c=centers[k]
                    for(var j=0;j<=12;j++) {
                        var a=(c[2]+j*7.5)*Math.PI/180
                        p.push([c[0]+r*Math.cos(a),c[1]+r*Math.sin(a)])
                    }
                }
                p.push([x+w/2,y]); return p
            }
            function length(p) {
                var n=0
                for(var i=1;i<p.length;i++) n+=Math.hypot(p[i][0]-p[i-1][0],p[i][1]-p[i-1][1])
                return n
            }
            function range(c,p,from,to) {
                var n=0
                c.beginPath()
                for(var i=1;i<p.length;i++) {
                    var a=p[i-1], b=p[i], d=Math.hypot(b[0]-a[0],b[1]-a[1])
                    var lo=Math.max(0,from-n), hi=Math.min(d,to-n)
                    if(hi>lo && d>0) {
                        c.moveTo(a[0]+(b[0]-a[0])*lo/d,a[1]+(b[1]-a[1])*lo/d)
                        c.lineTo(a[0]+(b[0]-a[0])*hi/d,a[1]+(b[1]-a[1])*hi/d)
                    }
                    n+=d
                }
                c.stroke()
            }
            onPaint: {
                var c=getContext("2d"); c.reset()
                if(!root.displayed) return
                c.lineWidth=1; c.strokeStyle="#778BA2"; c.globalAlpha=.6
                if(!root.exiting) for(var i=0;i<root.candidates.length;i++) {
                    if(i===root.selected) continue
                    var r=root.candidates[i].rect, p=rounded(r[0]-2,r[1]-2,r[2]+4,r[3]+4,10), n=length(p)
                    for(var s=0;s<n;s+=11) range(c,p,s,Math.min(n,s+5))
                }
                if(root.moving) return
                var x=root.bx, y=root.by, w=root.bw, h=root.bh
                var ring=rounded(x-6,y-6,w+12,h+12,18), perimeter=length(ring)
                c.strokeStyle=root.ink; c.lineWidth=2.5; c.lineCap="round"
                c.globalAlpha=root.warning ? .65+.3*Math.sin(root.lightTime/150) : .9
                var ratio=root.remainingRatio
                if(root.exiting && root.exitKind==="confirm") ratio*=Math.max(0,1-root.exitTime/.52)
                range(c,ring,0,perimeter*ratio)
                var light=rounded(x-1,y-1,w+2,h+2,12), total=length(light), start=total*root.lightTime/2600
                c.strokeStyle="#A5F3FC"; c.lineWidth=3; c.globalAlpha=.9
                range(c,light,start,Math.min(total,start+total*.13))
                if(start+total*.13>total) range(c,light,0,start+total*.13-total)
            }
        }
        Rectangle {
            x: root.gx-1; y: root.gy-1; width: root.gw+2; height: root.gh+2
            color: "transparent"; radius: 12; border.width: 2; border.color: "#88A5F3FC"
            opacity: root.ghostOpacity
        }
        Item {
            id: band
            objectName: "selectedFieldBand"
            x: root.bx+root.bounceAmount*root.bounceX; y: root.by+root.bounceAmount*root.bounceY
            width: root.bw; height: root.bh
            opacity: root.bandOpacity
            scale: root.exiting ? (root.exitKind === "timeout" ? 1+.016*root.exitTime : 1+.012*Math.sin(Math.PI*root.exitTime)) : 1
            Repeater {
                model: 5
                Rectangle {
                    required property int index
                    x: -3-index*2; y: -3-index*2
                    width: band.width+6+index*4; height: band.height+6+index*4
                    radius: Math.min(14+index*2,height/2)
                    color: "transparent"; border.width: 3
                    border.color: root.ink; opacity: .13-index*.022
                }
            }
            Rectangle {
                anchors.fill: parent; anchors.margins: -1
                radius: Math.min(12,height/2); border.width: 2; border.color: root.ink
                color: "#0D22D3EE"
            }
            Rectangle {
                anchors.fill: parent; radius: Math.min(12,height/2)
                color: "#FFFFFF"
                opacity: root.exiting && root.exitKind === "confirm" ? .3*Math.max(0,Math.sin(root.exitTime/.52*Math.PI)) : 0
            }
            Rectangle {
                x: -3-root.pulseTime/520*9; y: -3-root.pulseTime/520*9
                width: band.width+6+root.pulseTime/520*18; height: band.height+6+root.pulseTime/520*18
                color: "transparent"; radius: Math.min(14,height/2); border.width: 2; border.color: "#A5F3FC"
                opacity: root.moving || root.exiting ? 0 : .85*Math.sin(Math.PI*root.pulseTime/520)
            }
        }
        Rectangle {
            id: chip
            x: Math.max(8,Math.min(root.width-width-8,root.bx+root.bw-width))
            y: root.by>=36 ? root.by-height-11 : root.by+root.bh+11
            width: caption.implicitWidth+22; height: 25; radius: 8
            color: "#EE101C27"; border.width: 1; border.color: root.ink
            opacity: root.bandOpacity
            Text {
                id: caption
                anchors.centerIn: parent
                text: root.deferred ? "地址栏 · Tap 聚焦" : "输入框 "+(root.selected+1)+" / "+root.candidates.length+" · Tap 语音"
                color: "#CFFAFE"; font.pixelSize: 12; font.weight: Font.DemiBold
            }
        }
    }
    onCandidatesChanged: paint.requestPaint()
    onSelectedChanged: paint.requestPaint()
    onRemainingRatioChanged: paint.requestPaint()
    onWarningChanged: paint.requestPaint()
    onLightTimeChanged: paint.requestPaint()
    onMovingChanged: paint.requestPaint()
    onExitTimeChanged: paint.requestPaint()
    onBxChanged: paint.requestPaint()
    onByChanged: paint.requestPaint()
    onBwChanged: paint.requestPaint()
    onBhChanged: paint.requestPaint()
}
