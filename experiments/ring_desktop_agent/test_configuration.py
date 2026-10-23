import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import flow
from launcher import parser


class Locations(unittest.TestCase):
    def test_launcher_does_not_choose_a_task_output_directory(self):
        self.assertIsNone(parser().parse_args([]).output_dir)

    def test_setup_keeps_optional_save_location_separate_from_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            output = base/'user-selected-location'
            with patch.object(flow, 'ROOT', base/'runtime'), patch.object(flow, 'RPC') as rpc:
                rpc.return_value.call.side_effect = lambda method, *_: {
                    'account/read': {'requiresOpenaiAuth': True, 'account': {'type': 'chatgpt'}},
                    'thread/start': {'thread': {'id': 'thread'}},
                    'mcpServerStatus/list': {'data': [{'name': 'cua_repl', 'tools': {'js': {}, 'turn_ended': {}, 'js_reset': {}}}]}
                }[method]
                app = flow.Flow(output_dir=output, codex_path='/unused', with_ring=False)
                try:
                    app.setup()
                    self.assertFalse(output.exists())
                    self.assertEqual(app.state['outputDirectory'], str(output))
                    params = next(call.args[1] for call in rpc.return_value.call.call_args_list
                                  if call.args[0] == 'thread/start')
                    self.assertEqual(params['cwd'], str(base/'runtime'))
                    self.assertNotIn('sandbox', params)
                    self.assertNotIn('sandboxPolicy', params)
                    self.assertIn(str(output), params['developerInstructions'])
                finally:
                    app.events.close()


if __name__ == '__main__':
    unittest.main()
