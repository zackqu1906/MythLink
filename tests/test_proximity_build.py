"""Check incremental builds with fake tools; never rebuild or sign the live helper."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("zsh") is None, reason="macOS build uses zsh")
def test_unchanged_helper_is_not_resigned(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(repo / "scripts/build-proximity.sh", scripts / "build-proximity.sh")
    native = tmp_path / "native/ProxiMicPresence"
    native.mkdir(parents=True)
    for name in ("Policy.swift", "Calibration.swift", "UnlockSecret.swift", "ReturnWakePolicy.swift", "main.swift", "Info.plist"):
        (native / name).write_text("fixture")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "xcrun").write_text('''#!/bin/zsh
if [[ "$1" == "--show-sdk-path" ]]; then print /fake-sdk; exit 0; fi
if [[ "$2" == "--version" ]]; then print fake-swift; exit 0; fi
print compile >> "$BUILD_CALLS"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then print fixture > "$2"; chmod +x "$2"; exit 0; fi
  shift
done
exit 1
''')
    (bin_dir / "codesign").write_text('''#!/bin/zsh
if [[ "$1" == "--force" ]]; then print sign >> "$BUILD_CALLS"; fi
exit 0
''')
    for tool in bin_dir.iterdir(): tool.chmod(0o755)
    calls = tmp_path / "calls"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "BUILD_CALLS": str(calls)}
    def run(path):
        return subprocess.run(["/bin/zsh", str(path)], cwd=tmp_path, env=env,
                              text=True, capture_output=True, check=True).stdout
    run(scripts / "build-proximity.sh")
    assert calls.read_text().splitlines() == ["compile", "sign"]
    assert "复用" in run(Path("scripts/build-proximity.sh"))
    assert calls.read_text().splitlines() == ["compile", "sign"]
    (native / "Calibration.swift").write_text("changed")
    run(scripts / "build-proximity.sh")
    assert calls.read_text().splitlines() == ["compile", "sign", "compile", "sign"]
