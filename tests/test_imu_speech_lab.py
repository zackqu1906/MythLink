"""Numerical and capture invariants for the standalone experiment, no hardware."""
import json
import struct
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from tools.imu_speech_lab.analysis import correlation, features, packet_rate, quality, spectrum, summarize
from tools.imu_speech_lab.app import Config, DeviceClock, Lab, Recorder, imu_response_error, make_report, plot_rows, review_state


def signal_data(seconds=10, hz=200, frequency=31):
    t = np.arange(int(seconds * hz)) / hz
    imu = np.zeros((len(t), 17))
    imu[:, 0:3] = t[:, None]
    imu[:, 3] = np.arange(len(t))
    imu[:, 4] = np.arange(len(t)) // 10
    imu[:, 5] = np.sin(2 * np.pi * frequency * t)
    imu[:, 7] = 9.80665
    imu[:, 8] = 3 * np.sin(2 * np.pi * 17 * t)
    at = np.arange(int(seconds * 50)) * .02 + .01
    audio = np.zeros((len(at), 7))
    audio[:, 0:2] = at[:, None]
    audio[:, 2] = np.arange(len(at)) * 320
    audio[:, 3] = 320
    audio[:, 4] = .05
    return imu, audio


def test_psd_frequency_and_physical_power():
    imu, _ = signal_data()
    s = spectrum(imu, 200)
    assert s["acc_peak_hz"] == pytest.approx(31, abs=.3)
    assert s["gyro_peak_hz"] == pytest.approx(17, abs=.3)
    df = s["hz"][1]
    assert np.sum(10 ** (np.asarray(s["acc_db"]) / 10)) * df == pytest.approx(.5, rel=.01)
    assert s["hz"][-1] == 100


