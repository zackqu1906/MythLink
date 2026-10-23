import unittest
from interaction import Interaction
class Gestures(unittest.TestCase):
    def setUp(self):
        self.events=[];self.state={'turnId':'turn-a','request':{'token':'req-a','kind':'permission','selected':1,'options':[{},{}]}}
        self.router=Interaction(self.events.append,lambda:self.state)
    def begin(self):
        self.assertTrue(self.router.gesture('double-tap'))
        self.router.started(10)
    def test_up_down_navigate_and_tap_confirms_without_recording(self):
        self.router.gesture('swipe-up');self.router.gesture('swipe-down');self.router.gesture('tap')
        self.assertEqual(self.events,[('option_move',('req-a',-1)),('option_move',('req-a',1)),('choice',('req-a',1))])
    def test_double_tap_starts_voice_even_with_options(self):
        self.begin();self.assertFalse(any(k=='choice' for k,_ in self.events))
    def test_recording_tap_is_normal_submission_never_approval(self):
        self.begin();self.assertTrue(self.router.gesture('tap',True))
        self.assertEqual(self.router.finished(10,'下一条任务')['mode'],'normal')
        self.assertFalse(any(k=='choice' for k,_ in self.events))
    def test_recording_right_is_steer_not_pause(self):
        self.begin();self.assertTrue(self.router.gesture('swipe-right',True))
        self.assertEqual(self.router.finished(10,'改成20项')['mode'],'steer')
        self.assertFalse(any(k=='pause' for k,_ in self.events))
    def test_finishing_repeated_gesture_cannot_change_latched_route(self):
        self.begin();self.router.gesture('swipe-right',True);self.router.gesture('tap',True)
        self.assertEqual(self.router.finished(10,'修改')['mode'],'steer')
    def test_asr_latency_does_not_retarget_new_task(self):
        self.begin();self.router.gesture('swipe-right',True);self.state['turnId']='turn-b'
        self.assertEqual(self.router.finished(10,'修改')['target_turn'],'turn-a')
    def test_cancel_drops_late_transcript(self):
        self.begin();self.router.gesture('swipe-left',True)
        self.assertIsNone(self.router.finished(10,'不应执行'))
    def test_duplicate_final_is_delivered_once(self):
        self.begin();self.router.gesture('tap',True)
        self.assertIsNotNone(self.router.finished(10,'下一条'))
        self.assertIsNone(self.router.finished(10,'下一条'))
    def test_right_when_not_recording_does_not_control_task(self):
        self.router.gesture('swipe-right');self.assertEqual(self.events,[])
    def test_question_answer_is_bound_to_original_request(self):
        self.state['request']['kind']='question';self.begin();self.router.gesture('tap',True)
        self.state['request']['token']='new-request'
        self.assertEqual(self.router.finished(10,'方案甲')['question_token'],'req-a')
    def test_old_asr_error_does_not_discard_current_recording(self):
        self.begin()
        self.assertFalse(self.router.fail(9))
        self.assertEqual(self.router.phase,'recording')
        self.router.gesture('tap',True)
        self.assertEqual(self.router.finished(10,'仍然有效')['text'],'仍然有效')
if __name__=='__main__':unittest.main()
