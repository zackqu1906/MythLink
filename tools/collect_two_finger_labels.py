"""Prompted single/two-finger collection with ground-truth labels (Raw2 only).

Prompts start as soon as the ring is connected, so the model's cold start is
part of the recording. Each trial keeps the per-frame finger probability; the
finished session scores the current rule against whole-contact rules.
"""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import queue
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from two_finger_labels import ContactAssembler, LabelSession, evaluate, format_report, make_plan

OUTPUT_ROOT = ROOT / 'data' / 'recordings' / 'two-finger-labels'


class PacketLog:
    """Raw token packets for later replay; BLE callbacks must never wait on disk."""

    def __init__(self, path):
        self.items = queue.SimpleQueue()
        self.path = path
        self.thread = threading.Thread(target=self._write, name='two-finger-packets', daemon=True)
        self.thread.start()

    def add(self, packet):
        self.items.put(dict(kind='token_packet', hex=bytes(packet).hex(), arrival=time.monotonic()))

    def _write(self):
        with self.path.open('w', encoding='utf-8') as handle:
            while (item := self.items.get()) is not None:
                handle.write(json.dumps(item) + '\n')

    def close(self):
        self.items.put(None)
        self.thread.join(2.)


class RingWorker(QThread):
    status = Signal(str)
    ready = Signal()
    contact = Signal(object)
    stopped = Signal(str)

    def __init__(self, device, packet_path):
        super().__init__()
        self.device, self.packet_path = device, packet_path
        self.assembler = ContactAssembler()
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()

    def run(self):
        error = ''
        try:
            asyncio.run(self.stream())
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        self.stopped.emit(error)

    def on_event(self, event):
        contact = self.assembler.event(event)
        if contact is not None:
            self.contact.emit(contact)

    def hook(self, stream, packets):
        """Observe every model frame and packet without changing either path."""
        engine, original_frame = stream.receiver.engine, stream.receiver.engine.frame
        original_submit = stream.submit

        def frame(recognition, now, detector):
            self.assembler.frame(recognition.frame + 1, recognition.touch, recognition.finger)
            original_frame(recognition, now, detector)

        def submit(packet):
            packets.add(packet)
            return original_submit(packet)

        engine.frame, stream.submit = frame, submit

    async def stream(self):
        from ring_python_sdk import RingSession
        from ring_python_sdk.ble import send_mic_control, send_swipe_stop
        session = RingSession(self.device, 10., connection_backend='sdk', battery_poll_enabled=False)
        session.auto_reconnect = False
        ended = threading.Event()
        packets = PacketLog(self.packet_path)
        try:
            self.status.emit('正在连接戒指…请先关闭主程序和其他测试程序')
            if not await session.connect_target(self.device):
                raise RuntimeError('未找到戒指或连接失败')
            info = getattr(session._sdk_transport, 'device_info', None)
            if info is not None and '.touchpad.' not in info.firmware_version:
                raise RuntimeError(f'固件 {info.firmware_version} 不支持 Raw2 双指，请换配套触摸板固件的戒指')
            await send_mic_control(session.client, session.rx_uuid, on=False)
            await send_swipe_stop(session.client, session.rx_uuid)
            await session.touchpad_on(protocol='raw2', on_event=self.on_event,
                                      on_pointer_move=self.assembler.pointer,
                                      on_stopped=lambda error: ended.set(), duration_s=None)
            self.hook(session.touchpad, packets)
            self.ready.emit()
            while not self.stop_requested.is_set() and not ended.is_set():
                await asyncio.sleep(.05)
            if ended.is_set() and not self.stop_requested.is_set():
                raise session.touchpad_error or RuntimeError('数据流已结束')
        finally:
            try:
                if session.touchpad_active:
                    await session.touchpad_off()
            finally:
                await session.disconnect()
                packets.close()


