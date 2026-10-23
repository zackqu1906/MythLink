"""Hub 三层手势语义，不管理窗口焦点或音频。

词位是有端点的有限轨道，候选是环；横向越界退出选词，不把纵向用作发送。
最新用户约定覆盖演示 HTML：选词时 tap 开始语音，再 tap 结束并自动确认。
"""
from dataclasses import dataclass

DIRECTIONS={'swipe-up':1,'swipe-right':2,'swipe-down':3,'swipe-left':4}

@dataclass
class HubInteraction:
    mode:str='hub'
    rank:int=0
    slot:int=0
    count:int=0

    def select(self,rank,count):
        self.mode='action';self.rank=rank;self.slot=0;self.count=count

    def gesture(self,name):
        if self.mode=='voice':return 'end_voice' if name in ('tap','enter') else None
        if self.mode=='hub':return ('select',DIRECTIONS[name]) if name in DIRECTIONS else None
        if name=='escape':return 'confirm_word' if self.mode=='word' else 'back'
        if name=='enter':return 'confirm_word' if self.mode=='word' else 'submit'
        if self.mode=='action':
            if name=='tap' and self.count:return ('word',self.slot)
            rank=DIRECTIONS.get(name)
            if rank==self.rank:return 'submit'
            if rank==(self.rank+1)%4+1:return 'back'
        elif self.mode=='word':
            if name=='tap':return 'start_voice'
            if name in ('swipe-up','swipe-down'):return ('cycle',1 if name=='swipe-up' else -1)
            if name in ('swipe-left','swipe-right'):
                delta=-1 if name=='swipe-left' else 1
                if 0<=self.slot+delta<self.count:return ('word',self.slot+delta)
                return ('exit_word',delta)
        return None
