"""Standalone ring trajectory collection window, independent of the input app."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from selection_capture import ContactCapture, atomic_save, now
import eight_direction_selection as selection_profile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from proximic_ring.touchpad.stroke.stroke_examples import task_example

DEFAULT_SDK = ROOT / "src"
DEFAULT_OUTPUT = ROOT / "data" / "recordings" / "selection-eight"


class RingWorker:
    def __init__(self, device, sdk_root, messages):
        self.device, self.sdk_root, self.messages = device, sdk_root, messages
        self.stop = threading.Event()
        self.overflow = threading.Event()
        self.thread = threading.Thread(target=self.run, name="SelectionRing", daemon=True)

    def post(self, kind, value):
        try:
            self.messages.put_nowait((kind, value))
        except queue.Full:
            self.overflow.set()

    def run(self):
        try:
            asyncio.run(self.acquire())
        except Exception as error:
            self.post("error", str(error))
        finally:
            self.post("ended", None)

    async def acquire(self):
        sys.path.insert(0, str(self.sdk_root))
        from ring_python_sdk import RingSession
        session = RingSession("Ringo", 8.)
        session.battery_poll_enabled = False
        ended = asyncio.Event()
        outcome = []

        def stopped(error):
            outcome.append(error)
            ended.set()

        try:
            if not await session.connect_target(self.device):
                raise RuntimeError("未找到或未能连接戒指；请检查名称、蓝牙和其他程序的连接。")
            if self.stop.is_set():
                return
            await session.touchpad_on(
                on_event=lambda e: self.post("event", asdict(e)),
                on_stats=lambda s: self.post("stats", asdict(s)),
                on_stopped=stopped, duration_s=None)
            self.post("ready", {"name": session.target_name, "address": session.target_address})
            while not self.stop.is_set() and not ended.is_set():
                await asyncio.sleep(.05)
            if outcome and outcome[-1]:
                raise outcome[-1]
        finally:
            await asyncio.wait_for(session.disconnect(), 10.)


class CollectionWindow:
    def __init__(self, root, output_dir=DEFAULT_OUTPUT, sdk_root=DEFAULT_SDK, device="Ringo", profile=selection_profile):
        self.profile = profile
        self.prefix = getattr(profile, "FILE_PREFIX", "selection-eight")
        self.root, self.output_dir, self.sdk_root = root, Path(output_dir), Path(sdk_root)
        self.session = self.path = self.raw_stream = self.worker = None
        self.messages = queue.Queue(maxsize=8192)
        self.capture = ContactCapture()
        self.armed = self.connected = self.closing = False
        self.warm = self.worker_error = False
        self.storage_ok = True
        self.raw_flush = 0
        self.participant = tk.StringVar(value="P01")
        self.device = tk.StringVar(value=device)
        self.train_phase, self.evaluation_phase = getattr(profile, "PHASE_LABELS", ("训练采集", "独立验证"))
        evaluation_only = getattr(profile, "EVALUATION_ONLY", False)
        self.phase = tk.StringVar(value=self.evaluation_phase if evaluation_only else self.train_phase)
        self.setup_notes = tk.StringVar(value=getattr(profile, "DEFAULT_SETUP_NOTES", ""))
        self.setup_notes_input = None
        self.rounds = tk.IntVar(value=getattr(profile, "EVALUATION_ROUNDS", 3) if evaluation_only else getattr(profile, "TRAIN_ROUNDS", 3))
        self.status = tk.StringVar(value="先建立采集记录，再连接戒指。")
        self.progress = tk.StringVar()
        self.title = tk.StringVar(value="准备采集")
        self.note = tk.StringVar(value=getattr(profile, "INTRO", "上滑后向左／右横移，用距离区分 1–5 档。"))
        self.detail = tk.StringVar(value="每笔是一条连续触桌轨迹；转弯时保持接触，结束时抬手。")
        self.storage = tk.StringVar(value=f"自动备份目录：{self.output_dir}")
        self.stats = tk.StringVar(value="未连接")
        root.title(getattr(profile, "TITLE", "上滑选字 · 往返轨迹采集 v2"))
        root.geometry("1080x850")
        root.minsize(900, 560)
        root.configure(bg="#f5f7fc")
        root.protocol("WM_DELETE_WINDOW", self.close)
        shell = ttk.Frame(root, padding=12)
        shell.pack(fill="both", expand=True)
        # Allocate the operation area first so a short viewport never clips it.
        self.footer = ttk.Frame(shell, padding=(8, 6))
        self.footer.pack(side="bottom", fill="x")
        content = ttk.Frame(shell)
        content.pack(fill="both", expand=True)
        self.content_view = tk.Canvas(content, highlightthickness=0, bg="#f5f7fc")
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.content_view.yview)
        scrollbar.pack(side="right", fill="y")
        self.content_view.pack(side="left", fill="both", expand=True)
        self.content_view.configure(yscrollcommand=scrollbar.set)
        outer = ttk.Frame(self.content_view, padding=8)
        self.content_item = self.content_view.create_window(0, 0, window=outer, anchor="nw")
        outer.bind("<Configure>", lambda e: self.content_view.configure(scrollregion=self.content_view.bbox("all")))
        self.content_view.bind("<Configure>", lambda e: self.resize_content(e.width))
        self.wrapped_labels = []
        ttk.Label(outer, text=getattr(profile, "TITLE", "上滑选字 · 轨迹采集"), font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        ttk.Label(outer, text=getattr(profile, "INTRO", "先采数据，再验证手势识别和距离选字。普通笔画与轻点也会作为对照采集。"),
                  font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(6, 14))
        setup = ttk.LabelFrame(outer, text="本次记录", padding=10)
        setup.pack(fill="x")
        ttk.Label(setup, text="参与者编号").grid(row=0, column=0, padx=4)
        self.participant_input = ttk.Entry(setup, textvariable=self.participant, width=12)
        self.participant_input.grid(row=0, column=1, padx=4)
        phases = [self.evaluation_phase] if evaluation_only else [self.train_phase, self.evaluation_phase]
        self.phase_input = ttk.Combobox(setup, textvariable=self.phase, values=phases, state="readonly", width=11)
        self.phase_input.grid(row=0, column=2, padx=4)
        self.phase_input.bind("<<ComboboxSelected>>", lambda e: self.rounds.set(getattr(profile, "EVALUATION_ROUNDS", 7) if self.phase.get() == self.evaluation_phase else getattr(profile, "TRAIN_ROUNDS", 3)))
        ttk.Label(setup, text="轮数").grid(row=0, column=3, padx=4)
        self.rounds_input = ttk.Spinbox(setup, from_=1, to=30, textvariable=self.rounds, width=4)
        self.rounds_input.grid(row=0, column=4, padx=4)
        self.new_button = ttk.Button(setup, text="建立新记录", command=self.create)
        self.new_button.grid(row=0, column=5, padx=6)
        self.load_button = ttk.Button(setup, text="续采 JSON", command=self.load)
        self.load_button.grid(row=0, column=6, padx=6)
        if hasattr(profile, "SETUP_NOTES_LABEL"):
            ttk.Label(setup, text=profile.SETUP_NOTES_LABEL).grid(row=1, column=0, sticky="w", pady=(8, 0))
            self.setup_notes_input = ttk.Entry(setup, textvariable=self.setup_notes)
            self.setup_notes_input.grid(row=1, column=1, columnspan=6, sticky="ew", pady=(8, 0))
        connection = ttk.Frame(outer)
        connection.pack(fill="x", pady=12)
        ttk.Label(connection, text="戒指名称或蓝牙地址").pack(side="left")
        self.device_input = ttk.Entry(connection, textvariable=self.device, width=24)
        self.device_input.pack(side="left", padx=8)
        self.connect_button = ttk.Button(connection, text="连接戒指", command=self.connect)
        self.connect_button.pack(side="left")
        self.disconnect_button = ttk.Button(connection, text="断开", command=self.disconnect)
        self.disconnect_button.pack(side="left", padx=8)
        ttk.Label(connection, textvariable=self.stats).pack(side="left", padx=8)
        ttk.Label(outer, text="连接前请断开主程序的戒指连接。此窗口只采轨迹，不控制鼠标或写入文字。").pack(anchor="w")
        ttk.Label(outer, textvariable=self.progress).pack(anchor="w", pady=(12, 6))
        self.bar = ttk.Progressbar(outer)
        self.bar.pack(fill="x")
        title_label = ttk.Label(outer, textvariable=self.title, wraplength=990, font=("Microsoft YaHei UI", 24, "bold"))
        title_label.pack(anchor="w", pady=(14, 8))
        note_label = ttk.Label(outer, textvariable=self.note, wraplength=990, font=("Microsoft YaHei UI", 12))
        note_label.pack(anchor="w")
        self.wrapped_labels.extend([title_label, note_label])
        self.candidates = ttk.Frame(outer)
        self.candidates.pack(fill="x", pady=10)
        self.cells = []
        self.direction_cells = {}
        for direction, row, column, text in getattr(profile, "RADIAL_MENU", ()):
            cell = tk.Label(self.candidates, text=text, bg="#e9edf5", fg="#34425d",
                            font=("Microsoft YaHei UI", 13), padx=16, pady=6)
            cell.grid(row=row, column=column, padx=5, pady=3, sticky="ew")
            self.direction_cells[direction] = cell
        if self.direction_cells:
            tk.Label(self.candidates, text="先 ↑ 上滑\n再选方向 · 抬手结束", bg="#f5f7fc",
                     font=("Microsoft YaHei UI", 11)).grid(row=1, column=1, padx=10)
            for column in range(3):
                self.candidates.columnconfigure(column, weight=1)
        for i, text in enumerate("" if self.direction_cells else "一二三四五", 1):
            cell = tk.Label(self.candidates, text=f"{i}  {text}", bg="#e9edf5", fg="#34425d",
                            font=("Microsoft YaHei UI", 15), padx=25, pady=8)
            cell.pack(side="left", padx=(0, 12))
            self.cells.append(cell)
        if getattr(profile, "HIDE_CANDIDATES", False):
            self.candidates.pack_forget()
        self.canvas = tk.Canvas(outer, bg="white", height=270, highlightthickness=1, highlightbackground="#dfe5f1")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        detail_label = ttk.Label(outer, textvariable=self.detail, wraplength=990)
        detail_label.pack(anchor="w", pady=7)
        self.wrapped_labels.append(detail_label)
        self.test_summary = tk.StringVar()
        if hasattr(profile, "progress_note"):
            summary_label = ttk.Label(self.footer, textvariable=self.test_summary, wraplength=990)
            summary_label.pack(anchor="w")
            self.wrapped_labels.append(summary_label)
        self.test_prediction = tk.StringVar()
        if hasattr(profile, "footer_prediction"):
            prediction_label = ttk.Label(self.footer, textvariable=self.test_prediction, wraplength=990)
            prediction_label.pack(anchor="w")
            self.wrapped_labels.append(prediction_label)
        actions = ttk.Frame(self.footer)
        actions.pack(fill="x", pady=5)
        self.arm_button = ttk.Button(actions, text="开始／下一笔（1）", command=self.next_sample)
        self.arm_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="保存并下一笔（空格）", command=self.save_sample)
        self.save_button.pack(side="left", padx=10)
        self.redraw_button = ttk.Button(actions, text="重写这一笔（2）", command=self.retry)
        self.redraw_button.pack(side="left")
        self.export_button = ttk.Button(actions, text="导出 JSON", command=self.export)
        self.export_button.pack(side="right")
        self.undo_button = ttk.Button(actions, text="撤销上一笔", command=self.undo)
        self.undo_button.pack(side="right", padx=10)
        status_label = ttk.Label(self.footer, textvariable=self.status, wraplength=990, foreground="#0a31a0")
        status_label.pack(anchor="w", pady=7)
        storage_label = ttk.Label(self.footer, textvariable=self.storage, wraplength=990)
        storage_label.pack(anchor="w")
        self.wrapped_labels.extend([status_label, storage_label])
        self.plan_help = tk.StringVar()
        help_label = ttk.Label(outer, textvariable=self.plan_help, wraplength=990)
        help_label.pack(anchor="w", pady=(8, 0))
        self.wrapped_labels.append(help_label)
        root.bind("<KeyPress-1>", lambda e: self.shortcut(self.next_sample))
        root.bind("<KeyPress-2>", lambda e: self.shortcut(self.retry))
        root.bind("<Return>", lambda e: self.shortcut(self.next_sample))
        root.bind("<space>", lambda e: self.shortcut(self.save_sample))
        root.bind("<MouseWheel>", self.scroll_content)
        self.render()
        root.after(25, self.poll)

    def shortcut(self, callback):
        focus = self.root.focus_get()
        if focus is not None and focus in (self.participant_input, self.device_input, self.rounds_input, self.phase_input, self.setup_notes_input) and not focus.instate(["disabled"]):
            return
        callback()
        return "break"

    def resize_content(self, width):
        self.content_view.itemconfigure(self.content_item, width=width)
        for label in self.wrapped_labels:
            label.configure(wraplength=max(100, width-20))

    def scroll_content(self, event):
        if self.content_view.winfo_rooty() <= event.y_root < self.content_view.winfo_rooty() + self.content_view.winfo_height():
            self.content_view.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

    def next_sample(self):
        if self.armed or not self.connected or not self.warm or not self.storage_ok or self.closing:
            return
        if self.session and self.session["draft"]:
            self.save_sample()
            if not self.storage_ok:
                return
        self.arm()
        self.render()

    def install(self, session):
        if self.raw_stream:
            self.raw_stream.close()
        self.session = session
        self.path = self.output_dir / f"{self.prefix}-{session['sessionId']}.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_stream = self.path.with_suffix(".events.jsonl").open("a", encoding="utf-8")
        self.capture.reset()
        self.armed = False
        self.persist()
        self.render()

    def create(self):
        if self.worker:
            return
        if self.session and not messagebox.askyesno("新记录", "当前记录已有自动备份。建立另一份新记录？"):
            return
        try:
            session = self.profile.new_session(self.participant.get(), "evaluation" if self.phase.get() == self.evaluation_phase else "train",
                                               self.rounds.get(), self.device.get().strip())
            if self.setup_notes_input is not None:
                session["setupNotes"] = self.setup_notes.get().strip()
            self.install(session)
            self.status.set("记录已建立。连接并预热完成后，按 1 开始这一笔。")
        except Exception as error:
            messagebox.showerror("建立失败", str(error))

    def load(self):
        if self.worker:
            return
        filename = filedialog.askopenfilename(title="继续之前的记录", initialdir=self.output_dir, filetypes=[("JSON", "*.json")])
        if not filename:
            return
        if self.session and not messagebox.askyesno("续采", "当前记录已有自动备份。切换到所选记录？"):
            return
        try:
            data = self.profile.validate_session(json.loads(Path(filename).read_text(encoding="utf-8")))
            # A reviewable unfinished draft survives a restart.
            self.install(data)
            self.participant.set(data["participant"])
            self.phase.set(self.evaluation_phase if data["phase"] == "evaluation" else self.train_phase)
            self.setup_notes.set(data.get("setupNotes", getattr(self.profile, "DEFAULT_SETUP_NOTES", "")))
            self.device.set(data.get("device") or "Ringo")
            self.status.set("已续采。未确认的上一笔也已恢复。" +
                            "")
        except Exception as error:
            messagebox.showerror("续采失败", str(error))

    def persist(self):
        try:
            self.session["updatedAt"] = now()
            atomic_save(self.path, self.session)
            self.storage.set(f"已自动保存：{self.path}")
            if self.raw_stream:
                self.raw_stream.flush()
            self.storage_ok = True
            return True
        except Exception as error:
            self.armed = False
            self.storage_ok = False
            self.storage.set(f"自动保存失败，请立即导出：{error}")
            messagebox.showerror("保存失败", str(error))
            return False

    def connect(self):
        if self.worker or not self.session or not self.device.get().strip():
            return
        self.worker = RingWorker(self.device.get().strip(), self.sdk_root, self.messages)
        self.warm = self.worker_error = False
        self.status.set("正在连接并准备采集…")
        self.worker.thread.start()
        self.render()

    def disconnect(self):
        self.armed = self.connected = False
        self.capture.reset()
        if self.worker:
            self.worker.stop.set()
            self.status.set("正在停止数据流并断开连接…")
        self.render()

    def arm(self):
        if self.armed or not self.connected or not self.warm or not self.storage_ok or not self.session or self.session["draft"] or len(self.session["samples"]) == len(self.session["tasks"]):
            return
        self.capture.reset()
        self.armed = True
        self.status.set("已准备：抬手后开始提示动作，结束时抬手。")
        self.draw()
        self.render()

    def save_sample(self):
        if not self.session or not self.session["draft"]:
            return
        index = len(self.session["samples"])
        trace = self.session["draft"]
        sample = {**trace, "taskIndex": index, "task": self.session["tasks"][index],
                  "confirmedAt": now(), "source": "ring-sdk-touchpad"}
        if hasattr(self.profile, "predict_trace"):
            try:
                sample["gesturePrediction"] = self.profile.predict_trace(trace)
            except Exception as error:
                messagebox.showerror("识别测试失败", str(error))
                return
        if hasattr(self.profile, "sample_annotations"):
            try:
                sample.update(self.profile.sample_annotations(trace, sample["task"]))
            except Exception as error:
                messagebox.showerror("对照识别失败", str(error))
                return
        self.session["samples"].append(sample)
        self.session["draft"] = None
        if self.persist():
            self.status.set("已保存。按 1 开始下一笔。")
        self.render()

    def retry(self):
        if not self.session:
            return
        if self.session["draft"]:
            self.session["rejected"].append({"taskIndex": len(self.session["samples"]),
                                             "reason": "participant-retry", "trace": self.session["draft"]})
            self.session["draft"] = None
            if not self.persist():
                self.render()
                return
        self.armed = False
        self.capture.reset()
        self.arm()
        self.render()

    def undo(self):
        if not self.session or not self.session["samples"] or self.armed:
            return
        if not messagebox.askyesno("撤销上一笔", "撤销上一笔并返回它的提示？当前未保存的草稿也将放入重采记录。"):
            return
        if self.session["draft"]:
            self.session["rejected"].append({"taskIndex": len(self.session["samples"]), "reason": "undo-next-draft", "trace": self.session["draft"]})
        self.session["rejected"].append({"reason": "undo", "trace": self.session["samples"].pop()})
        self.session["draft"] = None
        self.capture.reset()
        self.persist()
        self.render()

    def export(self):
        if not self.session:
            return
        filename = filedialog.asksaveasfilename(title="导出采集数据", initialdir=self.output_dir,
                    initialfile=f"{self.prefix}-{self.session['participant']}-{self.session['phase']}-{len(self.session['samples'])}.json",
                    defaultextension=".json", filetypes=[("JSON", "*.json")])
        if filename:
            try:
                atomic_save(filename, {**self.session, "exportedAt": now()})
                self.status.set(f"已导出 {len(self.session['samples'])} 笔：{filename}")
            except Exception as error:
                messagebox.showerror("导出失败", str(error))

    def poll(self):
        if self.closing:
            if self.worker and self.worker.thread.is_alive():
                self.root.after(50, self.poll)
                return
            if self.raw_stream:
                self.raw_stream.close()
            self.root.destroy()
            return
        if self.worker and self.worker.overflow.is_set():
            self.worker.overflow.clear()
            while not self.messages.empty():
                self.messages.get_nowait()
            self.capture.reset()
            self.armed = False
            if self.session:
                self.session["interruptions"].append({"at": now(), "taskIndex": len(self.session["samples"]), "reason": "UI queue overflow"})
                self.persist()
            self.status.set("数据队列溢出；本笔已取消，请重采。")
        for _ in range(4000):
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "ready":
                self.connected = True
                self.session["deviceInfo"] = value
                self.persist()
                self.status.set("已连接。等待预热完成，再按 1 开始。")
            elif kind == "stats":
                self.warm = value["warmup_frames"] >= 200
                self.stats.set(f"预热 {value['warmup_frames']}/200 · 接触 {value['contact_probability']:.2f}")
            elif kind in {"ended", "error"}:
                self.connected = self.armed = False
                self.capture.reset()
                if kind == "error":
                    self.worker_error = True
                    self.status.set(f"连接错误：{value}")
                elif not self.worker_error:
                    self.status.set("数据流已停止，记录已保留。")
            elif kind == "event":
                self.event(value)
        if self.worker and not self.worker.thread.is_alive():
            self.worker = None
        self.draw()
        self.render()
        self.root.after(25, self.poll)

    def event(self, value):
        if self.raw_stream:
            try:
                self.raw_stream.write(json.dumps({"receivedAt": now(), "armed": self.armed,
                    "taskIndex": len(self.session["samples"]), "event": value}, ensure_ascii=False, allow_nan=False)+"\n")
                self.raw_flush += 1
                if self.raw_flush >= 100:
                    self.raw_stream.flush()
                    self.raw_flush = 0
            except Exception as error:
                self.disconnect()
                self.status.set(f"原始事件保存失败；已停止采集：{error}")
                return
        draft = self.session.get("draft") if self.session else None
        if (draft and value.get("kind") in {"click", "click_verdict"}
                and value["step"] == draft["endStep"]
                and 0 <= value["timestamp"] - draft["rawEvents"][-1]["timestamp"] <= .5):
            draft["rawEvents"].append(value)
            draft["sdkClickVerdict"] = True if value["kind"] == "click" else value["is_click"]
            self.persist()
        if not self.armed:
            return
        result = self.capture.feed(value)
        if isinstance(result, str):
            self.session["interruptions"].append({"at": now(), "taskIndex": len(self.session["samples"]), "reason": result})
            self.armed = False
            self.persist()
            self.status.set(result + "。按 1 重新开始。")
        elif isinstance(result, dict):
            self.armed = False
            self.session["draft"] = result
            self.persist()
            self.status.set("轨迹已采到：按 1 确认保存并开始下一笔，按 2 重写。不要按轨迹好不好看来筛选。")

    def draw(self):
        self.canvas.delete("all")
        draft = self.session.get("draft") if self.session else None
        points = [(p["x"], p["y"]) for p in draft["points"]] if draft else self.capture.preview() if self.armed else []
        if not points:
            self.canvas.create_text(20, 20, anchor="nw", text="等待采集 · 原始尺寸保存在数据中，画面仅缩放展示", fill="#778298")
            return
        width, height = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        scale = min((width-70)/max(1, max(xs)-min(xs)), (height-60)/max(1, max(ys)-min(ys)))
        coords = [(35+(x-min(xs))*scale, 30+(y-min(ys))*scale) for x, y in points]
        if len(coords) > 1:
            self.canvas.create_line(*[v for point in coords for v in point], fill="#0a31cc", width=3)
        x, y = coords[0]
        self.canvas.create_oval(x-5, y-5, x+5, y+5, fill="#248e5c", outline="")
        x, y = coords[-1]
        self.canvas.create_oval(x-4, y-4, x+4, y+4, fill="#dd6247", outline="")

    def render(self):
        session = self.session
        plan_version = session.get("planVersion", session["version"]) if session else getattr(self.profile, "PLAN_VERSION", 2)
        plan_size = len(self.profile.inventory(plan_version))
        self.plan_help.set(f"每轮 {plan_size} 笔。" + self.profile.PLAN_HELP)
        count = len(session["samples"]) if session else 0
        total = len(session["tasks"]) if session else 0
        done = bool(session and count == total)
        task = session["tasks"][count] if session and not done else None
        self.progress.set(f"已保存 {count} / {total} 笔" + (f" · 第 {task['round']} 轮 · {self.evaluation_phase if session['phase']=='evaluation' else self.train_phase}" if task else ""))
        if hasattr(self.profile, "progress_note"):
            self.test_summary.set(self.profile.progress_note(session))
        self.bar["value"] = count / total * 100 if total else 0
        if task:
            self.title.set(task["title"])
            note = self.profile.display_note(task) if hasattr(self.profile, "display_note") else task["note"]
            example = task_example(task)
            self.note.set(note + ('\n' + example if example else ''))
        elif done:
            self.title.set("本次采集完成")
            self.note.set(getattr(self.profile, "COMPLETION_NOTE", "记录已自动备份。导出 JSON 后，另开一次独立验证采集。"))
        for i, cell in enumerate(self.cells, 1):
            active = task and task.get("targetIndex") == i
            cell.configure(bg="#0a31cc" if active else "#e9edf5", fg="white" if active else "#34425d")
        for direction, cell in self.direction_cells.items():
            active = task and task.get("menuDirection") == direction
            cell.configure(bg="#0a31cc" if active else "#e9edf5", fg="white" if active else "#34425d")
        draft = session.get("draft") if session else None
        if hasattr(self.profile, "footer_sample_prediction"):
            self.test_prediction.set(self.profile.footer_sample_prediction(draft, task))
        elif hasattr(self.profile, "footer_prediction"):
            self.test_prediction.set(self.profile.footer_prediction(draft))
        if draft:
            self.detail.set(f"{len(draft['points'])} 点 · {draft['durationMs']:.0f} ms · 路径 {draft['pathLength']:.3f} 原始单位 · 坐标未归一化")
            if hasattr(self.profile, "trace_details") and task:
                self.detail.set(self.detail.get() + "\n" + self.profile.trace_details(draft, task))
        else:
            self.detail.set("转弯时保持触桌，结束时抬手。1 开始／保存并下一笔，2 重写；Enter 同 1，空格仅保存。")
        for button, enabled in [(self.arm_button, self.connected and self.warm and self.storage_ok and not self.armed and not done),
                                (self.save_button, bool(draft)), (self.redraw_button, bool(session and not done)),
                                (self.export_button, bool(session)), (self.undo_button, count > 0 and not self.armed),
                                (self.connect_button, bool(session) and not self.worker),
                                (self.disconnect_button, bool(self.worker)),
                                (self.new_button, not self.worker), (self.load_button, not self.worker)]:
            button.configure(state="normal" if enabled else "disabled")
        for widget in (self.participant_input, self.rounds_input, self.device_input):
            widget.configure(state="disabled" if self.worker else "normal")
        if self.setup_notes_input is not None:
            self.setup_notes_input.configure(state="disabled" if self.worker else "normal")
        self.phase_input.configure(state="disabled" if self.worker else "readonly")

    def close(self):
        if self.closing:
            return
        if self.session and not self.persist():
            return
        self.closing = True
        self.disconnect()
        self.status.set("正在安全断开戒指，随后关闭窗口…")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="Ringo")
    parser.add_argument("--sdk-root", type=Path, default=DEFAULT_SDK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    root = tk.Tk()
    CollectionWindow(root, args.output_dir, args.sdk_root, args.device)
    root.mainloop()


if __name__ == "__main__":
    main()
