"""Portable history archives, with per-record snapshots and analysis indexes."""
from collections import Counter
from contextlib import ExitStack, contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from .modification_dataset import _CURRENT_INTERACTION_ID


README = """Mythlink 语音历史导出 v1

summary.csv：每条交互一行，适合表格筛选；UTF-8 BOM 编码。
records.jsonl：每行一个完整 InteractionRecord，不截断口述、原文、context 或模型输入。
interactions/<interaction_id>/：原始 record.json，以及已留存的录音、截图、ASR 更新、事件和 IMU 文件。
associations.jsonl：导出开始时已有的人工关联与偏好标注。通过 interaction_id 关联；清单记录缺失引用。
manifest.json：导出时间、各版本/模式数量、context 覆盖率、文件校验和、跳过/缺失文件。

推荐分析顺序
1. 用 interaction_id 连接 CSV、完整记录和附件；ASR session_id 可能跨运行重复，不能用作全局主键。
2. 按 schema_version 和 routing_mode 分组；先检查 context 的状态与覆盖率，再比较行为。
3. 区分实际模型输入与采集证据：screen_capture/screen_evidence 是原始截图及 OCR；
   input_context 是输入法/目标快照；context_preparation 是整理后的上文及其模型调用；
   asr.context 是 ASR 上文；llm.requests[].request_input 才是已记录的实际正文请求输入。
   旧版 llm.requests[].input 可能只是准备阶段的数据，不能当作已发送的实际输入。
4. requested/selected/final_applied/predicted 等 mode 字段意义不同；分类结果不是用户意图真值。
   outcome.accepted、acceptance_strength、feedback、events 和 associations 需结合判断。
   applied/pending_undo 不能等同于用户满意；取消也不一定表示识别错误。

CSV 字段约定
created_at/updated_at/captured_at 使用记录原有 ISO 时间和时区；不要按文件夹名猜测时区。
editable_text_present 区分“保存了空原文”和“没有保存原文”。context_complete 为原始值，不补造。
context_enabled 只表示模型上文开关，关闭时仍可能留存原始截图/OCR。
actual_model_input_count 只统计 request_input 为对象的正文请求，不代表独立的分类/ASR 请求数量。
audio_in_archive/screenshot_in_archive 表示附件确实在此次导出中。
CSV 的空单元格代表未记录或不适用，完整 JSON 保留 null、空字符串及原始结构。
文本若以表格公式字符开头，CSV 增加单引号防止被执行；完整 JSON 保留原文。

采集边界
这是已有本地记录的导出，不补采截图、不调用模型、不修改历史，也不改变后续采集范围。
导出开始时固定记录列表，每条记录在自己的读取时刻取得一致快照；压缩不占用记录写入锁。
导出期间新产生的交互及某条快照之后的更新不在该条记录中，可在本次处理结束后再导出。
旧版本没有采集的字段不会补造。原始截图通常只覆盖当时可见窗口，输入框内容也可能不完整。
没有连续屏幕上下文、未触发语音的对照窗口和完整的后续手工修订，不能仅凭此数据推断因果关系。
"""


class ExportCancelled(Exception):
    pass


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ExportCancelled("导出已取消")


def _object(value):
    return value if isinstance(value, dict) else {}


