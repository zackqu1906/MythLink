"""Local support bundles with calendar ranges and complete retained evidence."""
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from ..runtime_paths import app_data_root, is_frozen
from ..version import app_version
from ..voice.history_export import _record_snapshot, _open_regular, _check_cancel


RANGE_LABELS = {"today": "今天", "week": "近一周", "all": "全部"}
README = """Mythlink 详细诊断包 v1

diagnostic.log：按时间筛选的完整统一运行日志，不受窗口最近 1000 行或清空窗口影响。
包含已记录的启动、连接、音频、ASR、模型、输入法、手势、触控板、场景、权限和异常堆栈。
runtime.json：导出时的版本、系统、进程、运行状态及关键配置；不是故障发生时的状态。
interactions/：时间范围内相关语音交互的完整 record.json、事件、ASR 更新，以及已留存的录音、截图、IMU。
ime/：已存在的输入法光标诊断，不会为导出开启额外采集。
manifest.json：请求范围、实际时间覆盖、源文件、数量、缺失/读取失败和文件 SHA256。

今天指本机当天零点至导出开始；近一周指今天和前六个自然日；全部指本地仍保留的记录。
日志按每条记录的时间和时区筛选，不按文件修改时间筛选；跨文件的异常续行随上一条记录保留。
没有可识别时间的独立片段保守保留，数量见 unknown_time_lines，不能认定发生于所选日期。
语音记录按创建至最后更新时间与所选范围是否重叠纳入，保留完整交互以便分析跨午夜操作。
损坏或缺少时间的记录会在清单中说明；未采集、已删除或被轮转覆盖的历史无法恢复。
日志轮转快照和每条语音记录各自一致；导出期间的新日志、新交互或后续更新可能不在包中。

排查顺序：先查看 manifest.json 的覆盖与 warnings，再用 run / trace / interaction_id 串联事件。
先核对 runtime.json 的版本和源码/打包运行方式，随后定位错误前后的日志及完整交互。
模型输入、输出、耗时、概率、OCR 和目标原文以 record.json 中实际留存的字段为准；缺失不会补造。
本包包含个人口述、上下文和已保存的录音/截图，仅在你选择导出后写入本机，不会自动上传。
配置中的凭据字段不导出；原始业务证据保持原样。分享前可查看包内内容。
"""


def parse_time(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value).astimezone()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


@dataclass(frozen=True)
class LogRange:
    key: str
    start: datetime | None
    end: datetime

    @classmethod
    def create(cls, key, now=None):
        if key not in RANGE_LABELS:
            raise ValueError("请选择今天、近一周或全部日志")
        explicit_zone = now is not None and now.tzinfo is not None
        now = now or datetime.now().astimezone()
        if now.tzinfo is None:
            now = now.astimezone()
        day = now.date() - timedelta(days=6 if key == "week" else 0)
        # Localize midnight separately so DST changes do not turn calendar days
        # into a rolling 168-hour interval. Honor explicitly supplied timezones.
        if explicit_zone:
            start = datetime.combine(day, time.min, tzinfo=now.tzinfo)
        else:
            start = datetime.combine(day, time.min).astimezone()
        return cls(key, None if key == "all" else start, now)

    def contains(self, stamp):
        return (self.start is None or stamp >= self.start) and stamp <= self.end

    def overlaps(self, record):
        first, last = parse_time(record.get("created_at")), parse_time(record.get("updated_at"))
        known = [value for value in (first, last) if value is not None]
        return not known or (min(known) <= self.end and (self.start is None or max(known) >= self.start))


def line_time(line):
    if line.startswith("["):
        return parse_time(line[1:].partition("]")[0])
    if line.startswith("{"):
        try:
            row = json.loads(line)
            for key in ("timestamp", "time", "created_at"):
                if key in row:
                    return parse_time(row[key])
        except (ValueError, TypeError):
            pass
    return None


