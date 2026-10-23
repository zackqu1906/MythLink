"""Ring interaction state, independent of ASR latency and server request ordering."""
import threading

class Interaction:
    def __init__(self, emit, snapshot):
        self.emit=emit;self.snapshot=snapshot;self.lock=threading.RLock()
        self.phase='idle';self.frame=None;self.session=None;self.retired=set()
    def phase_changed(self,phase):
        self.phase=phase;self.emit(('voice_phase',phase))
    def gesture(self,name,busy=False):
        with self.lock:
            s=self.snapshot();r=s.get('request')
            if name=='swipe-left':
                if self.frame is not None:
                    if self.session is not None:self.retired.add(self.session)
                    self.phase_changed('cancelling')
                self.emit(('cancel','ring_gesture'));return False
            if self.phase in ('starting','recording'):
                if name in ('tap','swipe-right'):
                    self.frame['mode']='steer' if name=='swipe-right' else 'normal'
                    self.phase_changed('finishing')
                    self.emit(('voice_submit_mode',self.frame['mode']))
                    return True # RuntimeSettings.end contains both endpoint gestures.
                return False
            if busy or self.phase in ('finishing','cancelling'):return False
            if name=='double-tap':
                self.frame={'mode':'normal','target_turn':s.get('turnId') or None,
                    'question_token':r['token'] if r and r.get('kind')=='question' else None}
                self.session=None;self.phase_changed('starting');return True
            if name in ('swipe-up','swipe-down'):
                if r and r.get('options'):
                    self.emit(('option_move',(r['token'],-1 if name=='swipe-up' else 1)))
                return False
            if name=='tap':
                if r and r.get('options') and r.get('selected',-1)>=0:
                    self.emit(('choice',(r['token'],r['selected'])))
                return False
            return False
    def started(self,session):
        with self.lock:
            self.session=session
            if self.frame is not None and self.phase=='starting':self.phase_changed('recording')
    def finished(self,session,text):
        with self.lock:
            if session in self.retired or self.frame is None:return None
            if self.session is not None and session!=self.session:return None
            self.retired.add(session)
            result=dict(self.frame,text=text)
            self.frame=None;self.session=None;self.phase_changed('idle')
            return result
    def partial(self,session,text):
        with self.lock:
            if session in self.retired or self.frame is None:return None
            if self.session is not None and session!=self.session:return None
            return {'text':text,'question_token':self.frame.get('question_token')}
    def fail(self,session=None):
        with self.lock:
            if session is not None:
                self.retired.add(session)
                if self.session is not None and session!=self.session:return False
            self.frame=None;self.session=None;self.phase_changed('idle')
            return True
