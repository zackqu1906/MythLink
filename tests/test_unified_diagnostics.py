import logging
import os
import subprocess
import sys
import threading
import traceback

import pytest

from proximic_ring.diagnostic_log import RotatingDiagnosticLog
from proximic_ring.runtime_diagnostics import DiagnosticStream, RuntimeCapture
from proximic_ring.scene_diagnostics import SceneDiagnostics


def test_console_runtime_scene_and_exceptions_share_one_log(tmp_path):
    path = tmp_path / 'diagnostic.log'
    writer = RotatingDiagnosticLog(path)
    original = sys.stdout, sys.stderr
    capture = RuntimeCapture(writer)
    try:
        print('[startup] ready')
        print('one console event')
        print('native warning', file=sys.stderr)
        logging.getLogger('test-engine').warning('model warning')
        writer.record('[EVENT CONNECTED] device_count=1')
        SceneDiagnostics(path, writer=writer).record('s-test', 'capture', 'recognized',
            native={'scanned_nodes': 369, 'elapsed_ms': 60.4})
        try:
            raise RuntimeError('example failure')
        except RuntimeError:
            traceback.print_exc()
    finally:
        capture.close()
    assert (sys.stdout, sys.stderr) == original
    content = path.read_text()
    for marker in ['[startup] ready', 'one console event', 'native warning', 'model warning',
                   '[EVENT CONNECTED]', '"scanned_nodes":369', 'RuntimeError: example failure']:
        assert content.count(marker) == 1
    assert '[stderr]' in content and '[python.test-engine]' in content and '[scene]' in content
    assert all('run=' in line and 'process=' in line and 'thread=' in line for line in content.splitlines())
    assert sorted(p.name for p in tmp_path.glob('*.log')) == ['diagnostic.log']


def test_partial_stdout_writes_from_threads_do_not_mix(tmp_path):
    writer = RotatingDiagnosticLog(tmp_path / 'diagnostic.log')
    stream = DiagnosticStream(writer, 'stdout')
    barrier = threading.Barrier(4)
    def write(index):
        stream.write(f'worker-{index}:')
        barrier.wait()
        stream.write('complete\n')
    threads = [threading.Thread(target=write, args=(index,)) for index in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=5)
    stream.write('last partial')
    stream.flush()
    content = writer.path.read_text()
    assert len(content.splitlines()) == 5
    for index in range(4): assert content.count(f'worker-{index}:complete') == 1
    assert content.count('last partial') == 1


def test_qt_warnings_and_uncaught_thread_errors_share_log(tmp_path):
    # Exercise the real default threading exception hook and Qt callback in a
    # child so pytest's exception/warning hooks do not mask either producer.
    path = tmp_path / 'diagnostic.log'
    script = '''
import sys, threading
from PySide6.QtCore import qWarning
from proximic_ring.diagnostic_log import RotatingDiagnosticLog
from proximic_ring.runtime_diagnostics import RuntimeCapture
capture = RuntimeCapture(RotatingDiagnosticLog(sys.argv[1]))
capture.install_qt()
qWarning('qt diagnostic evidence')
def fail():
    raise RuntimeError('uncaught background evidence')
thread = threading.Thread(target=fail)
thread.start()
thread.join()
capture.close()
'''
    process = subprocess.run([sys.executable, '-c', script, str(path)],
                             capture_output=True, timeout=15)
    assert process.returncode == 0, process.stderr.decode()
    content = path.read_text()
    assert content.count('qt diagnostic evidence') == 1
    assert '[WARNING] [qt.default]' in content
    assert 'RuntimeError: uncaught background evidence' in content
    assert 'Traceback (most recent call last)' in content