def filter_logs(paths, target, selection, cancel=None):
    """Stream rotations in order, preserving traceback continuation lines."""
    stats = dict(lines=0, selected_lines=0, unknown_time_lines=0,
                 available_start=None, available_end=None, selected_start=None, selected_end=None)
    first = last = selected_first = selected_last = previous = None
    for path in paths:
        with Path(path).open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                _check_cancel(cancel)
                stats["lines"] += 1
                stamp = line_time(line)
                if stamp is not None:
                    previous = stamp
                    first = stamp if first is None else min(first, stamp)
                    last = stamp if last is None else max(last, stamp)
                if previous is None or selection.contains(previous):
                    target.write(line.encode("utf-8"))
                    stats["selected_lines"] += 1
                    if previous is None:
                        stats["unknown_time_lines"] += 1
                    else:
                        selected_first = previous if selected_first is None else min(selected_first, previous)
                        selected_last = previous if selected_last is None else max(selected_last, previous)
    for key, stamp in (("available_start", first), ("available_end", last),
                       ("selected_start", selected_first), ("selected_end", selected_last)):
        stats[key] = stamp.isoformat() if stamp else None
    return stats


_SECRET = re.compile(r"api.?key|access.?key|secret|password|passwd|token|authorization|credential|private.?key", re.I)


def safe_configuration(values):
    """Exclude credential fields, including nested JSON stored by QSettings."""
    if isinstance(values, dict):
        return {str(key): "[omitted]" if _SECRET.search(str(key)) else safe_configuration(value)
                for key, value in values.items()}
    if isinstance(values, (list, tuple)):
        return [safe_configuration(value) for value in values]
    if isinstance(values, str) and values.lstrip().startswith(("{", "[")):
        try:
            return safe_configuration(json.loads(values))
        except ValueError:
            pass
    if values is None or isinstance(values, (str, int, float, bool)):
        return values
    return str(values)


