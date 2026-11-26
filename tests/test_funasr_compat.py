from __future__ import annotations

import importlib
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize("finish", [False, True])
def test_sensevoice_progress_counts_resumed_bytes_once_and_closes(monkeypatch, tmp_path, finish):
    from proximic_ring.asr import funasr_compat as compat
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path))
    snapshot = tmp_path / "models/iic--SenseVoiceSmall/snapshots/master"
    snapshot.mkdir(parents=True)
    for name in compat._SENSEVOICE_FILES:
        if name != "model.pt":
            (snapshot / name).touch()
    partial = snapshot / "model.pt.incomplete"
    partial.write_bytes(bytes(20))
    ticks = iter(range(1, 20))
    monkeypatch.setattr(compat, "time", type("Clock", (), {"monotonic": lambda: next(ticks)}))
    events = []
    def transfer(model_id, *, progress_callbacks, **options):
        callback = progress_callbacks[0]("model.pt", 100)
        callback.update(20)
        callback.update(20)  # Hub repeats the saved offset when retrying.
        partial.write_bytes(bytes(55))
        callback.update(35)
        if finish:
            partial.write_bytes(bytes(100))
            partial.rename(snapshot / "model.pt")
            callback.end()
        return str(snapshot)
    hub = importlib.import_module("modelscope_hub.compat.snapshot_download")
    monkeypatch.setattr(hub, "snapshot_download", transfer)
    if finish:
        assert compat.download_sensevoice(events.append) == str(snapshot)
        assert events[-2] == (100, 100)
        assert compat.download_sensevoice(lambda _: pytest.fail("cached model")) == str(snapshot)
    else:
        with pytest.raises(RuntimeError, match="model.pt"):
            compat.download_sensevoice(events.append)
    assert events[:4] == [(0, 0), (20, 100), (20, 100), (55, 100)]
    assert events[-1] is None


def test_frozen_funasr_compat_reuses_legacy_modelscope_cache(
    monkeypatch, tmp_path: Path
):
    module_names = (
        "librosa",
        "soxr",
        "modelscope",
        "modelscope.hub",
        "modelscope.hub.check_model",
        "modelscope.hub.snapshot_download",
        "modelscope.utils",
        "modelscope.utils.constant",
    )
    for name in module_names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path))

    cached = tmp_path / "models/iic--SenseVoiceSmall/snapshots/master"
    cached.mkdir(parents=True)
    for filename in ("configuration.json", "config.yaml", "am.mvn", "chn_jpn_yue_eng_ko_spectok.bpe.model"):
        (cached / filename).touch()
    hub = importlib.import_module("modelscope_hub.compat.snapshot_download")
    downloads = []

    def finish_download(model_id, **options):
        downloads.append(model_id)
        assert set(options["allow_patterns"]) == {
            "model.pt", "config.yaml", "configuration.json", "am.mvn",
            "chn_jpn_yue_eng_ko_spectok.bpe.model",
        }
        (cached / "model.pt").touch()
        return str(cached)

    monkeypatch.setattr(hub, "snapshot_download", finish_download)

    from proximic_ring.asr.funasr_compat import prepare_funasr_runtime

    prepare_funasr_runtime()
    shim = importlib.import_module("modelscope.hub.snapshot_download")

    assert shim.snapshot_download("iic/SenseVoiceSmall", revision="master") == str(
        cached
    )
    assert shim.snapshot_download("iic/SenseVoiceSmall", revision="master") == str(cached)
    assert downloads == ["iic/SenseVoiceSmall"]
    assert sys.modules["librosa"].__spec__ is not None
    assert sys.modules["soxr"].__spec__ is not None