def test_packet_endpoints_detect_wrong_firmware_rate():
    imu, _ = signal_data()
    # SDK fills each packet at 200 Hz, but packet endpoints advance at 100 Hz.
    imu[:, 2] += (imu[:, 3] // 10) * .05
    assert packet_rate(imu) == pytest.approx(100)
    assert not quality(imu, 200)["timing_ok"]


def test_packet_sequence_rollover_and_loss_do_not_fake_rate():
    imu, _ = signal_data()
    imu[:, 4] = (imu[:, 4] + 65530) % 65536
    imu = np.delete(imu, np.arange(100, 110), axis=0)
    assert packet_rate(imu) == pytest.approx(200)
    assert quality(imu, 200)["gaps"] == 1


def test_spectrum_does_not_bridge_dropouts():
    imu, _ = signal_data()
    imu = np.delete(imu, np.arange(1600, 1800), axis=0)
    assert spectrum(imu, 200) == {}  # only one second remains after the gap


def test_features_remove_gravity_and_reject_bad_audio():
    imu, audio = signal_data()
    imu[:, 5] = 0
    f = features(imu, audio, 200)
    assert np.max(f[:, 1]) < 1e-12
    assert np.median(f[:, 3]) == pytest.approx(.05)
    audio[:, 6] = 1  # overflow/underflow: never treat as reliable correlation
    assert np.all(np.isnan(features(imu, audio, 200)[:, 3]))
    assert correlation(f) is None


def test_envelope_delay_sign_and_missing_bins():
    rng = np.random.default_rng(4)
    energy = rng.uniform(.01, 1, 300)
    f = np.zeros((300, 4))
    f[:, 0] = np.arange(300) * .2 + .1
    f[:, 3] = energy
    f[2:, 1] = energy[:-2]  # IMU is 400 ms later
    f = np.delete(f, np.arange(60, 90), axis=0)
    result = correlation(f)
    assert result["lag_s"] == pytest.approx(.4)
    assert result["r"] > .999


def test_conditions_exclude_transitions_and_report_baseline():
    imu, audio = signal_data(seconds=20)
    imu[imu[:, 0] >= 10, 5] *= 4
    summary = summarize(imu, audio, [{"t": 0, "label": "silence"}, {"t": 10, "label": "self_speech"}], 200)
    speaking = next(s for s in summary["conditions"] if s["label"] == "self_speech")
    assert speaking["acc_ratio"] == pytest.approx(4, rel=.01)
    assert 7 < speaking["seconds"] <= 8
    assert all(s["usable_windows"] <= 40 for s in summary["segments"])


def test_display_keeps_short_spikes():
    imu, _ = signal_data()
    imu[1229, 6] = 10
    assert np.max(np.asarray(plot_rows(imu))[:, 2]) == 10


def test_recorder_unwraps_uptime_and_rejects_reset(tmp_path):
    r = Recorder(tmp_path / "rollover", Config(mic="none"))
    sample = dict(sample_index=1, packet_seq=1, accel_ms2=(0, 0, 9.8), gyro_dps=(0, 0, 0), raw=(0, 0, 16384, 0, 0, 0))
    r.on_imu(SimpleNamespace(**sample, uptime_ms=2**32-5))
    r.on_imu(SimpleNamespace(**sample, uptime_ms=0))
    assert r.device_seconds == pytest.approx(.005)
    r.on_imu(SimpleNamespace(**sample, uptime_ms=0))
    assert "设备时间" in r.error
    r.close()
    assert r.count == 2


def test_raw_sdk_packet_reaches_recorder(tmp_path):
    from ring_python_sdk.imu.processor import ImuProcessor
    r = Recorder(tmp_path / "sdk", Config(mic="none"))
    decoder = ImuProcessor(None, None, False, 250, 2, 200, 200, 10, on_sample=r.on_imu)
    payload = bytes([0x21, 0x02]) + struct.pack("<HBI", 1, 10, 1000)
    payload += struct.pack("<6h", 12, -34, 16384, 2, -3, 4) * 10
    decoder.handle_notification(None, bytearray(payload))
    r.close(); decoder.close()
    assert r.count == 10
    arr = np.loadtxt(r.path / "imu.csv", delimiter=",", skiprows=1)
    assert arr[-1, 2] - arr[0, 2] == pytest.approx(.045)
    np.testing.assert_array_equal(arr[0, 11:], [12, -34, 16384, 2, -3, 4])


def test_demo_lifecycle_and_offline_report(tmp_path):
    lab = Lab(tmp_path)
    lab.start(Config(demo=True))
    deadline = time.monotonic() + 4
    while (lab.recorder is None or lab.recorder.count < 410) and time.monotonic() < deadline:
        time.sleep(.05)
    assert lab.recorder is not None
    lab.recorder.mark("silence", "test")
    lab.stop.set()
    lab.thread.join(timeout=5)
    assert not lab.thread.is_alive()
    assert lab.status == "已保存", lab.error
    snapshot = lab.snapshot()
    assert snapshot["demo"] is True
    assert snapshot["quality"]["timing_ok"]
    p = lab.recorder.path
    assert json.loads((p / "metadata.json").read_text())["synthetic"] is True
    assert (p / "report.html").exists()
    assert json.loads((p / 'speech_segments.json').read_text())['method'] == 'webrtcvad'
    assert 'speechSelect' in (p / 'report.html').read_text()
    assert "/*BOOTSTRAP*/null" not in (p / "report.html").read_text()
    assert "NaN" not in (p / "summary.json").read_text()
    with wave.open(str(p / "audio.wav")) as w:
        assert w.getnframes() == lab.recorder.audio_count
        assert w.getframerate() == 16000
    assert make_report(p)["quality"]["timing_ok"]


@pytest.mark.parametrize("data", [{"hz": 0}, {"demo": "false"}, {"chip": "unknown"}, {"frames_per_packet": 100}, {"name": ""}])
def test_reject_invalid_config(data):
    with pytest.raises(ValueError):
        Config.parse(data)


def test_firmware_control_response_is_not_silent_data_loss():
    assert "21 09 01" in imu_response_error(bytes.fromhex("21 09 01"))
    assert "token" in imu_response_error(bytes.fromhex("21 05 01 00"))
    assert not imu_response_error(bytes.fromhex("21 02 01 00"))
    assert not imu_response_error(bytes.fromhex("21 09 00"))


def test_unlabeled_period_is_not_a_condition():
    imu, audio = signal_data()
    assert summarize(imu, audio, [{"t": 0, "label": "unlabeled"}], 200)["conditions"] == []


def test_project_token_parity_and_units(tmp_path):
    from ring_python_sdk.imu.processor import ImuProcessor
    from ring_python_sdk.touchpad.core import START, parse_tokens
    assert START.hex() == "2100c800c800d007100a01"
    config = Config(stream="token", accel_fs=16, gyro_fs=2000, mic="none")
    r = Recorder(tmp_path / "token", config)
    decoder = ImuProcessor(None, None, False, 2000, 16, 200, 200, 10,
                           encode_mode="token", on_sample=r.on_imu)
    values = [.1, -.2, .3, -.4, .5, -.6]
    packet = b"\x21\x05" + struct.pack("<HBIB", 12, 10, 1000, 1) + struct.pack("<6e", *values) * 10
    _, _, expected = parse_tokens(packet)
    decoder.handle_notification(None, bytearray(packet))
    r.close(); decoder.close()
    arr = np.loadtxt(r.path / "imu.csv", delimiter=",", skiprows=1)
    np.testing.assert_array_equal(arr[:, 5:11], expected)
    assert np.isnan(arr[:, 11:]).all()  # never fabricate raw ADC measurements
    assert "t1,t2,t3,t4,t5,t6" in (r.path / "imu.csv").read_text().splitlines()[0]
    assert "t1_t6" in json.loads((r.path / "metadata.json").read_text())["units"]
    assert not imu_response_error(packet, "token")


def test_new_captures_require_supplied_raw_configuration():
    with pytest.raises(ValueError):
        Config.parse({"stream": "token", "hz": 1000})
    for change in ({'stream': 'token'}, {'hz': 1000}, {'accel_fs': 2}, {'gyro_fs': 250}, {'chip': 'icm45686'}):
        with pytest.raises(ValueError):
            Config.parse(change)
    config = Config.parse({})
    assert (config.stream, config.hz, config.accel_fs, config.gyro_fs, config.mic) == ('raw', 200, 16, 2000, 'ring')


def test_ring_clock_shares_anchor_across_streams_and_wrap():
    clock = DeviceClock()
    assert clock.map(2**32-50, 1.0) == 1.0
    # Different BLE arrival times cannot move the shared device clock.
    assert clock.map(0, 1.3) == pytest.approx(1.05)
    assert clock.map(25, 1.1) == pytest.approx(1.075)


def test_ring_pcm_roundtrip_and_real_gap(tmp_path):
    from ring_python_sdk.public_protocol import AudioFrame
    r = Recorder(tmp_path / 'ring', Config())
    pcm = np.tile(np.array([-32768, -1, 0, 1, 32767], dtype='<i2'), 320)
    r.on_ring_audio(AudioFrame(65535, 2**32-100, pcm.tobytes()))
    r.on_ring_audio(AudioFrame(1, 100, pcm.tobytes()))  # one 100 ms block missing
    r.close()
    a = np.loadtxt(r.path / 'audio.csv', delimiter=',', skiprows=1)
    assert len(a) == 10
    assert a[5, 0] - a[4, 0] == pytest.approx(.12)
    assert r.audio_missing_blocks == 1
    assert r.audio_health()['ready']
    with wave.open(str(r.path / 'audio.wav')) as wav:
        np.testing.assert_array_equal(np.frombuffer(wav.readframes(3200), dtype='<i2'), np.tile(pcm, 2))
    blocks = np.loadtxt(r.path / 'ring_audio_blocks.csv', delimiter=',', skiprows=1)
    assert blocks[0, 2] == 2**32-100
    assert blocks[1, 2] == 100


def test_ring_audio_and_imu_same_uptime_align(tmp_path):
    from ring_python_sdk.public_protocol import AudioFrame
    r = Recorder(tmp_path / 'alignment', Config())
    r.on_ring_audio(AudioFrame(0, 1000, np.ones(1600, dtype='<i2').tobytes()))
    sample = SimpleNamespace(uptime_ms=1000, sample_index=1, packet_seq=1,
        accel_ms2=(0,0,0), gyro_dps=(0,0,0), raw=(0,)*6)
    r.on_imu(sample)
    r.close()
    a = np.loadtxt(r.path / 'audio.csv', delimiter=',', skiprows=1)
    imu = np.loadtxt(r.path / 'imu.csv', delimiter=',', skiprows=1, ndmin=2)
    assert a[-1, 0] + .01 == pytest.approx(imu[0, 0])


def test_zero_audio_does_not_pass_health_and_computer_input_rejected(tmp_path):
    from ring_python_sdk.public_protocol import AudioFrame
    for source in ['default', '0', 'computer']:
        with pytest.raises(ValueError):
            Config.parse({'mic': source})
    r = Recorder(tmp_path / 'zero', Config())
    for n in range(20):
        r.on_ring_audio(AudioFrame(n, n*100, bytes(3200)))
    r.close()
    assert not r.audio_health()['ready']
    assert r.audio_health()['zero_run_s'] == 2


def test_review_keeps_full_session_and_earliest_samples(tmp_path):
    r = Recorder(tmp_path / 'full', Config(demo=True))
    imu, audio = signal_data(seconds=65)
    for row in imu:
        r.submit('imu', row.tolist())
        if r.q.qsize() > 1000:
            time.sleep(.01)
    r.close()
    summary = make_report(r.path)
    state = review_state(r.path, summary)
    assert state['review']
    assert len(state['imu']) == 13000
    assert state['imu'][0][0] == 0
    assert state['imu'][-1][0] > 64
    html = (r.path / 'report.html').read_text()
    assert '/*DASHBOARD_SCRIPT*/' not in html
    assert 'clampRange' in html


def raw_packet(seq, uptime, values=(2048, 0, 0, 164, -164, 0)):
    return b'\x21\x02' + struct.pack('<HBI', seq, 10, uptime) + struct.pack('<6h', *values) * 10


def test_new_sdk_raw_units_loss_wrap_and_saved_integers(tmp_path):
    from tools.imu_speech_lab.raw_sdk import RawSession, IMU_START
    r = Recorder(tmp_path / 'new_sdk', Config(mic='none'))
    session = RawSession(r.on_imu, r.on_ring_audio, r.on_wire)
    assert IMU_START.hex() == '2100c800c800d007100a0000'
    session.accept(raw_packet(65535, 1000))
    session.accept(raw_packet(0, 1050))
    session.accept(raw_packet(0, 1050))  # Duplicate must not create samples.
    session.accept(raw_packet(2, 1150))  # One missing packet.
    r.close()
    rows = np.loadtxt(r.path / 'imu.csv', delimiter=',', skiprows=1)
    assert not session.error
    assert session.stats['sample_count'] == 30
    assert session.stats['estimated_lost_samples'] == 10
    assert session.stats['dropped_packet_count'] == 1
    np.testing.assert_array_equal(rows[:, 11:], np.tile([2048, 0, 0, 164, -164, 0], (30, 1)))
    np.testing.assert_allclose(rows[:, 5], 9.80665)
    np.testing.assert_allclose(rows[:, 8:11], np.tile([10, -10, 0], (30, 1)))
    assert rows[-1, 3] == 39
    assert rows[-1, 2] == pytest.approx(.195)
    assert len((r.path / 'ble_packets.jsonl').read_text().splitlines()) == 4


def audio_fragments(seq, uptime, predictor=100):
    block = struct.pack('<hBBH', predictor, 0, 0, 1600) + bytes(800)
    chunks = [block[:200], block[200:600], block[600:]]
    return [b'\x20\x03' + struct.pack('<HHHI', seq, i, 3, uptime) + chunk for i, chunk in enumerate(chunks)]


def test_new_sdk_fragmented_audio_shares_raw_clock_and_preserves_gap(tmp_path):
    from tools.imu_speech_lab.raw_sdk import RawSession
    r = Recorder(tmp_path / 'new_audio', Config())
    session = RawSession(r.on_imu, r.on_ring_audio, r.on_wire)
    session.mic_enabled = True
    session.accept(raw_packet(0, 1000))
    first = audio_fragments(0, 1000)
    for fragment in [first[2], first[1], first[1], first[0]]:
        session.accept(fragment)
    for fragment in audio_fragments(2, 1200, 200):  # Missing seq 1.
        session.accept(fragment)
    session.drain(force=True)
    r.close()
    assert not session.error and not r.error
    assert r.audio_blocks == 2 and r.audio_missing_blocks == 1
    assert r.audio_health()['ready']
    imu = np.loadtxt(r.path / 'imu.csv', delimiter=',', skiprows=1)
    audio = np.loadtxt(r.path / 'audio.csv', delimiter=',', skiprows=1)
    assert audio[4, 0] + .01 == pytest.approx(imu[-1, 0])
    assert audio[5, 0] - audio[4, 0] == pytest.approx(.12)
    with wave.open(str(r.path / 'audio.wav')) as wav:
        pcm = np.frombuffer(wav.readframes(3200), dtype='<i2')
    np.testing.assert_array_equal(pcm[:1600], np.full(1600, 100))
    np.testing.assert_array_equal(pcm[1600:], np.full(1600, 200))
    assert session.audio_stats['gap_events'] == 1


def test_new_sdk_rejects_tokens_and_conflicting_microphone_fragments():
    from tools.imu_speech_lab.raw_sdk import RawSession
    samples = []
    session = RawSession(samples.append, lambda _: None, lambda *_: None)
    session.accept(b'\x21\x05' + bytes(128))
    assert 'token' in session.error and not samples
    session = RawSession(samples.append, lambda _: None, lambda *_: None)
    session.mic_enabled = True
    fragment = audio_fragments(0, 1000)[0]
    session.accept(fragment)
    session.accept(fragment[:-1] + b'\x01')
    assert '分片冲突' in session.error
    assert session.audio_stats['invalid_packets'] == 1


def test_packet_rate_averages_rounded_packet_timestamps():
    imu, _ = signal_data()
    ends = np.round(np.arange(1, 201) * (10000 / 202.8)) / 1000
    imu[:, 2] = np.repeat(ends, 10) - np.tile(np.arange(9, -1, -1) / 200, 200)
    assert packet_rate(imu) == pytest.approx(202.8, abs=.03)


def test_speech_segments_keep_pauses_and_never_bridge_missing_frames():
    from tools.imu_speech_lab.speech import group_speech_frames
    # A real 100 ms pause may join speech, but a 100 ms missing interval may not.
    frames = [(i*.02, (i+1)*.02, i < 10 or i >= 15) for i in range(25)]
    joined = group_speech_frames(frames)
    assert len(joined) == 1
    assert joined[0]['voiced_seconds'] == pytest.approx(.4)
    missing = group_speech_frames(frames[:10] + frames[15:])
    assert len(missing) == 2
    assert missing[0]['end'] == pytest.approx(.2)
    assert missing[1]['start'] == pytest.approx(.3)
    assert group_speech_frames(frames[:4]) == []  # transient under 180 ms


def test_speech_uses_wav_offsets_and_device_times_not_wall_clock(tmp_path, monkeypatch):
    import sys
    from tools.imu_speech_lab.speech import detect_speech
    class FakeVad:
        def __init__(self, mode):
            assert mode == 2
        def is_speech(self, frame, rate):
            assert len(frame) == 640 and rate == 16000
            return True
    monkeypatch.setitem(sys.modules, 'webrtcvad', SimpleNamespace(Vad=FakeVad))
    with wave.open(str(tmp_path/'audio.wav'), 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'NONE'))
        wav.writeframes(np.full(6400, 3277, dtype='<i2').tobytes())
    _, audio = signal_data(seconds=.4)
    audio[:, 0] += 7.3  # shared device time, unrelated to WAV index zero
    audio[10:, 0] += .1  # missing block: do not extend/merge across it
    speech = detect_speech(tmp_path, audio)
    assert speech['status'] == 'ready'
    assert len(speech['segments']) == 2
    assert speech['segments'][0]['start'] == pytest.approx(7.3)
    assert speech['segments'][0]['end'] == pytest.approx(7.5)
    assert speech['segments'][1]['start'] == pytest.approx(7.6)
    # All-zero and status-flagged audio are never speech, even if VAD returns True.
    audio[:, 4] = 0
    assert detect_speech(tmp_path, audio)['segments'] == []
    audio[:, 4] = .05
    audio[:, 6] = 1
    assert detect_speech(tmp_path, audio)['segments'] == []
    assert detect_speech(tmp_path, audio, 'computer')['status'] == 'unavailable'


def test_speech_missing_dependency_is_explicit(tmp_path, monkeypatch):
    import sys
    from tools.imu_speech_lab.speech import detect_speech
    monkeypatch.setitem(sys.modules, 'webrtcvad', None)
    _, audio = signal_data(seconds=1)
    result = detect_speech(tmp_path, audio)
    assert result['status'] == 'unavailable'
    assert 'webrtcvad-wheels' in result['reason']
    assert result['segments'] == []


def test_real_vad_does_not_label_digital_silence(tmp_path):
    pytest.importorskip('webrtcvad')
    from tools.imu_speech_lab.speech import detect_speech
    with wave.open(str(tmp_path/'audio.wav'), 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'NONE'))
        wav.writeframes(bytes(32000))
    _, audio = signal_data(seconds=1)
    audio[:, 4] = 0
    assert detect_speech(tmp_path, audio)['segments'] == []
