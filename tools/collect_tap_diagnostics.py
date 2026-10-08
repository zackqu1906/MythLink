#!/usr/bin/env python3
"""Intent-labeled click/stroke collection, independent of detector success."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QVBoxLayout, QWidget)
from collect_stroke_samples import TraceView
from proximic_ring.stroke_input import StrokeDictionary
from proximic_ring.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk import RingSession
from ring_python_sdk.ble import send_mic_control, send_swipe_stop

class Recorder:
    """Queue writes off inference/BLE threads; label before observing outcomes."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('x', encoding='utf-8')
        self.queue = queue.Queue(maxsize=20000)
        self.lock = threading.Lock()
        self.trial_id = None
        self.phase = 'warmup'
        self.click_style = '自由点击'
        self.dropped = 0
        self.error = None
        self.closed = False
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()

    def _write(self):
        try:
            flushed_at = time.monotonic()
            while True:
                try:
                    row = self.queue.get(timeout=.25)
                except queue.Empty:
                    self.file.flush()
                    flushed_at = time.monotonic()
                    continue
                if row is None:
                    break
                self.file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                # Each trial's annotation and boundaries are durable immediately.
                if (row['kind'] in {'session', 'trial_start', 'annotation', 'trial_end', 'session_end'}
                        or time.monotonic() - flushed_at >= .25):
                    self.file.flush()
                    flushed_at = time.monotonic()
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.file.close()

    def record(self, kind, **value):
        with self.lock:
            if self.closed:
                return
            row = dict(time=datetime.now().astimezone().isoformat(), mono=time.monotonic(),
                       kind=kind, trial_id=self.trial_id, **value)
            row.setdefault('intended', 'click')
            row['phase'] = self.phase
            row['click_style'] = self.click_style
            try:
                self.queue.put_nowait(row)
            except queue.Full:
                self.dropped += 1

    def begin(self, trial_id, task):
        with self.lock:
            self.trial_id = trial_id
        self.record('trial_start', intended=task)

    def end(self, validity, **value):
        self.record('annotation', validity=validity, dropped_records=self.dropped, **value)
        self.record('trial_end')
        with self.lock:
            self.trial_id = None

    def close(self):
        self.record('session_end', dropped_records=self.dropped)
        with self.lock:
            self.closed = True
        # The producer has already stopped; wait for remaining data to reach disk.
        while self.thread.is_alive():
            try:
                self.queue.put(None, timeout=.1)
                break
            except queue.Full:
                pass
        self.thread.join(5)
        if self.thread.is_alive():
            self.error = '保存尚未完成'


def instrument_processor(processor, recorder):
    """Observe this processor instance only; preserve original numerical rules."""
    detector = processor.post.click
    original_movement, original_ready = detector.movement, detector.ready
    original_probability = detector.probability

    def probability(index, p):
        recorder.record('raw_contact_probability', step=index, probability=float(p))
        return original_probability(index, p)

    def movement(index, velocity):
        recorder.record('raw_velocity', step=index, vx=float(velocity[0]), vy=float(velocity[1]))
        return original_movement(index, velocity)

    def ready():
        verdicts = []
        for start, end in detector.pending:
            if detector.last_move is None or detector.last_move < end - 1:
                break
            missing = any(k not in detector.raw for k in range(start, end))
            net = None if missing else math.hypot(
                sum(detector.raw[k][0] for k in range(start, end)) * .005,
                sum(detector.raw[k][1] for k in range(start, end)) * .005)
            reason = ('duration' if not 0 < end-start < 40 else 'missing_frames' if missing
                      else 'click' if net <= .02 else 'movement')
            verdicts.append(dict(start_step=start, end_step=end,
                                 duration_ms=(end-start)*5, raw_net_displacement=net, reason=reason))
        result = original_ready()
        for verdict in verdicts:
            recorder.record('sdk_click_verdict', **verdict)
        return result

    detector.movement, detector.ready, detector.probability = movement, ready, probability
    original_poll = processor.poll

    def poll(*args, **kwargs):
        result = original_poll(*args, **kwargs)
        if result:
            recorder.record('sdk_batch', events=[asdict(event) for event in result])
        return result

    processor.poll = poll


