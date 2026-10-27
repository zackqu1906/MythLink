#!/usr/bin/env python3
"""Collect labeled Ring strokes using the established sensing pipeline."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import QPointF, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QVBoxLayout, QWidget, QCheckBox, QSlider)
from proximic_ring.touchpad.stroke.stroke_input import StrokeCollector, StrokeDictionary, SYMBOLS
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence, FuzzyPolicy, retune_evidence, ADAPTIVE_SETTINGS
from proximic_ring.touchpad.stroke.stroke_examples import stroke_example
from proximic_ring.touchpad.stroke.stroke_display import fixed_scale_trace, TRIAL_TRACE_SCALE
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
import math
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk import RingSession
from ring_python_sdk.ble import send_mic_control, send_swipe_stop
from ring_python_sdk.touchpad import TouchpadMove, TouchpadContact, TouchpadProcessor


class TraceMonitor:
    """Observe contact diagnostics; the verdict-aware output owns display ink."""
    def __init__(self, collector, samples_changed, reset_changed):
        self.collector = collector
        self.samples_changed = samples_changed
        self.reset_changed = reset_changed
        self.samples = []
        self.last_samples = 0.

    @property
    def points(self):
        return self.collector.points

    def __getattr__(self, name):
        return getattr(self.collector, name)

    def reset(self):
        self.collector.reset()
        self.samples = []
        self.reset_changed()

    def feed(self, event):
        completed = self.collector.feed(event)
        now = time.monotonic()
        snapshot = getattr(event, 'contact_probability', None)
        self.samples.append({'step': event.step, 'timestamp': event.timestamp,
                             'kind': event.kind, 'state': getattr(event, 'state', None),
                             'sampled_at': now, 'dx': getattr(event, 'dx', 0.), 'dy': getattr(event, 'dy', 0.),
                             'contact': snapshot,
                             'stroke_contact': snapshot,
                             'drawing': self.collector.touching, 'points': len(self.points),
                             'path_length': self.collector.length,
                             'released': completed is not None})
        if event.kind == 'contact' or completed is not None or now - self.last_samples >= .1:
            self.samples_changed(self.samples)
            self.samples = []
            self.last_samples = now
        return completed


class RingWorker(QThread):
    status = Signal(str)
    stats = Signal(object)
    trace = Signal(object)
    stroke = Signal(object)
    tap = Signal()
    gesture = Signal(object)
    sdk_click = Signal(object)
    contact_samples = Signal(object)
    contact_reset = Signal()

    def __init__(self, device, dictionary, synthetic=False, gestures=True):
        super().__init__()
        self.device, self.dictionary, self.synthetic = device, dictionary, synthetic
        self.gestures = gestures
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()

    def run(self):
        try:
            if self.synthetic:
                self.run_synthetic()
            else:
                asyncio.run(self.stream())
        except Exception as exc:
            self.status.emit(f'连接或采集失败：{exc}')

    def create_output(self, ended):
        output = TouchpadStrokeOutput(
            connection=ended, on_ready=lambda: None,
            on_end=lambda reason, error: ended.set(),
            on_stroke=self.recognize, on_tap=self.tap.emit,
            on_stroke_details=lambda value: self.recognize(value['points'], value.get('kinematics')),
            on_trace=lambda points, finished: self.trace.emit(points))
        self.configure_output(output)
        output.collector = TraceMonitor(output.collector, self.contact_samples.emit,
                                        self.contact_reset.emit)
        output.start()
        output.activate()
        return output

    def submit_event(self, output, event):
        if event.kind == 'click':
            self.sdk_click.emit({'step': event.step, 'timestamp': event.timestamp})
        output.submit(event)

    def run_synthetic(self):
        import numpy as np
        import struct

        class Backbone:
            def __init__(self):
                self.frames = 0

            def step(self, token):
                self.frames += 1
                return np.array([[.4, 0., math.log(99 if 225 <= self.frames+offset-2 <= 245 else 1/99)]
                                 for offset in range(5)])

        ended = threading.Event()
        output = self.create_output(ended)
        try:
            processor = TouchpadProcessor(backbone=Backbone())
            self.configure_processor(processor)
            base = time.monotonic()
            for sequence in range(28):
                if self.stop_requested.is_set() or ended.is_set():
                    break
                arrival = base+sequence*.05
                packet = b'\x21\x05'+struct.pack('<HBIB', sequence, 10, sequence*50, 1)+struct.pack('<60e', *([0.]*60))
                processor.feed(packet, arrival=arrival, now=arrival)
                for event in processor.poll(arrival):
                    self.submit_event(output, event)
            while not self.stop_requested.is_set() and not ended.is_set():
                self.stop_requested.wait(.05)
        finally:
            output.stop()
            output.thread.join(2.)
            self.status.emit('已断开，可再次连接测试')

    async def stream(self):
        session = RingSession(self.device, 10.)
        session.battery_poll_enabled = False
        session.auto_reconnect = False
        ended = threading.Event()
        output = self.create_output(ended)

        def submit(event):
            self.submit_event(output, event)

        try:
            self.status.emit('正在连接戒指…请先断开主程序的戒指连接')
            if not await session.connect_target(self.device):
                raise RuntimeError('未找到戒指或连接失败')
            if self.stop_requested.is_set():
                return
            # Explicitly send OFF so previous clients cannot leave these channels on.
            await send_mic_control(session.client, session.rx_uuid, on=False)
            await send_swipe_stop(session.client, session.rx_uuid)
            await session.touchpad_on(on_event=submit, on_stats=self.stats.emit,
                                     on_stopped=lambda error: ended.set(), duration_s=None)
            await self.configure_touchpad(session.touchpad)
            if self.gestures:
                await session.swipe_on(on_trigger=self.gesture.emit, print_events=False,
                                       print_triggers=False, print_profile=False)
            self.status.emit('已连接 · 麦克风关闭 · 四向手势' + ('开启' if self.gestures else '关闭'))
            while not self.stop_requested.is_set() and not ended.is_set():
                await asyncio.sleep(.05)
            if ended.is_set() and not self.stop_requested.is_set():
                raise session.touchpad_error or RuntimeError('数据流已结束')
        finally:
            output.stop()
            try:
                if session.touchpad_active:
                    await session.touchpad_off()
            finally:
                await session.disconnect()
                await asyncio.to_thread(output.thread.join, 2.)
                self.status.emit('已断开，可再次连接测试')

    def configure_output(self, output):
        """Optional trial-specific arbitration; ordinary collection stays unchanged."""

    def configure_processor(self, processor):
        """Optional instance-only diagnostics; invoked on the inference thread."""

    async def configure_touchpad(self, stream):
        await asyncio.get_running_loop().run_in_executor(
            stream.executor, self.configure_processor, stream.processor)

    def recognize(self, points, kinematics=None):
        started = time.monotonic()
        result = self.dictionary.recognize(points)
        if result is not None:
            self.stroke.emit({'result': result, 'points': points, 'kinematics': kinematics,
                              'recognition_ms': (time.monotonic() - started) * 1000})


class TraceView(QWidget):
    def __init__(self):
        super().__init__()
        self.points = []
        self.overlay = False
        self.setMinimumHeight(180)

    def set_overlay_mode(self, enabled):
        self.overlay = enabled
        self.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)
        self.setAttribute(Qt.WA_NoSystemBackground, enabled)
        self.update()

    def show_trace(self, points):
        self.points = points
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.overlay:
            painter.fillRect(self.rect(), QColor('#f4f6fa'))
        if len(self.points) < 2:
            if not self.overlay:
                painter.drawText(self.rect(), Qt.AlignCenter, '书写轨迹会显示在这里')
            return
        path = [QPointF(x, y) for x, y in fixed_scale_trace(self.points, self.width(), self.height())]
        if not path:
            return
        painter.setOpacity(.42 if self.overlay else 1.)
        painter.setPen(QPen(QColor('#234cdb'), 3))
        for a, b in zip(path, path[1:]):
            painter.drawLine(a, b)
        painter.setBrush(QColor('#21a675'))
        painter.drawEllipse(path[0], 5, 5)


class Window(QWidget):
    trace_view_type = TraceView

    def __init__(self, device, synthetic=False, gestures=True, collect=False, new_collection=False,
                 recognizer='structure', allowed_characters=None):
        super().__init__()
        self.setWindowTitle('Ring 独立笔画测试')
        self.resize(780, 560)
        self.allowed_characters = allowed_characters
        self.dictionary = StrokeDictionary(recognizer=recognizer)
        self.dictionary.recognize([(0, 0), (1, 0)])  # Preload before hardware connection and timing.
        self.recognizer = recognizer
        self.template_profile = self.dictionary._dtw.profile if recognizer == 'dtw' else 'none'
        if recognizer == 'dtw':
            self.setWindowTitle('Ring DTW 32点人工复测')
        self.code, self.selected = '', 0
        self.evidence = []
        self.fuzzy_policy = FuzzyPolicy()
        self.predictor = StrokeContext()
        self.worker = None
        self.closing = False
        self.synthetic = synthetic
        self.gestures = gestures
        log_dir = ROOT / 'data' / 'stroke-test'
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / (datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.jsonl')
        self.log = self.log_path.open('w', encoding='utf-8')
        self.record('test_session', synthetic=synthetic,
                    contact_mode='sdk_confirmed', gestures=gestures,
                    trace_display=dict(mode='fixed-scale', pixels_per_sdk_unit=TRIAL_TRACE_SCALE))
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.device = QLineEdit(device)
        self.device.setPlaceholderText('戒指名称或蓝牙地址')
        self.connect_button = QPushButton('连接并开始')
        self.connect_button.clicked.connect(self.toggle)
        top.addWidget(self.device); top.addWidget(self.connect_button)
        layout.addLayout(top)
        self.status = QLabel('先关闭主程序的戒指连接，再开始测试')
        layout.addWidget(self.status)
        self.editor = QLineEdit()
        self.editor.setPlaceholderText('确认的文字会输入这里，也可以直接打字')
        self.editor.setMinimumHeight(48)
        layout.addWidget(self.editor)
        self.composition = QLabel('等待落笔')
        layout.addWidget(self.composition)
        row = QHBoxLayout()
        self.buttons = []
        for i in range(5):
            button = QPushButton()
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, index=i: self.commit(self.selected // 5 * 5 + index))
            self.buttons.append(button); row.addWidget(button)
        layout.addLayout(row)
        self.trace_view = self.trace_view_type()
        layout.addWidget(self.trace_view)
        self.result = QLabel('绿色圆点是起笔位置；固定比例显示，保留笔画长短')
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        controls = QHBoxLayout()
        for title, action in [('退一笔', self.undo), ('清空笔画', self.clear)]:
            button = QPushButton(title); button.clicked.connect(action); controls.addWidget(button)
        self.fuzzy_toggle = QCheckBox('模糊匹配')
        self.fuzzy_toggle.setChecked(True)
        self.fuzzy_toggle.toggled.connect(self.refresh)
        controls.addWidget(self.fuzzy_toggle)
        layout.addLayout(controls)
        fuzzy_controls = QHBoxLayout()
        self.fuzzy_strength_label = QLabel('模糊程度：50（标准）')
        self.fuzzy_strength_label.setMinimumWidth(150)
        fuzzy_controls.addWidget(self.fuzzy_strength_label)
        fuzzy_controls.addWidget(QLabel('低'))
        self.fuzzy_strength_slider = QSlider(Qt.Horizontal)
        self.fuzzy_strength_slider.setObjectName('fuzzyStrengthSlider')
        self.fuzzy_strength_slider.setRange(0, 100)
        self.fuzzy_strength_slider.setValue(50)
        self.fuzzy_strength_slider.setFocusPolicy(Qt.NoFocus)
        self.fuzzy_strength_slider.setMinimumWidth(140)
        self.fuzzy_strength_slider.setAccessibleName('模糊程度')
        self.fuzzy_strength_slider.setToolTip('立即重算当前候选；50为默认。关闭模糊匹配可使用严格模式。')
        self.fuzzy_strength_slider.valueChanged.connect(self.set_fuzzy_strength)
        fuzzy_controls.addWidget(self.fuzzy_strength_slider, 1)
        fuzzy_controls.addWidget(QLabel('高'))
        self.fuzzy_strength_reset = QPushButton('恢复默认')
        self.fuzzy_strength_reset.setFocusPolicy(Qt.NoFocus)
        self.fuzzy_strength_reset.clicked.connect(lambda: self.fuzzy_strength_slider.setValue(50))
        fuzzy_controls.addWidget(self.fuzzy_strength_reset)
        self.fuzzy_toggle.toggled.connect(self.fuzzy_strength_slider.setEnabled)
        self.fuzzy_toggle.toggled.connect(self.fuzzy_strength_reset.setEnabled)
        layout.addLayout(fuzzy_controls)
        self.adaptive_status = QLabel('短／快笔自动增加容错 · 单笔最高增加30档')
        self.adaptive_status.setObjectName('adaptiveFuzzyStatus')
        self.adaptive_status.setWordWrap(True)
        layout.addWidget(self.adaptive_status)
        self.record('fuzzy_settings', policy=self.fuzzy_policy.snapshot(), adaptive_policy=ADAPTIVE_SETTINGS,
                    reason='initial')
        self.editor.cursorPositionChanged.connect(self.refresh)
        self.editor.selectionChanged.connect(self.refresh)
        self.metrics = QLabel('等待数据')
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        self.contact_state = QLabel('接触状态：等待数据')
        layout.addWidget(self.contact_state)
        layout.addWidget(QLabel('分笔：原 SDK 检测确认的落下／抬起帧边界'))
        self.contact_watchdog = QTimer(self)
        self.contact_watchdog.timeout.connect(self.check_contact_freshness)
        self.contact_watchdog.start(100)
        contact_controls = QHBoxLayout()
        self.contact_test = QPushButton('开始接触／抬起检测')
        self.contact_test.clicked.connect(self.start_contact_test)
        contact_controls.addWidget(self.contact_test)
        for title, state in [('标记：手指已接触桌面', True), ('标记：手指已抬起', False)]:
            button = QPushButton(title)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, actual=state: self.mark_contact(actual))
            contact_controls.addWidget(button)
        layout.addLayout(contact_controls)
        self.contact_prompt = QLabel('可手动标记实际接触和抬起时刻，用于对照模型判定。')
        self.contact_prompt.setWordWrap(True)
        layout.addWidget(self.contact_prompt)
        self.phase_timer = QTimer(self)
        self.phase_timer.timeout.connect(self.update_contact_test)
        layout.addWidget(QLabel('左右滑选字 · 轻触确认 · 上滑退笔 · 下滑清空' if gestures
                               else '轻触确认候选；四向手势已关闭。'))
        self.refresh()
        self.collection = None
        if collect:
            self.setup_collection(layout, new_collection)

    def setup_collection(self, layout, new_session):
        from stroke_collection_plan import make_plan
        directory = ROOT / 'data' / 'stroke-collection'
        directory.mkdir(parents=True, exist_ok=True)
        rows = []
        path = None
        if not new_session and not self.synthetic:
            for previous in sorted(directory.glob('*.jsonl'), reverse=True):
                saved = [json.loads(line) for line in previous.read_text(encoding='utf-8').splitlines() if line.strip()]
                if (saved and saved[0]['kind'] == 'session' and not saved[0].get('synthetic')
                        and saved[0].get('version') == 3
                        and saved[0].get('recognizer', 'structure') == self.recognizer
                        and saved[0].get('template_profile', 'none') == self.template_profile):
                    if sum(row['kind'] == 'annotation' for row in saved) < len(saved[0]['plan']):
                        path, rows = previous, saved
                    break
        plan = rows[0]['plan'] if rows else make_plan()
        annotations = [row for row in rows if row['kind'] == 'annotation']
        path = path or directory / (datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.jsonl')
        self.collection = dict(path=path, log=path.open('a', encoding='utf-8'), plan=plan,
                               index=len(annotations), pending=None,
                               counts=Counter(row['rating'] for row in annotations))
        self.collection_record('resume' if rows else 'session', version=3, plan=plan,
                               sensing_baseline='orientation_fix_20261002',
                               synthetic=self.synthetic, source='original_stroke_test',
                               contact_mode='sdk_confirmed', gestures=self.gestures,
                               recognizer=self.recognizer, template_profile=self.template_profile,
                               dtw_parameters=dict(points=32, radius=6, direction_weight=.15) if self.recognizer == 'dtw' else None)
        self.collection_progress = QLabel()
        self.collection_target = QLabel()
        self.collection_target.setStyleSheet('font-size: 20px; font-weight: bold;')
        self.collection_target.setWordWrap(True)
        layout.insertWidget(2, self.collection_progress)
        layout.insertWidget(3, self.collection_target)
        rating_row = QHBoxLayout()
        self.collection_buttons = []
        self.collection_shortcuts = []
        for number, title in [(1, '1 正确'), (2, '2 错误'), (3, '3 模糊')]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, value=number: self.rate_collection(value))
            rating_row.addWidget(button)
            self.collection_buttons.append(button)
            shortcut = QShortcut(QKeySequence(str(number)), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(lambda value=number: self.rate_collection(value))
            self.collection_shortcuts.append(shortcut)
        retry = QPushButton('重写这一笔')
        retry.clicked.connect(self.retry_collection)
        rating_row.addWidget(retry)
        layout.insertLayout(6, rating_row)
        # Keep the original handwriting display; hide unrelated contact-test controls.
        self.editor.hide()
        self.contact_test.hide()
        self.contact_prompt.hide()
        for i in range(layout.count()):
            row = layout.itemAt(i).layout()
            if row:
                for j in range(row.count()):
                    widget = row.itemAt(j).widget()
                    if isinstance(widget, QPushButton) and widget.text().startswith('标记：'):
                        widget.hide()
        self.setWindowTitle('Ring 笔画样本采集')
        self.resize(850, 720)
        self.update_collection()

    def collection_record(self, kind, **values):
        state = self.collection
        state['log'].write(json.dumps(dict(kind=kind, time=datetime.now().isoformat(),
                                          monotonic=time.monotonic(), **values), ensure_ascii=False) + '\n')
        state['log'].flush()

    def update_collection(self):
        state = self.collection
        index, plan = state['index'], state['plan']
        counts = state['counts']
        self.collection_progress.setText(f'已标记 {index}/{len(plan)} · 正确 {counts[1]} / 错误 {counts[2]} / 模糊 {counts[3]}')
        if index < len(plan):
            trial = plan[index]
            rest = '上一轮已完成，可以休息；准备好直接写下一笔。\n' if index and index % 19 == 0 else ''
            self.collection_target.setText(rest + f"第 {trial['round']}/10 轮：{trial['variant']}\n{trial['hint']}\n"
                                           + stroke_example(trial['shape']))
        else:
            self.collection_target.setText('190 笔全部完成，数据已保存。')
        for button in self.collection_buttons:
            button.setEnabled(state['pending'] is not None)
        for shortcut in self.collection_shortcuts:
            shortcut.setEnabled(state['pending'] is not None)

    def rate_collection(self, rating):
        state = self.collection
        if state is None or state['pending'] is None:
            return
        self.collection_record('annotation', trial_index=state['index'], rating=rating,
                               target=state['plan'][state['index']], value=state['pending'])
        state['counts'][rating] += 1
        state['index'] += 1
        state['pending'] = None
        self.clear()
        self.update_collection()

    def retry_collection(self):
        if self.collection['pending'] is not None:
            self.collection_record('retry', trial_index=self.collection['index'],
                                   value=self.collection['pending'])
        self.collection['pending'] = None
        self.clear()
        self.update_collection()

    def record(self, kind, **values):
        self.log.write(json.dumps({'time': datetime.now().isoformat(), 'kind': kind, **values},
                                  ensure_ascii=False) + '\n')
        self.log.flush()

    def toggle(self):
        if self.worker is not None:
            self.connect_button.setEnabled(False)
            self.status.setText('正在停止并断开…')
            self.worker.stop()
            return
        self.worker = RingWorker(self.device.text().strip(), self.dictionary, self.synthetic, self.gestures)
        self.warmup = 0
        self.last_contact_at = time.monotonic()
        self.worker.status.connect(self.status.setText)
        self.worker.trace.connect(self.trace_view.show_trace)
        self.worker.stroke.connect(self.accept_stroke)
        self.worker.tap.connect(self.accept_sdk_tap)
        self.worker.gesture.connect(self.accept_gesture)
        self.worker.sdk_click.connect(lambda value: self.record('sdk_click', **value))
        self.worker.stats.connect(self.show_stats)
        self.worker.contact_samples.connect(self.show_contact)
        self.worker.contact_reset.connect(self.reset_contact_display)
        self.worker.finished.connect(self.disconnected)
        self.device.setEnabled(False)
        self.connect_button.setText('停止并断开')
        self.worker.start()

    def disconnected(self):
        self.phase_timer.stop()
        self.contact_test.setEnabled(True)
        self.worker.deleteLater()
        self.worker = None
        self.contact_state.setText('接触状态：已停止采集')
        self.device.setEnabled(True)
        self.connect_button.setEnabled(True)
        self.connect_button.setText('连接并开始')
        if self.closing:
            self.close()

    def accept_stroke(self, value):
        self.code += value['result']['category']
        self.evidence.append(stroke_evidence(value['result']['category'], value['result'],
                                            policy=self.fuzzy_policy, metrics=value.get('kinematics')))
        from proximic_ring.touchpad.stroke.stroke_personal import capture_ink
        ink = capture_ink(value['points'])
        if ink is not None:
            self.evidence[-1]['ink'] = ink
        self.update_adaptive_status()
        self.selected = 0
        self.refresh()
        r = value['result']
        self.result.setText(f"识别：{r['shape']} · 轨迹 {len(value['points'])} 点 · "
                            f"模板距离 {r['distance']:.5f} · 识别计算 {value['recognition_ms']:.1f} ms\n"
                            + stroke_example(r['shape']))
        if r.get('recognizer') == 'dtw_v1':
            alternatives = r['category_alternatives']
            self.result.setText(self.result.text()+ '\n编码备选：'+ ' / '.join(
                f"{item['shape']}（{SYMBOLS[item['category']]}）" for item in alternatives))
        self.record('stroke', **value, code=self.code, fuzzy_matching=self.fuzzy_toggle.isChecked(),
                    fuzzy_candidates=self.matches[:5], fuzzy_policy=self.fuzzy_policy.snapshot(),
                    adaptation=self.evidence[-1].get('adaptation'),
                    effective_fuzzy_policy=self.evidence[-1].get('effective_policy', self.fuzzy_policy.snapshot()))
        adjustment = self.evidence[-1].get('adaptation')
        if adjustment and adjustment['applied'] and hasattr(self, 'trial_event'):
            metrics = self.evidence[-1]['kinematics']
            self.trial_event('fuzzy_adaptation', stroke_index=len(self.evidence)-1,
                base_level=adjustment['baseLevel'], effective_level=adjustment['effectiveLevel'],
                boost=adjustment['boost'], path_length=metrics['length'],
                duration_ms=metrics['duration_ms'], speed=metrics['speed'],
                fuzzy_matching=self.fuzzy_toggle.isChecked(), policy=self.evidence[-1]['effective_policy'])
        if self.collection is not None and self.collection['index'] < len(self.collection['plan']):
            state = self.collection
            if state['pending'] is not None:
                self.collection_record('replaced_unrated_stroke', trial_index=state['index'], value=state['pending'])
            state['pending'] = value
            self.collection_record('capture', trial_index=state['index'], target=state['plan'][state['index']], value=value)
            self.update_collection()

    def set_fuzzy_strength(self, level):
        policy = FuzzyPolicy(max(0, min(100, int(level))))
        if policy == self.fuzzy_policy:
            return
        self.fuzzy_policy = policy
        self.evidence = retune_evidence(self.evidence, policy)
        self.update_adaptive_status()
        if self.fuzzy_strength_slider.value() != policy.level:
            self.fuzzy_strength_slider.setValue(policy.level)
        label = '标准' if policy.level == 50 else '低' if policy.level < 25 else '高' if policy.level >= 75 else '中'
        self.fuzzy_strength_label.setText(f'模糊程度：{policy.level}（{label}）')
        self.fuzzy_strength_slider.setToolTip('立即重算当前候选；50为默认。关闭模糊匹配可使用严格模式。')
        self.selected = 0
        self.refresh()
        self.record('fuzzy_settings', policy=policy.snapshot(), adaptive_policy=ADAPTIVE_SETTINGS,
                    adaptations=[stroke.get('adaptation') for stroke in self.evidence],
                    code=self.code, reason='slider')
        self.log.flush()
        if hasattr(self, 'trial_event'):
            self.trial_event('fuzzy_settings', level=policy.level, policy=policy.snapshot())

    def update_adaptive_status(self):
        stroke = self.evidence[-1] if self.evidence else {}
        adjustment = stroke.get('adaptation', {})
        if not self.fuzzy_toggle.isChecked():
            self.adaptive_status.setText('严格匹配 · 自动容错已关闭')
        elif adjustment.get('applied'):
            metrics = stroke['kinematics']
            self.adaptive_status.setText(
                f"本笔模糊：{adjustment['baseLevel']} → {adjustment['effectiveLevel']} "
                f"（+{adjustment['boost']}）\n"
                f"长度 {metrics['length']:.1f} · {metrics['duration_ms']:.0f} ms · "
                f"速度 {metrics['speed']:.0f}/s")
        else:
            self.adaptive_status.setText('短／快笔自动增加容错 · 单笔最高增加30档')
        self.adaptive_status.setToolTip('长度和速度使用SDK原始位移单位；时长来自接触采样帧。缺少时长时使用滑块基准。')

    def refresh(self):
        self.update_adaptive_status()
        self.matches = self.dictionary.fuzzy_candidates(self.evidence, predictor=self.predictor,
            context=self.editor.text()[:self.editor.cursorPosition()] if not self.editor.hasSelectedText() else '',
            allowed_characters=self.allowed_characters, policy=self.fuzzy_policy) \
            if self.fuzzy_toggle.isChecked() else []
        self.candidates = [item['char'] for item in self.matches] if self.fuzzy_toggle.isChecked() \
            else self.dictionary.candidates(self.code, 30, allowed_characters=self.allowed_characters)
        self.selected = max(0, min(self.selected, len(self.candidates) - 1))
        offset = self.selected // 5 * 5
        corrected = {index for item in self.matches[:5] for index in item['corrections']}
        selected_match = self.matches[self.selected] if self.matches else {}
        replacements = set(selected_match.get('replacement_corrections', ()))
        self.composition.setText(' '.join(
            ('<span style="color:#DC2626">' + SYMBOLS[c].replace('𠃍', '乛') + '</span>'
             if index in replacements else SYMBOLS[c].replace('𠃍', '乛')) +
            ('<sub style="font-size:9px">?</sub>' if self.fuzzy_toggle.isChecked() and
             (self.evidence[index]['ambiguous'] or index in corrected) else '')
            for index, c in enumerate(self.code)) or '等待落笔')
        self.composition.setToolTip('；'.join(
            f'候选「{selected_match["char"]}」：第{index+1}笔 '
            f'{SYMBOLS[self.code[index]]} → {SYMBOLS[selected_match["code"][index]]}'
            for index in sorted(replacements)))
        for i, button in enumerate(self.buttons):
            absolute = offset + i
            button.setText(f'{i+1}  {self.candidates[absolute]}' if absolute < len(self.candidates) else '—')
            button.setEnabled(absolute < len(self.candidates))
            button.setStyleSheet('background: #234cdb; color: white; font-weight: bold;'
                                if absolute == self.selected and self.candidates else '')

    def accept_gesture(self, event):
        # Match main's existing firmware gesture mapping, including class 5 tap.
        if event.kind != 'trigger' or event.protocol_version != 2:
            return
        self.record('gesture', class_id=event.class_id)
        if event.class_id in (3, 4):
            self.selected += 1 if event.class_id == 4 else -1
            self.refresh()
        elif event.class_id == 1:
            self.undo()
        elif event.class_id == 2:
            self.clear()
        elif event.class_id == 5:
            self.commit(self.selected)

    def accept_sdk_tap(self):
        self.trace_view.show_trace([])
        if not self.gestures:
            self.commit(self.selected)

    def commit(self, index):
        if not 0 <= index < len(self.candidates):
            return
        character = self.candidates[index]
        self.editor.insert(character)
        self.record('commit', character=character, code=self.code)
        self.clear()

    def undo(self):
        self.evidence = self.evidence[:-1]
        self.code = self.code[:-1]; self.selected = 0; self.refresh()

    def clear(self):
        self.evidence = []
        self.code = ''; self.selected = 0; self.refresh()

    def show_stats(self, value):
        self.warmup = value.warmup_frames
        self.metrics.setText(f'预热 {value.warmup_frames}/200 · 收包 {value.packets} · '
                             f'缺包 {value.missing_packets} · 重置 {value.resets} · '
                             f'接触概率 {value.contact_probability:.2f}\n'
                             f'推理批次 {value.inference_batch_ms:.1f} ms · '
                             f'收包队列 {value.packet_queue_age_ms:.1f} ms')
        if time.monotonic() - getattr(self, 'last_stats_log', 0) >= 1:
            self.record('stats', **asdict(value))
            self.last_stats_log = time.monotonic()

    def show_contact(self, samples):
        if self.worker is None: return
        last = samples[-1]
        self.last_contact_at = time.monotonic()
        state = (
                 '模型判定接触（尚无有效笔画）' if last['drawing'] and last['path_length'] < 1.5 else
                 '书写中' if last['drawing'] else '未采集笔画')
        probability = last['stroke_contact']
        diagnostic = f" · 概率诊断 {probability:.2f}" if probability is not None else ''
        self.contact_state.setText(f"接触状态：{state}{diagnostic}")
        self.record('contact_samples', samples=samples)
        if self.collection is not None:
            self.collection_record('sdk_samples', trial_index=self.collection['index'], samples=samples)

    def reset_contact_display(self):
        if self.worker is None: return
        self.contact_state.setText('接触状态：采集已重置，等待重新落笔')
        self.record('contact_reset', monotonic=time.monotonic())

    def check_contact_freshness(self):
        if self.worker is not None and time.monotonic()-getattr(self, 'last_contact_at', 0) > .3:
            self.contact_state.setText('接触状态：暂无新数据，不能判断是否接触')

    def mark_contact(self, actual):
        self.record('actual_contact', monotonic=time.monotonic(), touching=actual)
        self.contact_prompt.setText('已标记：手指接触桌面' if actual else '已标记：手指抬起')

    def start_contact_test(self):
        if self.worker is None or getattr(self, 'warmup', 0) < 200:
            self.contact_prompt.setText('请先连接戒指，等待预热达到 200/200。')
            return
        self.phase_start = time.monotonic()
        self.phase_index = -1
        self.contact_test.setEnabled(False)
        self.phase_timer.start(100)
        self.update_contact_test()

    def update_contact_test(self):
        now = time.monotonic()
        index = int((now-self.phase_start)//3)
        if index >= 7:
            self.phase_timer.stop()
            self.contact_test.setEnabled(True)
            self.contact_prompt.setText('检测完成，日志已保存。可再次测试或停止连接。')
            self.record('contact_test_end', monotonic=now)
            return
        expected = index % 2 == 1
        if index != self.phase_index:
            self.phase_index = index
            self.record('contact_prompt', monotonic=now, touching=expected, phase=index)
            QApplication.beep()
        remaining = max(1, 3-int((now-self.phase_start)%3))
        action = ('将食指接触桌面并轻轻画横线' if expected
                  else '抬起食指并在空中轻轻移动')
        self.contact_prompt.setText(f'{action} · 剩余 {remaining} 秒。动作完成时点击对应标记按钮。')

    def closeEvent(self, event):
        if self.worker is not None:
            self.closing = True
            self.worker.stop()
            self.status.setText('正在断开戒指…')
            event.ignore()
        else:
            if self.collection is not None:
                self.collection_record('close', completed=self.collection['index'])
                self.collection['log'].close()
            self.log.close()
            event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='Ringo6B72')
    parser.add_argument('--self-test', type=Path, help='Offline smoke test; save a screenshot')
    parser.add_argument('--no-gestures', action='store_true', help='Disable firmware swipes for comparison')
    parser.set_defaults(collect=True)
    parser.add_argument('--new', action='store_true', help='Start a new collection')
    parser.add_argument('--recognizer', choices=['structure', 'dtw'], default='structure')
    args = parser.parse_args()
    app = QApplication([])
    window = Window(args.device, synthetic=bool(args.self_test), gestures=not args.no_gestures,
                    collect=args.collect, new_collection=args.new, recognizer=args.recognizer)
    window.show()
    if args.self_test:
        window.toggle()
        deadline = time.monotonic() + 10
        timer = QTimer()
        def verify():
            if window.code:
                assert window.code == 'h' and window.candidates[0] == '一'
                assert len(window.trace_view.points) >= 5
                if args.collect:
                    from PySide6.QtTest import QTest
                    from collections import Counter
                    sample = window.collection['pending']
                    assert sample is not None
                    assert window.grab().save(str(args.self_test))
                    window.accept_stroke(sample)
                    assert window.collection['index'] == 0 and window.collection['pending'] is sample
                    window.retry_collection()
                    assert window.collection['pending'] is None
                    for index in range(190):
                        window.accept_stroke(sample)
                        window.connect_button.setFocus()
                        QTest.keyClick(window.connect_button, (Qt.Key_1, Qt.Key_2, Qt.Key_3)[index % 3])
                        assert window.collection['index'] == index + 1
                    saved = [json.loads(line) for line in window.collection['path'].read_text(encoding='utf-8').splitlines()]
                    labels = [row for row in saved if row['kind'] == 'annotation']
                    assert len(labels) == 190
                    assert Counter(row['target']['shape'] for row in labels)['撇点'] == 50
                    assert window.collection['pending'] is None
                    print('PASS: original test window, actual Ring stroke worker, continuous rewriting, focused keyboard ratings and 190 labels')
                    timer.stop(); window.close()
                    return
                from types import SimpleNamespace
                def swipe(class_id, kind='trigger', version=2):
                    window.worker.gesture.emit(SimpleNamespace(
                        class_id=class_id, kind=kind, protocol_version=version))
                for _ in range(5): swipe(4)
                assert window.selected == 5
                assert window.buttons[0].text().endswith(window.candidates[5])
                swipe(3)
                assert window.selected == 4
                swipe(4, kind='event'); swipe(4, version=1)
                assert window.selected == 4
                chosen = window.candidates[4]
                assert window.grab().save(str(args.self_test))
                if window.gestures:
                    window.worker.tap.emit()
                    assert window.code == 'h' and window.editor.text() == ''
                    swipe(5, kind='event'); swipe(5, version=1)
                    assert window.code == 'h'
                    swipe(5)
                else:
                    window.worker.tap.emit()
                assert window.editor.text() == chosen and window.code == ''
                window.code = 'hs'; window.refresh()
                if window.gestures:
                    window.worker.tap.emit()
                    assert window.code == 'hs' and window.editor.text() == chosen
                swipe(1)
                assert window.code == 'h'
                swipe(2)
                assert window.code == ''
                window.last_contact_at = time.monotonic()-1
                window.check_contact_freshness()
                assert '暂无新数据' in window.contact_state.text()
                window.reset_contact_display()
                assert '已重置' in window.contact_state.text()
                window.warmup = 200
                window.start_contact_test()
                for index in range(7):
                    window.phase_start = time.monotonic()-index*3-.01
                    window.update_contact_test()
                    assert window.phase_index == index
                window.mark_contact(False)
                window.phase_start = time.monotonic()-21.01
                window.update_contact_test()
                assert not window.phase_timer.isActive() and window.contact_test.isEnabled()
                print('PASS: original SDK contact, strokes, four-way callbacks, candidate paging and firmware tap commit')
                timer.stop(); window.close()
            elif time.monotonic() > deadline:
                print('FAIL: no stroke received'); timer.stop(); window.close(); app.exit(1)
        timer.timeout.connect(verify); timer.start(100)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
