"""Reject dependencies that would prevent the app launching on its target macOS."""

import importlib.util
from pathlib import Path
import plistlib

import pytest


spec = importlib.util.spec_from_file_location(
    "verify_macos_bundle", Path(__file__).parents[1] / "tools/verify_macos_bundle.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def write_plist(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(values))


def load_command(version, legacy=False):
    if legacy:
        return f"Load command 1\n cmd LC_VERSION_MIN_MACOSX\n cmdsize 16\n version {version}\n sdk 26.0\n"
    return f"Load command 1\n cmd LC_BUILD_VERSION\n cmdsize 32\n platform 1\n minos {version}\n sdk 26.0\n"


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    app = tmp_path / "Mythlink.app"
    write_plist(app / "Contents/Info.plist", {"LSUIElement": True, "LSMinimumSystemVersion": "13.3"})
    (app / "Contents/Resources").mkdir()
    binaries = {}

    def add_binary(relative, version="13.3", legacy=False, architecture="arm64"):
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        binaries[path] = (architecture, load_command(version, legacy))
        return path

    add_binary("Contents/MacOS/Mythlink")
    inspected = []

    def run(command, *args):
        path = Path(args[-1])
        inspected.append(path)
        if command.endswith("/file"):
            return f"Mach-O {binaries[path][0]}" if path in binaries else "data"
        if args[0] == "-l":
            return binaries[path][1]
        return f"{path}:\n /usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"

    monkeypatch.setattr(verifier, "_run", run)
    return app, add_binary, inspected


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("relative", [
    "Contents/Frameworks/dependency.so",
    "Contents/Helpers/Helper.app/Contents/MacOS/Helper",
])
def test_rejects_newer_dependency_and_helper_load_commands(bundle, relative, legacy):
    app, add_binary, _ = bundle
    add_binary(relative, "14.0", legacy)
    assert verifier.verify_bundle(app, "13.3") == [
        f"requires macOS 14.0, above declared 13.3: {relative}"
    ]


@pytest.mark.parametrize("legacy", [False, True])
def test_equivalent_patch_versions_and_newer_sdk_are_allowed(bundle, legacy):
    app, add_binary, _ = bundle
    add_binary("Contents/Frameworks/dependency.so", "13.3.0", legacy)
    write_plist(app / "Contents/Info.plist", {"LSUIElement": True, "LSMinimumSystemVersion": "13.3.0"})
    assert verifier.verify_bundle(app, "13.3") == []


@pytest.mark.parametrize("relative", [
    "Contents/Frameworks/dependency.so",
    "Contents/Helpers/Helper.app/Contents/MacOS/Helper",
])
def test_requires_arm64_in_nested_binaries(bundle, relative):
    app, add_binary, _ = bundle
    add_binary(relative, architecture="x86_64")
    assert verifier.verify_bundle(app, "13.3") == [f"Mach-O binary is not arm64: {relative}"]


@pytest.mark.parametrize("relative", [
    "Contents/Info.plist",
    "Contents/Helpers/Helper.app/Contents/Info.plist",
    "Contents/Frameworks/Example.framework/Versions/A/Resources/Info.plist",
])
@pytest.mark.parametrize("declaration", [
    {"LSMinimumSystemVersion": "14.0"},
    {"LSMinimumSystemVersionByArchitecture": {"arm64": "14.0"}},
])
def test_rejects_newer_nested_plist_declarations(bundle, relative, declaration):
    app, _, _ = bundle
    write_plist(app / relative, {"LSUIElement": True, **declaration})
    assert verifier.verify_bundle(app, "13.3") == [
        f"declares macOS 14.0, above target 13.3: {relative}"
    ]


def test_does_not_inspect_symbolic_links(bundle):
    app, add_binary, inspected = bundle
    binary = add_binary("Contents/Frameworks/dependency.so")
    alias = binary.with_name("alias.so")
    alias.symlink_to(binary.name)
    assert verifier.verify_bundle(app, "13.3") == []
    assert alias not in inspected
