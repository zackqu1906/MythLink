"""Screen-point trajectories → bounded rectangles → original pixel crops.

Do not use normalized handwriting coordinates. Extrema keep every actual
pointer sample even when the display trail is decimated. A selection belongs
to one screen; negative desktop origins and different pixel scales are valid.
"""
from dataclasses import dataclass, field
import math


@dataclass
class Selection:
    screen: tuple
    points: list = field(default_factory=list)
    bounds: tuple | None = None

    def add(self,x,y):
        if not math.isfinite(x) or not math.isfinite(y):return
        sx,sy,w,h=self.screen
        x=max(sx,min(sx+w,x));y=max(sy,min(sy+h,y))
        if self.bounds:
            l,t,r,b=self.bounds;self.bounds=min(l,x),min(t,y),max(r,x),max(b,y)
        else:self.bounds=x,y,x,y
        if not self.points or math.hypot(x-self.points[-1][0],y-self.points[-1][1])>=1:
            self.points.append((x,y))
        if len(self.points)>4096:self.points=self.points[::2]+[self.points[-1]]

    @property
    def valid(self):
        return bool(self.bounds and self.bounds[2]-self.bounds[0]>=8 and self.bounds[3]-self.bounds[1]>=8)

    def pixels(self,width,height):
        if not self.valid:raise ValueError('选区太小，请重新圈出需要的内容')
        sx,sy,w,h=self.screen;l,t,r,b=self.bounds
        # Use the returned image dimensions, not a guessed integer Retina ratio.
        left=max(0,math.floor((l-sx)*width/w));top=max(0,math.floor((t-sy)*height/h))
        right=min(width,math.ceil((r-sx)*width/w));bottom=min(height,math.ceil((b-sy)*height/h))
        return left,top,right-left,bottom-top