def export_diagnostics(writer, destination, selection, *, state=None, store=None,
                       ime_root=None, cancel=None, on_progress=None):
    """Publish one complete ZIP; leave no partial file on failure/cancellation."""
    destination = Path(destination).expanduser().resolve()
    if destination in writer.files() or destination == writer.path.with_name('.' + writer.path.name + '.lock'):
        raise ValueError("导出位置不能覆盖正在使用的日志")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = dict(format="mythlink-diagnostics", export_version=1,
                    range=selection.key, range_label=RANGE_LABELS[selection.key],
                    requested_start=selection.start.isoformat() if selection.start else None,
                    requested_end=selection.end.isoformat(), timezone=str(selection.end.tzinfo),
                    retention=dict(max_bytes_per_file=writer.max_bytes, backup_count=writer.backup_count),
                    log_sources=[], record_count=0, warnings=[], files=[])
    warnings = manifest["warnings"]
    with tempfile.TemporaryDirectory(prefix=".mythlink-diagnostics-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        snapshots = []
        # Copy under the shared rotation lock; filter/compress outside it so
        # exporting weeks of data does not stall ongoing input/log producers.
        with writer.locked():
            for index, path in enumerate(writer.files()):
                _check_cancel(cancel)
                try:
                    with ExitStack() as stack:
                        source = _open_regular(stack, path)
                        snapshot = temporary / f"log-{index}"
                        with snapshot.open("wb") as output:
                            while chunk := source.read(1024 * 1024):
                                _check_cancel(cancel)
                                output.write(chunk)
                        snapshots.append(snapshot)
                        manifest["log_sources"].append(dict(path=str(path), bytes=snapshot.stat().st_size))
                except FileNotFoundError:
                    continue
                except (OSError, ValueError) as exc:
                    warnings.append(dict(source=str(path), reason=str(exc)))
        log_path = temporary / "diagnostic.log"
        with log_path.open("wb") as output:
            manifest["log_coverage"] = filter_logs(snapshots, output, selection, cancel)
        if not snapshots:
            warnings.append(dict(source="diagnostic.log", reason="没有可读取的运行日志"))
        archive_path = temporary / "bundle.zip"
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
            def add_stream(name, stream):
                digest, size = hashlib.sha256(), 0
                with archive.open(name, "w", force_zip64=True) as target:
                    while chunk := stream.read(1024 * 1024):
                        _check_cancel(cancel)
                        target.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                manifest["files"].append(dict(path=name, bytes=size, sha256=digest.hexdigest()))

            def add_json(name, value):
                add_stream(name, io.BytesIO((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()))

            with log_path.open("rb") as source:
                add_stream("diagnostic.log", source)
            add_json("runtime.json", dict(captured_at=selection.end.isoformat(), app_version=app_version(),
                system=platform.system(), system_version=platform.mac_ver()[0] or platform.release(),
                machine=platform.machine(), python=platform.python_version(), packaged=is_frozen(),
                executable=sys.executable, process=os.getpid(), data_root=str(app_data_root()),
                state=safe_configuration(state or {})))
            if store is not None:
                with store._lock:
                    if store.user_root.is_symlink() or store.interactions_root.is_symlink():
                        raise ValueError("语音历史目录不可用")
                    identities = sorted(p.parent.name for p in store.interactions_root.glob("*/record.json"))
                manifest["source_record_count"] = len(identities)
                for index, identity in enumerate(identities):
                    _check_cancel(cancel)
                    # Reuse the history export's consistent, safe record and
                    # attachment snapshot (including bounds on attachment paths).
                    with ExitStack() as stack:
                        try:
                            record, raw, files, missing = stack.enter_context(_record_snapshot(store, identity))
                        except (OSError, ValueError, TypeError, KeyError) as exc:
                            warnings.append(dict(source=identity, reason=str(exc)))
                        else:
                            if selection.overlaps(record):
                                if parse_time(record.get("created_at")) is None:
                                    warnings.append(dict(source=identity, reason="缺少有效创建时间，保守保留相关记录"))
                                base = f"interactions/{identity}/"
                                add_stream(base + "record.json", io.BytesIO(raw))
                                for name, stream in files.items():
                                    add_stream(base + name, stream)
                                warnings.extend(missing)
                                manifest["record_count"] += 1
                    if on_progress:
                        on_progress(index + 1, len(identities))
            if ime_root is not None:
                for path in sorted(Path(ime_root).glob("caret-trace-*.jsonl")):
                    _check_cancel(cancel)
                    try:
                        with ExitStack() as stack:
                            source = _open_regular(stack, path)
                            # Pin a bounded snapshot of the independently written
                            # native trace; never follow a growing file forever.
                            raw = source.read(os.fstat(source.fileno()).st_size)
                        snapshot = temporary / "ime.jsonl"
                        snapshot.write_bytes(raw)
                        filtered = temporary / "ime-filtered.jsonl"
                        with filtered.open("wb") as output:
                            coverage = filter_logs([snapshot], output, selection, cancel)
                        if coverage["selected_lines"]:
                            with filtered.open("rb") as source:
                                add_stream("ime/" + path.name, source)
                        manifest.setdefault("ime_sources", []).append(dict(path=str(path), **coverage))
                    except (OSError, ValueError) as exc:
                        warnings.append(dict(source=str(path), reason=str(exc)))
            add_stream("README.txt", io.BytesIO(README.encode("utf-8")))
            manifest["completed_at"] = datetime.now().astimezone().isoformat()
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        _check_cancel(cancel)
        archive_path.replace(destination)
    return dict(path=str(destination), record_count=manifest["record_count"],
                log_lines=manifest["log_coverage"]["selected_lines"], warning_count=len(warnings))


def suggested_filename(selection):
    return f"Mythlink-diagnostics-{selection.key}-{selection.end:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.zip"
