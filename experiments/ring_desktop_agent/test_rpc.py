import queue
import unittest
from rpc import RPC


class SetupEvents(unittest.TestCase):
    def test_setup_retains_server_requests_and_notification_order(self):
        rpc = RPC.__new__(RPC)
        rpc.inbox = queue.Queue()
        rpc.send = lambda *args: 1
        events = [{'method': 'notification', 'params': {}},
                  {'id': 1, 'method': 'serverRequest', 'params': {}},
                  {'id': 1, 'result': {'ready': True}},
                  {'method': 'after_response'}]
        for event in events:
            rpc.inbox.put(event)
        self.assertEqual(rpc.call('initialize'), {'ready': True})
        self.assertEqual([rpc.inbox.get_nowait() for _ in range(3)],
                         [events[0], events[1], events[3]])

    def test_timeout_preserves_notifications(self):
        rpc = RPC.__new__(RPC)
        rpc.inbox = queue.Queue()
        rpc.send = lambda *args: 1
        rpc.inbox.put({'method': 'notification'})
        with self.assertRaises(TimeoutError):
            rpc.call('initialize', timeout=0.01)
        self.assertEqual(rpc.inbox.get_nowait(), {'method': 'notification'})


if __name__ == '__main__':
    unittest.main()
