import json
import logging
import struct
import wave
import importlib.util
from pathlib import Path

from ring_python_sdk.audio.processor import AudioProcessor
from ring_python_sdk.public_protocol import MicBlockAssembler, decode_adpcm

_spec = importlib.util.spec_from_file_location("trace_verifier", Path(__file__).parents[1] / "tools/verify_ring_audio_trace.py")
_verifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_verifier)


def packet(sequence, predictor=0, index=0, payload=None):
    block = struct.pack("<hBBH", predictor, index, 0, 1600) + (bytes(800) if payload is None else payload)
    return b"\x20\x03" + struct.pack("<HHHI", sequence & 65535, 0, 1, sequence * 100) + block


def events(caplog):
    return [json.loads(record.getMessage().split("] ", 1)[1])
            for record in caplog.records if "[RING AUDIO HEALTH]" in record.getMessage()]


def test_zero_pcm_logs_encoded_evidence_and_recovery_without_changing_audio(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    now = [10.0]
    monkeypatch.setattr("ring_python_sdk.audio.diagnostics.time.monotonic", lambda: now[0])
    callbacks = []
    output = tmp_path / "mic.wav"
    processor = AudioProcessor(output, on_pcm=lambda seq, pcm: callbacks.append(pcm))
    for seq in range(19):
        processor.handle_notification(None, packet(seq))
    assert [row["event"] for row in events(caplog)] == ["first_block"]
    now[0] += 2
    processor.handle_notification(None, packet(19))
    onset = events(caplog)[-1]
    assert onset["event"] == "zero_pcm_started"
    assert (onset["zero_audio_s"], onset["zero_start_sequence"], onset["sequence"]) == (2.0, 0, 19)
    assert onset["pcm_peak"] == onset["pcm_rms"] == onset["step_index"] == 0
    assert onset["nibble_histogram"] == [1599] + [0] * 15
    assert decode_adpcm(bytes.fromhex(onset["encoded_hex"])) == bytes(3200)
    assert _verifier.verify(onset)["status"] == "verified"
    for seq in range(20, 60):
        processor.handle_notification(None, packet(seq))
    assert len(events(caplog)) == 2  # Burst/backlog cannot flood the log.
    now[0] += 30
    processor.handle_notification(None, packet(60))
    processor.handle_notification(None, packet(61, predictor=1))
    processor.close()
    rows = events(caplog)
    assert [row["event"] for row in rows][-2:] == ["zero_pcm_continues", "zero_pcm_recovered"]
    assert rows[-1]["zero_audio_s"] == 6.1
    assert rows[-1]["pcm_peak"] == rows[-1]["pcm_rms"] == 1
    assert "encoded_hex" not in rows[-1]
    with wave.open(str(output)) as wav:
        assert wav.readframes(wav.getnframes()) == b"".join(callbacks)
    assert len(callbacks) == 62
    assert processor._audio_diagnostics.last is None


def test_raw_evidence_is_capped_and_close_reports_unrecovered_stream(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    now = [0.0]
    monkeypatch.setattr("ring_python_sdk.audio.diagnostics.time.monotonic", lambda: now[0])
    processor = AudioProcessor(tmp_path / "mic.wav")
    for seq in range(30):
        now[0] += 30
        processor.handle_notification(None, packet(seq))
    processor.close()
    processor.close()
    rows = events(caplog)
    assert sum("encoded_hex" in row for row in rows) == 3
    assert sum("raw_notifications_hex" in row for row in rows) == 3
    assert rows[-1]["event"] == "zero_pcm_at_close"
    assert rows[-1]["zero_audio_s"] == 3
    assert len([row for row in rows if row["event"] == "zero_pcm_at_close"]) == 1


def test_nonzero_encoded_sign_bits_can_still_decode_to_digital_silence(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    processor = AudioProcessor(tmp_path / "mic.wav")
    for seq in range(20):
        processor.handle_notification(None, packet(seq, payload=b"\x88" * 800))
    processor.close()
    row = events(caplog)[1]
    assert row["pcm_all_zero"]
    assert row["nibble_histogram"][8] == 1599
    assert decode_adpcm(bytes.fromhex(row["encoded_hex"])) == bytes(3200)


def test_quiet_signal_and_log_failure_do_not_interrupt_pcm_delivery(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    processor = AudioProcessor(tmp_path / "mic.wav")
    for seq in range(50):
        processor.handle_notification(None, packet(seq, predictor=1))
    assert [row["event"] for row in events(caplog)] == ["first_block"]
    processor.close()

    def broken_log(*args, **kwargs):
        raise OSError("log unavailable")

    monkeypatch.setattr("ring_python_sdk.audio.diagnostics._LOG.info", broken_log)
    callbacks = []
    processor = AudioProcessor(tmp_path / "next.wav", on_pcm=lambda *args: callbacks.append(args))
    for seq in range(30):
        processor.handle_notification(None, packet(seq))
    processor.close()
    assert len(callbacks) == 30


def test_encoded_evidence_is_only_available_for_current_valid_completed_block():
    assembler = MicBlockAssembler()
    original = packet(65535)
    assert assembler.accept(original) is not None
    assert assembler.last_decoded_block == original[12:]
    assert assembler.accept(original) is None
    assert assembler.last_decoded_block is None
    assert assembler.accept(packet(65536)) is not None
    assert assembler.accept(b"invalid") is None
    assert assembler.last_decoded_block is None
    assert assembler.accept(packet(65537, index=89)) is None
    assert assembler.last_decoded_block is None
    assembler.reset()
    assert assembler.last_decoded_block is None


def test_raw_notifications_preserve_out_of_order_fragments_and_duplicates(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    processor = AudioProcessor(tmp_path / "mic.wav")
    for seq in range(20):
        block = packet(seq)[12:]
        fragments = [b"\x20\x03" + struct.pack("<HHHI", seq, i, 2, seq * 100) + data
                     for i, data in enumerate((block[:401], block[401:]))]
        order = [fragments[1], fragments[1], fragments[0]]
        for raw in order:
            processor.handle_notification(None, bytearray(raw))
    onset = events(caplog)[-1]
    assert onset["raw_notifications_hex"] == [p.hex() for p in order]
    assert _verifier.verify(onset)["status"] == "verified"
    # Independent audit detects damaged/incomplete data instead of filling it.
    assert _verifier.verify({**onset, "raw_notifications_hex": [order[0].hex()]})["status"] == "incomplete_raw_trace"
    changed = bytearray(order[0]); changed[-1] = 1
    assert _verifier.verify({**onset, "raw_notifications_hex": [changed.hex(), *onset["raw_notifications_hex"]]})["status"] == "invalid_evidence"
    assert _verifier.verify({**onset, "encoded_hex": "00" * 806})["status"] == "mismatch"
    assert _verifier.verify({**onset, "pcm_sha256": "wrong"})["status"] == "mismatch"
    processor.close()
    assert not processor._audio_diagnostics._raw_notifications


def test_raw_trace_is_bounded_and_legacy_logs_are_not_marked_verified(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    processor = AudioProcessor(tmp_path / "mic.wav")
    for seq in range(20):
        processor.handle_notification(None, packet(seq))
    onset = events(caplog)[-1]
    legacy = {k: v for k, v in onset.items() if k != "raw_notifications_hex"}
    assert _verifier.verify(legacy)["status"] == "missing_raw_notifications"
    for seq in range(100):
        processor._audio_diagnostics.record_notification(packet(seq))
    assert len(processor._audio_diagnostics._raw_notifications) == 32
    processor._audio_diagnostics.record_notification(b"\x20\x03" + bytes(100_000))
    assert max(map(len, processor._audio_diagnostics._raw_notifications)) <= 818
    processor.close()
