"""Compatibility helpers for the deliberately pruned FunASR runtime."""

from __future__ import annotations

import sys
import os
import threading
import time
from collections.abc import Callable
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType


_MODEL_FILES = ("model.pt", "configuration.json", "config.yaml")
_SENSEVOICE_FILES = _MODEL_FILES + ("am.mvn", "chn_jpn_yue_eng_ko_spectok.bpe.model")
DownloadProgress = Callable[[tuple[int, int] | None], None]


def _modelscope_cache_root() -> Path:
    return Path(os.environ.get("MODELSCOPE_CACHE", Path.home() / ".cache/modelscope")).expanduser()


def cached_modelscope_snapshot(model_id: str, revision: str = "master") -> Path | None:
    snapshot = _modelscope_cache_root() / "models" / model_id.replace("/", "--") / "snapshots" / revision
    files = _SENSEVOICE_FILES if model_id == "iic/SenseVoiceSmall" else _MODEL_FILES
    # The hub creates the snapshot directory before all downloads finish.
    return snapshot if all((snapshot / name).is_file() for name in files) else None


def download_sensevoice(on_progress: DownloadProgress) -> str:
    """Resolve the desktop model, reporting the bytes needed by this download."""
    from modelscope_hub.compat.snapshot_download import snapshot_download

    model_id = "iic/SenseVoiceSmall"
    cached = cached_modelscope_snapshot(model_id)
    if cached is not None:
        return str(cached)
    root = _modelscope_cache_root()
    # The Hub reuses a non-empty legacy cache ahead of the new snapshot layout.
    snapshot = root / "models/iic--SenseVoiceSmall/snapshots/master"
    for legacy in (root / "models" / model_id, root / "hub/models" / model_id):
        if legacy.is_dir() and any(legacy.iterdir()):
            snapshot = legacy
            break
    missing = {name for name in _SENSEVOICE_FILES if not (snapshot / name).is_file()}
    if not missing:
        return str(snapshot)

    sizes: dict[str, int] = {}
    finished: set[str] = set()
    lock = threading.Lock()
    last_update = 0.0

    def publish() -> None:
        # Read staged bytes at UI refresh rate. Hub retries can report an
        # existing range more than once, so summing callback deltas overcounts.
        received = 0
        for name, size in sizes.items():
            if name in finished:
                received += size
                continue
            partial = snapshot / (name + ".incomplete")
            try:
                if partial.is_file():
                    received += partial.stat().st_size
                else:
                    # Multipart downloads write <filename>_<start>_<end>.
                    received += sum(part.stat().st_size for part in snapshot.glob(name + "_[0-9]*_[0-9]*"))
            except FileNotFoundError:
                pass  # A worker just renamed or merged a staged file.
        total = sum(sizes.values()) if missing <= sizes.keys() and all(sizes.values()) else 0
        on_progress((min(received, total) if total else received, total))

    class FileProgress:
        def __init__(self, filename: str, file_size: int):
            self.name = filename
            with lock:
                sizes[filename] = file_size
                finished.discard(filename)

        def update(self, _size: int) -> None:
            nonlocal last_update
            with lock:
                now = time.monotonic()
                if now - last_update >= 0.25:
                    last_update = now
                    publish()

        def end(self) -> None:
            with lock:
                finished.add(self.name)
                if not sizes[self.name]:
                    target = snapshot / self.name
                    if target.is_file():
                        sizes[self.name] = target.stat().st_size
                publish()

    on_progress((0, 0))
    try:
        path = Path(snapshot_download(model_id, revision="master", cache_dir=str(root),
                                      allow_patterns=list(_SENSEVOICE_FILES), progress_callbacks=[FileProgress]))
        # The Hub may return a snapshot even if a file worker failed.
        absent = [name for name in _SENSEVOICE_FILES if not (path / name).is_file()]
        if absent:
            raise RuntimeError("SenseVoice 模型下载未完成，请重试。缺少 " + "、".join(absent))
        return str(path)
    finally:
        on_progress(None)