class Worker(QThread):
    status = Signal(str)
    ready = Signal()
    observed = Signal(object)
    trace = Signal(object)

    def __init__(self, device, recorder, gestures=True):
        super().__init__()
        self.device, self.recorder, self.gestures = device, recorder, gestures
        self.stopping = threading.Event()

    def emit_observation(self, kind, **value):
        self.recorder.record(kind, **value)
        self.observed.emit(dict(kind=kind, **value))

    def run(self):
        try:
            asyncio.run(self.stream())
        except Exception as exc:
            self.emit_observation('stream_error', error=str(exc))
            self.status.emit('连接或采集失败：' + str(exc))

    async def stream(self):
        dictionary = StrokeDictionary(recognizer='dtw')
        dictionary.recognize([(0., 0.), (1., 0.)])
        self.recorder.record('recognizer', name='dtw', profile=dictionary._dtw.profile)
        session = RingSession(self.device, 10.)
        session.battery_poll_enabled = False
        session.auto_reconnect = False
        ended = threading.Event()

        def stroke(points):
            result = dictionary.recognize(points)
            self.emit_observation('stroke', points=points, result=result)

        output = TouchpadStrokeOutput(connection=ended, on_ready=lambda: None,
            on_end=lambda reason, error: ended.set(), on_stroke=stroke,
            on_tap=lambda: self.emit_observation('output_tap'),
            on_trace=lambda points, finished: self.trace.emit(points))

        def submit(event):
            if event.kind != 'move':
                self.emit_observation('sdk_final_verdict' if event.kind == 'click_verdict' else 'sdk_' + event.kind,
                                      event=asdict(event))
            output.submit(event)

        def firmware(event):
            self.emit_observation('firmware_gesture', event=asdict(event))

        output.start()
        output.activate(None)
        try:
            self.status.emit('正在连接戒指…')
            if not await session.connect_target(self.device):
                raise RuntimeError('未找到戒指，请确认主程序已断开、名称正确')
            if self.stopping.is_set():
                return
            self.recorder.record('device', name=session.target_name, address=session.target_address)
            await send_mic_control(session.client, session.rx_uuid, on=False)
            await send_swipe_stop(session.client, session.rx_uuid)
            await session.touchpad_on(on_event=submit,
                on_stats=lambda stats: self.emit_observation('stats', stats=asdict(stats)),
                on_stopped=lambda error: ended.set(), duration_s=None)
            stream = session.touchpad
            # Install on the inference executor, between batches. No global patch.
            await asyncio.get_running_loop().run_in_executor(
                stream.executor, instrument_processor, stream.processor, self.recorder)
            original_submit = stream.submit

            def packet(data):
                self.recorder.record('token_packet', hex=bytes(data).hex(), arrival=time.monotonic())
                original_submit(data)

            stream.submit = packet
            if self.gestures:
                await session.swipe_on(on_trigger=firmware, print_events=False,
                                       print_triggers=False, print_profile=False)
            # Keep warmup and connection movements out of labeled trials.
            self.status.emit('已连接，正在预热，请保持手指离桌…')
            while not self.stopping.is_set() and not ended.is_set():
                if stream.stats.warmup_frames >= 200:
                    break
                await asyncio.sleep(.05)
            if not self.stopping.is_set() and not ended.is_set():
                self.status.emit('已就绪 · 按提示测试 · 数据自动保存')
                self.ready.emit()
            while not self.stopping.is_set() and not ended.is_set():
                await asyncio.sleep(.05)
            if ended.is_set() and not self.stopping.is_set():
                raise session.touchpad_error or RuntimeError('数据流已结束')
        finally:
            output.stop()
            try:
                if session.touchpad_active:
                    await session.touchpad_off()
            finally:
                try:
                    await session.disconnect()
                finally:
                    await asyncio.to_thread(output.thread.join, 2.)


