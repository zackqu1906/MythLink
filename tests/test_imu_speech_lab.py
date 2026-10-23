"""Numerical and capture invariants for the standalone experiment, no hardware."""
import json
import struct
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from tools.imu_speech_lab.analysis import correlation, features, packet_rate, quality, spectrum, summarize
from tools.imu_speech_lab.app import Config, Lab, Recorder, imu_response_error, make_report, plot_rows


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


def test_token_mode_matches_project_start_parameters():
    with pytest.raises(ValueError):
        Config.parse({"stream": "token", "hz": 1000})
    assert Config.parse({"stream": "token", "accel_fs": 16, "gyro_fs": 2000}).hz == 200