def prepare_funasr_runtime() -> None:
    """Satisfy FunASR's unused librosa import in a frozen application.

    ``funasr.utils.load_utils`` imports librosa unconditionally, although the
    audio paths used by Mythlink use torch, torchaudio and NumPy only.
    Keeping librosa would also retain numba, llvmlite, scipy and scikit-learn.
    Source installations keep using a real librosa when one is available; a
    frozen build uses this empty module because those APIs are never called.
    """

    if not getattr(sys, "frozen", False):
        return
    if "librosa" not in sys.modules:
        placeholder = ModuleType("librosa")
        placeholder.__doc__ = "Unused FunASR compatibility placeholder."
        placeholder.__spec__ = ModuleSpec("librosa", loader=None)
        sys.modules["librosa"] = placeholder
    # Transformers imports soxr when it detects the FunASR librosa placeholder.
    # Qwen3 text generation never calls either audio loading API.
    if "soxr" not in sys.modules:
        soxr_placeholder = ModuleType("soxr")
        soxr_placeholder.__doc__ = "Unused Transformers audio placeholder."
        soxr_placeholder.__spec__ = ModuleSpec("soxr", loader=None)
        sys.modules["soxr"] = soxr_placeholder
    _install_modelscope_download_shim()


def _install_modelscope_download_shim() -> None:
    """Expose the three ModelScope names FunASR uses without its model SDK.

    Current ModelScope delegates downloads to the much smaller
    ``modelscope_hub`` package.  Importing the legacy top-level SDK makes
    PyInstaller follow type-checking imports for thousands of unrelated CV,
    NLP, training and export modules, so the frozen app provides this narrow
    compatibility surface instead.
    """

    if "modelscope.hub.snapshot_download" in sys.modules:
        return

    from modelscope_hub.compat.snapshot_download import (
        snapshot_download as hub_snapshot_download,
    )

    def snapshot_download(
        model_id: str,
        revision: str | None = None,
        user_agent: object | None = None,
        **kwargs: object,
    ) -> str:
        del user_agent  # The lightweight hub supplies its own user agent.
        cached_snapshot = cached_modelscope_snapshot(model_id, str(revision or "master"))
        if cached_snapshot is not None:
            return str(cached_snapshot)
        # The frozen app ships the implementation. Fetch only the checkpoint
        # and frontend/tokenizer data used by that implementation.
        if model_id == "iic/SenseVoiceSmall" and not {"allow_patterns", "allow_file_pattern"}.intersection(kwargs):
            kwargs["allow_patterns"] = list(_SENSEVOICE_FILES)
        return str(
            hub_snapshot_download(
                model_id=model_id,
                revision=revision,
                cache_dir=str(_modelscope_cache_root()),
                **kwargs,
            )
        )

    class Invoke:
        KEY = "invoke"
        PIPELINE = "pipeline"
        LOCAL_TRAINER = "local_trainer"

    class ThirdParty:
        KEY = "third_party"

    packages = {
        "modelscope": ModuleType("modelscope"),
        "modelscope.hub": ModuleType("modelscope.hub"),
        "modelscope.utils": ModuleType("modelscope.utils"),
    }
    for name, module in packages.items():
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules.setdefault(name, module)

    snapshot_module = ModuleType("modelscope.hub.snapshot_download")
    snapshot_module.snapshot_download = snapshot_download  # type: ignore[attr-defined]
    sys.modules["modelscope.hub.snapshot_download"] = snapshot_module

    check_module = ModuleType("modelscope.hub.check_model")
    check_module.check_local_model_is_latest = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    sys.modules["modelscope.hub.check_model"] = check_module

    constant_module = ModuleType("modelscope.utils.constant")
    constant_module.Invoke = Invoke  # type: ignore[attr-defined]
    constant_module.ThirdParty = ThirdParty  # type: ignore[attr-defined]
    sys.modules["modelscope.utils.constant"] = constant_module