class Window(QWidget):
    worker_type = RingWorker

    def __init__(self, device='Ringo6B72', *, rounds=1, seed=None, output_root=OUTPUT_ROOT):
        super().__init__()
        self.device, self.output_root = device, Path(output_root)
        self.rounds, self.seed = rounds, seed if seed is not None else int(time.time())
        self.session, self.worker, self.folder, self.collecting, self.saved = None, None, None, False, False
        self.setWindowTitle('双指识别标注采集')
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        self.status = QLabel('按提示操作：连接后立刻开始，这样能录到模型刚启动时的表现。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.prompt = QLabel('等待开始')
        self.prompt.setAlignment(Qt.AlignCenter)
        self.prompt.setFont(QFont('Microsoft YaHei UI', 40, QFont.Bold))
        self.prompt.setMinimumHeight(190)
        layout.addWidget(self.prompt)
        self.next_prompt = QLabel('')
        self.next_prompt.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.next_prompt)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.last = QLabel('每完成一次接触（落下到抬起）自动进入下一条。做错了按退格键重做。')
        self.last.setWordWrap(True)
        layout.addWidget(self.last)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('连接并开始')
        self.redo_button = QPushButton('重做上一条（退格）')
        self.skip_button = QPushButton('跳过这一条')
        self.finish_button = QPushButton('结束并保存')
        for button, slot in ((self.start_button, self.start), (self.redo_button, self.redo),
                             (self.skip_button, self.skip), (self.finish_button, self.finish)):
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText('采集结束后，这里显示当前规则和整次接触规则的对比。')
        layout.addWidget(self.report, 1)
        QShortcut(QKeySequence(Qt.Key_Backspace), self, activated=self.redo)
        self.set_running(False)

    def set_running(self, running):
        self.start_button.setEnabled(not running)
        self.redo_button.setEnabled(running)
        self.skip_button.setEnabled(running)
        self.finish_button.setEnabled(running)

    def start(self):
        self.folder = self.output_root / datetime.now().strftime('%Y%m%d-%H%M%S')
        self.folder.mkdir(parents=True, exist_ok=True)
        self.session = LabelSession(make_plan(self.rounds, self.seed))
        self.saved, self.collecting = False, False
        self.write('session.json', dict(device=self.device, rounds=self.rounds, seed=self.seed,
                                        total=self.session.total, created=datetime.now().isoformat(),
                                        schema='two-finger-labels-v1'))
        self.trial_path = self.folder / 'trials.jsonl'
        self.trial_path.write_text('', encoding='utf-8')
        self.report.clear()
        self.set_running(True)
        self.worker = self.worker_type(self.device, self.folder / 'packets.jsonl')
        self.worker.status.connect(self.status.setText)
        self.worker.ready.connect(self.begin)
        self.worker.contact.connect(self.accept_contact)
        self.worker.stopped.connect(self.worker_stopped)
        self.worker.start()

    def write(self, name, value):
        (self.folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')

    def begin(self):
        self.collecting = True
        self.status.setText(f'已连接，正在采集。数据保存在 {self.folder}')
        self.show_prompt()

    def show_prompt(self):
        current = self.session.current
        if current is None:
            self.prompt.setText('完成')
            self.next_prompt.setText('')
            return
        self.prompt.setText(current['prompt'])
        self.prompt.setStyleSheet(f"color:{'#c2410c' if current['fingers'] == 2 else '#234cdb'};")
        upcoming = self.session.pending[1]['prompt'] if len(self.session.pending) > 1 else '最后一条'
        self.next_prompt.setText(f'下一条：{upcoming}')
        self.progress.setRange(0, self.session.total)
        self.progress.setValue(self.session.completed)
        self.progress.setFormat(f'{self.session.completed} / {self.session.total}')

    def append_trial(self, trial):
        with self.trial_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(trial, ensure_ascii=False) + '\n')

    def accept_contact(self, contact):
        if not self.collecting:
            return
        trial = self.session.accept(contact)
        if trial is None:
            return
        self.append_trial(trial)
        two = trial['sdk_two_finger_frames'] > 0
        expected = trial['label']['fingers'] == 2
        self.last.setText(f"上一条「{trial['label']['prompt']}」：当前规则判为{'双指' if two else '单指'} "
                          f"{'✓' if two == expected else '✗'}　（退格键可重做）")
        if self.session.current is None:
            self.finish()
        else:
            self.show_prompt()

    def redo(self):
        if self.collecting and (trial := self.session.redo()) is not None:
            self.append_trial(dict(kind='discard', index=trial['index']))
            self.last.setText(f"已作废上一条，请重做：{trial['label']['prompt']}")
            self.show_prompt()

    def skip(self):
        if self.collecting:
            self.session.skip()
            if self.session.current is None:
                self.finish()
            else:
                self.show_prompt()

    def finish(self):
        self.collecting = False
        self.set_running(False)
        self.save_report()
        self.stop_worker()
        # A new run must wait until the old one has released the ring.
        self.start_button.setEnabled(self.worker is None)
        self.prompt.setText('采集结束')
        self.next_prompt.setText('')

    def save_report(self):
        if self.session is None or self.saved:
            return
        self.saved = True
        report = evaluate(self.session.trials)
        self.write('report.json', report)
        self.report.setPlainText(format_report(report) + f'\n\n已保存到 {self.folder}')

    def stop_worker(self):
        if self.worker is not None:
            self.worker.stop()

    def worker_stopped(self, error):
        was_collecting, self.collecting = self.collecting, False
        self.worker = None
        self.set_running(False)
        self.save_report()
        if error:
            self.status.setText(f'连接中断：{error}。已采集的数据已保存，可重新开始。')
        elif was_collecting:
            self.status.setText('连接已结束，已采集的数据已保存。')
        else:
            self.status.setText('已断开，数据已保存。')

    def closeEvent(self, event):
        self.collecting = False
        self.save_report()
        worker = self.worker
        if worker is not None:
            worker.stop()
            worker.wait(5000)
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='Ringo6B72')
    parser.add_argument('--rounds', type=int, default=1, help='Repeat the 94-prompt plan this many times')
    parser.add_argument('--seed', type=int, help='Shuffle seed; defaults to the current time')
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle('Fusion')
    window = Window(args.device, rounds=max(1, args.rounds), seed=args.seed)
    from ring_python_sdk.touchpad.raw2_model import raw2_platform_supported
    if not raw2_platform_supported():
        window.status.setText('当前环境不支持 Raw2 双指，无法采集。')
        window.start_button.setEnabled(False)
    window.show()
    app.exec()


if __name__ == '__main__':
    main()
