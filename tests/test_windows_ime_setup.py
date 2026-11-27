"""Check the exit status the main installer receives from the TSF installer."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows Inno Setup")
def test_failed_dll_registration_fails_install_and_reinstall(tmp_path):
    root = Path(__file__).resolve().parents[1]
    compiler = shutil.which("ISCC") or root / ".build/tools/InnoSetup-6.7.4/ISCC.exe"
    if not Path(compiler).is_file():
        pytest.skip("Inno Setup compiler unavailable")

    # Run the release file and registration steps in a private directory with
    # an invalid DLL. No elevation, uninstall entry or TSF profile is needed.
    recipe = (root / "packaging/windows/MythlinkInput.iss").read_text(encoding="utf-8")
    install_steps = recipe[recipe.index("[Files]"):].replace(
        r"{commonpf64}\Mythlink Input Method", "{app}"
    )
    destination = tmp_path / "app"
    script = tmp_path / "fixture.iss"
    script.write_text(
        '#define InputDll "invalid.dll"\n#define InstalledDll "invalid.dll"\n'
        "[Setup]\nAppName=Mythlink IME registration test\nAppVersion=1\n"
        f"DefaultDirName={destination}\nPrivilegesRequired=lowest\n"
        "Uninstallable=no\nCloseApplications=no\nUsePreviousAppDir=no\n"
        "ArchitecturesAllowed=x64compatible\nArchitecturesInstallIn64BitMode=x64compatible\n"
        + install_steps, encoding="utf-8-sig"
    )
    (tmp_path / "invalid.dll").write_bytes(b"invalid test DLL")
    subprocess.run([compiler, "/Q", f"/O{tmp_path}", "/Ftest-setup", script], check=True)

    for _ in range(2):
        result = subprocess.run([tmp_path / "test-setup.exe", "/VERYSILENT",
                                 "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART"], timeout=60)
        assert (destination / "invalid.dll").read_bytes() == b"invalid test DLL"
        assert result.returncode != 0