class Window(QWidget):
    """An entire connected recording interval has the human intent 'click'."""
    def __init__(self, path, device='Ringo6B72', gestures=True, synthetic=False):
        super().__init__()
        self.setWindowTitle('Ring 持续点击测试')
        self.resize(760, 650)
        self.recorder = Recorder(path)
        self.recorder.record('session', version=2, mode='continuous_clicks',
            synthetic=synthetic, gestures=gestures, python=sys.version,
            source_hashes={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                ['src/ring_python_sdk/touchpad/core.py', 'src/proximic_ring/touchpad_strokes.py']})
        self.gestures = gestures
        self.worker = None
        self.last_error = None
        self.recording = False
        self.outcomes = Counter()
        self.latest_shape = '—'
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('先断开主程序戒指连接。连接后可连续点击；不用按次数完成，也不用逐条确认。'))
        row = QHBoxLayout()
        self.device = QLineEdit(device)
        self.connect_button = QPushButton('连接并开始记录')
        self.connect_button.clicked.connect(self.connect_ring)
        row.addWidget(self.device)
        row.addWidget(self.connect_button)
        layout.addLayout(row)
        self.status = QLabel('等待连接，所有动作的意图都将记录为“点击”。')
        layout.addWidget(self.status)
        self.prompt = QLabel('自由尝试各种点击：轻点、重一点、慢一点、快一点、连续点击。')
        self.prompt.setWordWrap(True)
        self.prompt.setStyleSheet('font-size: 22px; font-weight: bold; padding: 16px; background: #edf3ff;')
        layout.addWidget(self.prompt)
        row = QHBoxLayout()
        self.style = QComboBox()
        self.style.addItems(['自由点击', '正常点击', '轻点击', '较重点击', '慢点击', '快速连续点击', '双击'])
        self.style.currentTextChanged.connect(self.change_style)
        row.addWidget(QLabel('可选标记当前方式：'))
        row.addWidget(self.style)
        layout.addLayout(row)
        self.view = TraceView()
        layout.addWidget(self.view)
        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        self.quality = QLabel()
        self.quality.setWordWrap(True)
        layout.addWidget(self.quality)
        row = QHBoxLayout()
        self.note = QLineEdit()
        self.note.setPlaceholderText('可选备注：例如“现在快速双击”“刚才碰到了桌面”')
        note_button = QPushButton('保存备注')
        note_button.clicked.connect(self.save_note)
        row.addWidget(self.note)
        row.addWidget(note_button)
        layout.addLayout(row)
        self.path_label = QLabel('自动保存：' + str(path))
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        folder_button = QPushButton('打开数据文件夹')
        folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent))))
        layout.addWidget(folder_button)
        self.refresh()

    def refresh(self):
        self.result.setText(f"错误笔画输出：{self.outcomes['stroke']} 次（最近：{self.latest_shape}）\n"
            f"SDK 点击：{self.outcomes['sdk_click']} 次；固件点击：{self.outcomes['firmware_tap']} 次。\n"
            '每个输出都保留；这些事件计数不是实际点击次数或准确率。')
        self.quality.setText(f"接触重置：{self.outcomes['contact_reset']} 次；日志丢失：{self.recorder.dropped} 条。"
            + (' 保存错误：' + self.recorder.error if self.recorder.error else ' 数据持续写入本地文件。'))

    def change_style(self, text):
        with self.recorder.lock:
            self.recorder.click_style = text
        self.recorder.record('style_change', style=text)

    def save_note(self):
        if self.note.text().strip():
            self.recorder.record('note', note=self.note.text())
            self.note.clear()

    def connect_ring(self):
        if self.worker and self.worker.isRunning():
            self.worker.stopping.set()
            self.connect_button.setEnabled(False)
            self.status.setText('正在断开并保存…')
            return
        self.last_error = None
        self.worker = Worker(self.device.text().strip(), self.recorder, self.gestures)
        self.worker.status.connect(self.status.setText)
        self.worker.ready.connect(self.connected)
        self.worker.observed.connect(self.observe)
        self.worker.trace.connect(self.view.show_trace)
        self.worker.finished.connect(self.disconnected)
        self.connect_button.setText('停止记录并断开')
        self.device.setEnabled(False)
        self.worker.start()

    def connected(self):
        self.recording = True
        with self.recorder.lock:
            self.recorder.phase = 'collecting'
        self.recorder.record('collection_start')
        self.status.setText('正在持续记录 · 所有意图均为点击 · 可随时停止')
        self.prompt.setText('现在连续点击即可。被识别成笔画、识别成功或没有输出，都保存原始数据。')

    def disconnected(self):
        if self.recording:
            self.recorder.record('collection_end', outcomes=dict(self.outcomes))
        self.recording = False
        with self.recorder.lock:
            self.recorder.phase = 'idle'
        self.connect_button.setText('重新连接并继续记录')
        self.connect_button.setEnabled(True)
        self.device.setEnabled(True)
        self.status.setText('采集失败：' + self.last_error if self.last_error else
                           '已停止，记录已保留。可以重新连接，或关闭窗口完成保存。')

    def observe(self, row):
        if row['kind'] == 'stream_error':
            self.last_error = row['error']
        if not self.recording:
            return
        kind = row['kind']
        if kind == 'stroke':
            self.outcomes['stroke'] += 1
            self.latest_shape = (row.get('result') or {}).get('shape', '无类别')
        elif kind == 'sdk_click':
            self.outcomes['sdk_click'] += 1
        elif kind == 'firmware_gesture' and row['event'].get('class_id') == 5:
            self.outcomes['firmware_tap'] += 1
        elif kind == 'sdk_contact' and row['event']['state'] == 'reset':
            self.outcomes['contact_reset'] += 1
        elif kind == 'stream_error':
            self.recorder.record('quality_warning', error=row['error'])
        self.refresh()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stopping.set()
            self.status.setText('正在断开并保存，窗口稍后关闭…')
            QTimer.singleShot(200, self.close)
            event.ignore()
            return
        if self.recording:
            self.disconnected()
        self.timer.stop()
        self.recorder.close()
        if self.recorder.error:
            self.path_label.setText('保存失败：' + self.recorder.error)
            event.ignore()
            return
        event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='Ringo6B72')
    parser.add_argument('--no-gestures', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--self-test', type=Path, help='Offline UI smoke test screenshot; no device connection')
    args = parser.parse_args()
    path = args.output or ROOT/'data'/'tap-diagnostics'/(datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.jsonl')
    app = QApplication([])
    # The offscreen Qt plugin lacks Windows font discovery; load a local CJK font
    # for reproducible previews as well as an explicit font in the real window.
    if sys.platform == 'win32':
        import os
        font_path = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/'msyh.ttc'
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont('Microsoft YaHei', 10))
    window = Window(path, args.device, not args.no_gestures, bool(args.self_test))
    window.show()
    if args.self_test:
        def smoke():
            window.connected()
            window.recorder.record('stroke', points=[(0, 0), (4, 0)], result=dict(category='h', shape='横'))
            window.observe(dict(kind='stroke', result=dict(category='h', shape='横')))
            window.view.show_trace([(0., 0.), (4., 0.)])
            args.self_test.parent.mkdir(parents=True, exist_ok=True)
            app.processEvents()
            assert window.grab().save(str(args.self_test))
            assert window.outcomes['stroke'] == 1
            window.close()
            print('PASS: intended click saved despite stroke-only output')
        QTimer.singleShot(100, smoke)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
