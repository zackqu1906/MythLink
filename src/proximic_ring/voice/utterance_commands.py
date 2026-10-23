"""UI 到音频生产线程的显式句子命令，不伪造固件手势。

每条命令有连接寿命、时间戳和 GUI 端的输入所有权验证。准备输入法的 ACK
仍走原来的 RecognitionRuntime/ASR 控制器；UI 从不直接操作音频会话。
"""
import time
from queue import SimpleQueue,Empty

class UtteranceCommands:
    def __init__(self,connection):self.connection=connection;self.queue=SimpleQueue()
    def request(self,kind,prepare,done):
        if self.connection.is_set():return False
        self.queue.put((time.monotonic(),kind,prepare,done));return True
    def drain(self,controller,*,allowed):
        while True:
            try:stamp,kind,prepare,done=self.queue.get_nowait()
            except Empty:return
            action=None
            if allowed and not self.connection.is_set() and time.monotonic()-stamp<1:
                action=controller.request_gesture_toggle(prepare,allow_start=kind=='start',allow_end=kind=='end',explicit=True)
            done(action or 'unavailable')