def test_multiple_processes_rotate_without_missing_or_interleaving_records(tmp_path):
    path = tmp_path / 'diagnostic.log'
    script = '''
from proximic_ring.diagnostic_log import RotatingDiagnosticLog
import sys
log = RotatingDiagnosticLog(sys.argv[1], max_bytes=1200, backup_count=40)
for index in range(80):
    assert log.append(sys.argv[2] + ':' + str(index) + ':' + 'x' * 80)
'''
    processes = [subprocess.Popen([sys.executable, '-c', script, str(path), str(index)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for index in range(4)]
    for process in processes:
        _, error = process.communicate(timeout=15)
        assert process.returncode == 0, error.decode()
    writer = RotatingDiagnosticLog(path, max_bytes=1200, backup_count=40)
    export = tmp_path / 'report.log'
    writer.export(export)
    rows = export.read_text().splitlines()
    expected = {f'{worker}:{index}:' + 'x' * 80 for worker in range(4) for index in range(80)}
    assert len(rows) == len(expected) and set(rows) == expected


def test_tail_clear_and_export_include_rotations_in_order(tmp_path):
    writer = RotatingDiagnosticLog(tmp_path / 'diagnostic.log', max_bytes=35, backup_count=5)
    for index in range(8): writer.append(f'事件-{index}')
    writer.append('clear-marker-123')
    writer.append('事件-8')
    assert writer.tail(lines=3).splitlines() == ['事件-7', 'clear-marker-123', '事件-8']
    assert writer.tail(after_marker='clear-marker-123') == '事件-8'
    export = tmp_path / 'export' / 'complete.log'
    writer.export(export)
    content = export.read_text()
    assert content == ''.join(path.read_text() for path in writer.files() if path.exists())
    assert '事件-0' in content and '事件-8' in content
    assert '\ufffd' not in writer.tail(max_bytes=22)
    with pytest.raises(ValueError): writer.export(writer.path)
    with pytest.raises(ValueError): writer.export(writer.path.with_name('diagnostic.log.1'))


def test_crash_descriptor_follows_active_file_after_rotation(tmp_path):
    writer = RotatingDiagnosticLog(tmp_path / 'diagnostic.log', max_bytes=80)
    descriptor = writer.fileno()
    writer.append('first-' + 'a' * 60)
    # A different producer rotates the same log. The saved fd stays valid.
    other = RotatingDiagnosticLog(writer.path, max_bytes=80)
    other.append('second-' + 'b' * 60)
    os.write(descriptor, b'fatal traceback\n')
    assert 'fatal traceback' in writer.path.read_text()
    assert 'fatal traceback' not in writer.path.with_name('diagnostic.log.1').read_text()


def test_capture_fallback_remains_the_path_used_by_controller(monkeypatch, tmp_path):
    import proximic_ring.runtime_diagnostics as runtime
    import proximic_ring.diagnostic_log as diagnostics
    import proximic_ring.runtime_paths as paths
    blocked = tmp_path / 'occupied'
    blocked.write_text('file')
    monkeypatch.setattr(paths, 'app_data_root', lambda: blocked)
    monkeypatch.setattr(runtime.tempfile, 'gettempdir', lambda: str(tmp_path))
    capture = runtime.configure_diagnostics()
    try:
        assert diagnostics.diagnostic_log_path(blocked) == tmp_path / 'Mythlink-diagnostic.log'
        print('fallback works')
    finally:
        capture.close()
    assert 'fallback works' in (tmp_path / 'Mythlink-diagnostic.log').read_text()


def test_native_stderr_is_drained_without_polluting_json_protocol(monkeypatch, tmp_path):
    import proximic_ring.native_access as native
    import proximic_ring.diagnostic_log as diagnostics
    real_popen = subprocess.Popen
    script = '''
import json,sys
print('native startup failed detail', file=sys.stderr, flush=True)
for line in sys.stdin:
    message=json.loads(line)
    print(json.dumps({'id':message['id'], 'result':'ok'}), flush=True)
'''
    monkeypatch.setattr(native.subprocess, 'Popen',
        lambda args, **kwargs: real_popen([sys.executable, '-u', '-c', script], **kwargs))
    path = tmp_path / 'diagnostic.log'
    monkeypatch.setattr(diagnostics, 'diagnostic_log_path', lambda: path)
    channel = native.NativeAccessChannel()
    try:
        assert channel.call('status') == 'ok'
    finally:
        channel.close()
    text = path.read_text()
    assert 'native startup failed detail' in text and '[native.stderr.' in text
    assert '"result"' not in text


def test_controller_view_and_export_show_background_scene_details(tmp_path, monkeypatch):
    from test_interaction_controls import _controller, _close
    from PySide6.QtWidgets import QFileDialog
    controller = _controller(tmp_path, monkeypatch)
    destination = tmp_path / 'report.log'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(destination), ''))
    try:
        controller._append_log('visible event')
        controller._append_background_diagnostic('background event')
        controller.appGestures._diagnostics.record('trace-unified', 'capture', 'recognition_timeout',
                                                   elapsed_ms=301, scanned_nodes=1200)
        text = controller.readDiagnosticLog()
        assert 'visible event' in text and 'background event' in text and 'trace-unified' in text
        assert controller.appGestures._diagnostics.path == controller._diagnostic_log.path
        controller.clearLog()
        assert not controller.readDiagnosticLog()
        controller.exportDiagnosticLog()
        text = destination.read_text()
        assert 'visible event' in text and 'background event' in text and '"scanned_nodes":1200' in text
        assert not (controller._diagnostic_log.path.parent / 'scene-events.jsonl').exists()
    finally:
        _close(controller)
