"""Opt-in contract check against the installed desktop, without opening its UI.

Read the installed protocol table and execute only its pure text-input detector
in an isolated Node VM. No app code is imported, copied into the repo or changed.
"""
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import subprocess

import pytest

from proximic_ring.agent_workflow.agent.attachments import turn_input
from proximic_ring.agent_workflow.agent.codex_ipc import SUPPORTED_BUILDS, VERSIONS
from proximic_ring.agent_workflow.agent.codex_state import DesktopState


pytestmark = pytest.mark.skipif(
    os.environ.get('PROXIMIC_TEST_CODEX_CONTRACT') != '1',
    reason='opt in to inspect the installed macOS Codex bundle')


def bundles(archive):
    with archive.open('rb') as stream:
        sizes = struct.unpack('<4I', stream.read(16))
        header = json.loads(stream.read(sizes[3]))
        base = 8 + sizes[1]
        for folder, prefix in [('webview/assets', 'app-'), ('.vite/build', 'src-')]:
            node = header
            for part in folder.split('/'):
                node = node['files'][part]
            for name, entry in node['files'].items():
                if not name.startswith(prefix) or not name.endswith('.js'):
                    continue
                stream.seek(base + int(entry['offset']))
                yield stream.read(entry['size']).decode()


def test_installed_desktop_protocol_and_real_continuation_detector():
    app = Path('/Applications/ChatGPT.app/Contents')
    build = str(plistlib.loads((app/'Info.plist').read_bytes())['CFBundleVersion'])
    assert build in SUPPORTED_BUILDS, f'Unvalidated desktop build {build}'
    expected = {'thread-stream-state-changed':11, **VERSIONS}
    protocol_found = False
    detector = None
    for source in bundles(app/'Resources/app.asar'):
        table = re.search(r'\{"thread-stream-state-changed":11[^}]+\}', source)
        if table:
            versions = json.loads(table.group())
            assert {key:versions.get(key) for key in expected} == expected
            protocol_found = True
        match = re.search(
            r'function (\w+)\(e\)\{return e\?\.some\(e=>e\.type===`text`'
            r'&&e\.text_elements\.some\(\(\{placeholder:e\}\)=>e===`aeon-continuation`'
            r'\)\)===!0\}', source)
        if match:
            detector = match.group()
    assert protocol_found, 'Desktop protocol table changed; revalidate the bridge'
    assert detector, 'Desktop text detector changed; revalidate the render contract'

    original = turn_input('这个文件', [
        dict(kind='image', path='/test/window.png', automatic=True, context={}),
        dict(kind='file', name='report.pdf', path='/test/report.pdf')])
    model = DesktopState(dict(thread=dict(id='test', cwd='/test')))
    model.prepare_turn(dict(input=original))
    turn = model.turn('turn')
    model.observe(dict(method='item/started', params=dict(threadId='test', turnId='turn',
        item=dict(id='user', type='userMessage', content=original))))
    fixture = dict(detector=detector, original=original, params=turn['params']['input'],
                   content=turn['items'][0]['content'])
    script = """
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const f = JSON.parse(fs.readFileSync(0, 'utf8'));
const detect = vm.runInNewContext('(' + f.detector + ')', {}, {timeout: 1000});
// Reproduce the reported error with the actual installed renderer function.
assert.throws(() => detect(f.original), /Cannot read properties of undefined.*some/);
assert.equal(detect(f.params), false);
assert.equal(detect(f.content), false);
assert.equal(detect([{type:'text', text:'x',
    text_elements:[{placeholder:'aeon-continuation'}]}]), true);
process.stdout.write('original crash reproduced; normalized desktop inputs passed');
"""
    result = subprocess.run([str(app/'Resources/cua_node/bin/node'), '-e', script],
                            input=json.dumps(fixture), text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'normalized desktop inputs passed' in result.stdout