def summary_row(record, files):
    mode, target, asr, audio, capture, context, prepared, evidence, outcome = (
        _object(record.get(key)) for key in
        ("mode", "target", "asr", "audio", "screen_capture", "input_context", "context_preparation", "screen_evidence", "outcome"))
    requests = [r for r in _object(record.get("llm")).get("requests", []) if isinstance(r, dict)]
    has_original = "edit_source_text" in context or "original" in context
    return {
        "interaction_id": record.get("interaction_id"), "schema_version": record.get("schema_version"),
        "created_at": record.get("created_at"), "updated_at": record.get("updated_at"),
        "application": target.get("application") or context.get("application") or capture.get("application"),
        "routing_mode": mode.get("routing"), "requested_mode": mode.get("requested_mode"),
        "selected_mode": mode.get("selected"), "predicted_mode": mode.get("predicted"),
        "final_applied_mode": mode.get("final_applied"), "user_corrected": mode.get("user_corrected"),
        "task_type": mode.get("task_type"), "edit_tasks": _object(mode.get("edit_plan")).get("tasks"),
        "audio_duration_ms": audio.get("duration_ms"), "asr_text": asr.get("final_text"),
        "asr_model": asr.get("model"), "asr_context_status": _object(asr.get("context")).get("status"),
        "input_context_status": context.get("status"), "context_complete": context.get("context_complete"),
        "context_scope": context.get("context_scope"), "editable_text_present": has_original,
        "editable_text": context.get("edit_source_text", context.get("original")),
        "screen_status": capture.get("status"), "captured_at": evidence.get("captured_at"),
        "ocr_line_count": len(evidence["ocr_lines"]) if isinstance(evidence.get("ocr_lines"), list) else None,
        "context_enabled": prepared.get("enabled"), "context_preparation_status": prepared.get("status"),
        "prepared_context": prepared.get("context"), "llm_request_count": len(requests),
        "actual_model_input_count": sum(isinstance(r.get("request_input"), dict) for r in requests),
        "outcome_status": outcome.get("status"), "final_text": outcome.get("final_text"),
        "accepted": outcome.get("accepted"), "acceptance_strength": outcome.get("acceptance_strength"),
        "audio_in_archive": audio.get("file", "audio.wav") in files,
        "screenshot_in_archive": evidence.get("screenshot_file") in files,
        "record_path": f"interactions/{record.get('interaction_id')}/record.json",
    }


