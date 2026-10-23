from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "sign_macos_bundle", Path(__file__).parents[1] / "tools/sign_macos_bundle.py"
)
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


def test_signing_finds_native_code_in_resources_and_signs_children_first(tmp_path):
    app = tmp_path / "Example.app"
    framework = app / "Contents/Frameworks/Example.framework"
    binary = framework / "Versions/A/Example"
    extension = app / "Contents/Resources/python/example.so"
    for path in (binary, extension):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xcf\xfa\xed\xfe" + b"example")
    (framework / "Example").symlink_to("Versions/A/Example")
    (extension.parent / "linked.so").symlink_to(extension)
    (extension.parent / "data.txt").write_text("ordinary resource")
    targets = signing.signing_targets(app)
    assert set(targets) == {app, framework, binary, extension}
    assert targets.index(binary) < targets.index(framework) < targets.index(app)
    assert targets.index(extension) < targets.index(app)


DETAILS = """Identifier=com.proximic.voice
Authority=Developer ID Application: MythLink (5H42RU3M38)
TeamIdentifier=5H42RU3M38
CodeDirectory v=20500 flags=0x10000(runtime)
Timestamp=Oct 10, 2026
"""
REQUIREMENT = ('identifier "com.proximic.voice" and anchor apple generic '
               'and certificate leaf[subject.OU] = "5H42RU3M38"')


def test_identity_accepts_stable_developer_id_requirement():
    signing.check_identity(DETAILS, REQUIREMENT, "com.proximic.voice", "5H42RU3M38")


def test_webengine_helper_preserves_its_own_entitlements(tmp_path):
    helper=tmp_path/'QtWebEngineProcess.app'
    path=helper/'Contents/Resources/QtWebEngineProcess.entitlements'
    with pytest.raises(ValueError,match='Missing Qt WebEngine'):
        signing.webengine_entitlements(helper)
    path.parent.mkdir(parents=True);path.write_bytes(plistlib.dumps({'com.apple.security.cs.allow-jit':True}))
    assert signing.webengine_entitlements(helper)==path
    assert signing.webengine_entitlements(helper/'Contents/MacOS/QtWebEngineProcess')==path
    assert signing.webengine_entitlements(tmp_path/'Mythlink.app') is None


def test_rejects_bundle_id_change_even_if_signer_would_override_code_identifier(tmp_path):
    app = tmp_path / "Mythlink.app"
    info = app / "Contents/Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.other.app"}))
    with pytest.raises(ValueError, match="Bundle identifier changed"):
        signing.check_bundle_identifiers(app)


@pytest.mark.parametrize("details,requirement", [
    (DETAILS.replace("TeamIdentifier=5H42RU3M38", "TeamIdentifier=OTHERTEAM"), REQUIREMENT),
    (DETAILS.replace("Identifier=com.proximic.voice", "Identifier=com.other.app"), REQUIREMENT),
    (DETAILS.replace("Developer ID Application:", "Apple Development:"), REQUIREMENT),
    (DETAILS.replace("Timestamp=", "Signed Time="), REQUIREMENT),
    (DETAILS.replace("(runtime)", "(adhoc)"), REQUIREMENT),
    (DETAILS, 'cdhash H"abcdef"'),
    (DETAILS, REQUIREMENT.replace("5H42RU3M38", "OTHERTEAM")),
])
def test_identity_rejects_broken_update_or_distribution_identity(details, requirement):
    with pytest.raises(ValueError):
        signing.check_identity(details, requirement, "com.proximic.voice", "5H42RU3M38")


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh"])
@pytest.mark.parametrize("identity,allow,expected", [("", "0", 1), ("-", "0", 1), ("-", "1", 0)])
def test_no_certificate_never_silently_falls_back_to_adhoc(tmp_path, shell, identity, allow, expected):
    if not Path(shell).exists():
        pytest.skip("shell unavailable")
    security = tmp_path / "security"
    security.write_text("#!/bin/sh\necho '0 valid identities found'\n")
    security.chmod(0o755)
    script = Path(__file__).parents[1] / "scripts/macos-signing-env.sh"
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "PROJECT_ROOT": str(tmp_path), "APPLE_SIGNING_IDENTITY": identity,
           "PROXIMIC_SIGN_IDENTITY": "", "APPLE_SIGNING_KEYCHAIN": "",
           "PROXIMIC_ALLOW_ADHOC_SIGNING": allow}
    result = subprocess.run([shell, "-c", 'set -euo pipefail; source "$1"', "test", str(script)],
                            env=env, text=True, capture_output=True)
    assert result.returncode == expected, result.stderr


def test_adhoc_report_does_not_require_a_distribution_identity(tmp_path, monkeypatch):
    app=tmp_path/'Test.app';app.mkdir()
    monkeypatch.setattr(signing,'IDENTIFIERS',{'.':'com.test.app'})
    monkeypatch.setattr(signing,'check_bundle_identifiers',lambda _:None)
    monkeypatch.setattr(signing,'signing_targets',lambda _:[app])
    monkeypatch.setattr(signing,'run',lambda *args: '# designated => cdhash H"test"' if '-r-' in args else '')
    report=tmp_path/'report.json'
    signing.sign_bundle(app,'-','TEAM',tmp_path/'entitlements.plist',report)
    import json
    result=json.loads(report.read_text())
    assert result['team'] is None and result['components']['.']['requirement'] is None
