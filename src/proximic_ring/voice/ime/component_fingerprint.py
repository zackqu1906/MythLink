"""Toolchain-free fingerprint shared by development status and the IME builder."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FINGERPRINT_KEY = "MythlinkInputMethodSourceFingerprint"


def source_fingerprint(root: Path) -> str:
    paths = [root / name for name in (
        "native/ProxiMicInput/Info.plist",
        "scripts/build-input-method.sh",
        "scripts/InputMethodAdmin.swift",
        "scripts/InputSourceSelection.swift",
        "tools/build_input_method.py",
        "packaging/macos-target.txt",
        "src/proximic_ring/voice/ime/component_fingerprint.py",
    )]
    paths += sorted((root / "native/ProxiMicInput/Sources").glob("*.swift"))
    paths += sorted(path for path in (root / "native/ProxiMicInput/Resources").rglob("*")
                    if path.is_file() and path.name != ".DS_Store")
    digest = hashlib.sha256()
    for path in paths:
        content = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(str(len(content)).encode() + b"\0" + content)
    return digest.hexdigest()