def _csv_value(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _open_regular(stack, path):
    if path.is_symlink():
        raise ValueError("symbolic_link")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not_regular_file")
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    return stack.enter_context(stream)


@contextmanager
def _record_snapshot(store, identity):
    # Writers atomically replace these files. Opening them under the collector
    # lock pins one version; compression then streams from those open handles.
    with ExitStack() as stack:
        with store._lock:
            directory = store.interactions_root / identity
            if (identity in store._deleted_interactions or store.user_root.is_symlink()
                    or store.interactions_root.is_symlink() or directory.is_symlink()):
                raise ValueError("deleted_or_symbolic_link")
            raw = _open_regular(stack, directory / "record.json").read()
            record = json.loads(raw)
            if not isinstance(record, dict) or record.get("interaction_id") != identity:
                raise ValueError("invalid_record_identity")
            names = {record.get("events_file", "events.jsonl"): True,
                     _object(record.get("asr")).get("updates_file", "asr_updates.jsonl"): True}
            audio = _object(record.get("audio"))
            names[audio.get("file", "audio.wav")] = bool(audio.get("available"))
            for section, field in (("imu", "samples_file"), ("screen_evidence", "screenshot_file")):
                name = _object(record.get(section)).get(field)
                if name:
                    names[name] = True
            files, warnings = {}, []
            for name, expected in names.items():
                if not isinstance(name, str) or Path(name).name != name or name in {".", "..", "record.json"} or "\\" in name:
                    warnings.append(dict(interaction_id=identity, file=str(name), reason="invalid_attachment_name"))
                    continue
                try:
                    files[name] = _open_regular(stack, directory / name)
                except (OSError, ValueError) as exc:
                    if expected or not isinstance(exc, FileNotFoundError):
                        warnings.append(dict(interaction_id=identity, file=name, reason=type(exc).__name__))
        yield record, raw, files, warnings


def export_history(store, destination, *, on_progress=None, cancel=None):
    """Export all stored interactions, independent of UI filters and row limits."""
    started = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    with store._lock:
        if store.user_root.is_symlink() or store.interactions_root.is_symlink():
            raise ValueError("语音历史目录不可用")
        identities = sorted(p.name for p in store.interactions_root.iterdir()
            if _CURRENT_INTERACTION_ID.fullmatch(p.name) and p.name not in store._deleted_interactions) if store.interactions_root.exists() else []
        with ExitStack() as stack:
            try:
                associations = _open_regular(stack, store.association_index_path).read()
            except FileNotFoundError:
                associations = b""
    if not identities:
        raise ValueError("暂无可导出的语音历史")
    _check_cancel(cancel)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    name = f"Mythlink-voice-history-{datetime.now().astimezone():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.zip"
    path = destination / name
    manifest = dict(format="mythlink-voice-history", export_version=1, started_at=started,
                    snapshot_policy="per_record_locked", source_record_count=len(identities),
                    record_count=0, schema_versions=Counter(), routing_modes=Counter(),
                    context_coverage=Counter(), skipped_records=[], missing_files=[], files=[])
    completed = set()
    with tempfile.TemporaryDirectory(prefix=".mythlink-export-", dir=destination) as temporary:
        temporary = Path(temporary)
        archive = temporary / name
        with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=3, allowZip64=True) as output, \
                (temporary / "records.jsonl").open("w", encoding="utf-8") as records_file, \
                (temporary / "summary.csv").open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=list(summary_row({}, set())))
            writer.writeheader()
            for index, identity in enumerate(identities):
                _check_cancel(cancel)
                with ExitStack() as snapshot:
                    try:
                        record, raw, files, warnings = snapshot.enter_context(_record_snapshot(store, identity))
                        row = summary_row(record, files)
                    except (OSError, ValueError, TypeError, KeyError) as exc:
                        manifest["skipped_records"].append(dict(interaction_id=identity, reason=type(exc).__name__))
                    else:
                        base = f"interactions/{identity}/"
                        output.writestr(base + "record.json", raw)
                        manifest["files"].append(dict(path=base + "record.json", size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
                        for filename, stream in files.items():
                            digest, size = hashlib.sha256(), 0
                            with output.open(base + filename, "w", force_zip64=True) as target:
                                while chunk := stream.read(1024 * 1024):
                                    _check_cancel(cancel)
                                    target.write(chunk)
                                    digest.update(chunk)
                                    size += len(chunk)
                            manifest["files"].append(dict(path=base + filename, size=size, sha256=digest.hexdigest()))
                        manifest["missing_files"].extend(warnings)
                        writer.writerow({key: _csv_value(value) for key, value in row.items()})
                        records_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                        completed.add(identity)
                        manifest["record_count"] += 1
                        manifest["schema_versions"][str(record.get("schema_version", "unknown"))] += 1
                        manifest["routing_modes"][row["routing_mode"] or "unknown"] += 1
                        for key, value in {
                            "input_context_recorded": bool(record.get("input_context")),
                            "editable_text_present": row["editable_text_present"],
                            "context_complete_true": row["context_complete"] is True,
                            "screenshot_in_archive": row["screenshot_in_archive"],
                            "raw_ocr_recorded": row["ocr_line_count"] is not None,
                            "prepared_context_ok": row["context_preparation_status"] == "ok",
                            "actual_model_input_recorded": row["actual_model_input_count"] > 0,
                        }.items():
                            manifest["context_coverage"][key] += bool(value)
                if on_progress:
                    on_progress(index + 1, len(identities))
            if not completed:
                raise ValueError("没有可读取的语音历史，导出未生成")
            records_file.flush()
            csv_file.flush()
            output.write(temporary / "records.jsonl", "records.jsonl")
            output.write(temporary / "summary.csv", "summary.csv")
            output.writestr("associations.jsonl", associations)
            # Keep the raw index; report links outside this export instead of
            # silently altering manually supplied research labels.
            references = set()
            for line in associations.splitlines():
                try:
                    row = json.loads(line)
                    members = [_object(row.get("chosen")), *row.get("rejected", [])]
                    references.update(m["interaction_id"] for m in members if isinstance(m, dict) and m.get("interaction_id"))
                except (ValueError, TypeError, AttributeError):
                    manifest["association_parse_errors"] = manifest.get("association_parse_errors", 0) + 1
            manifest["association_missing_interaction_ids"] = sorted(references - completed)
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            output.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            output.writestr("README.txt", README)
        _check_cancel(cancel)
        # Publish only a complete ZIP, and never replace an existing export.
        os.link(archive, path)
    return dict(path=str(path), record_count=manifest["record_count"],
                skipped_count=len(manifest["skipped_records"]), missing_file_count=len(manifest["missing_files"]))
