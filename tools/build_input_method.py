"""Version the IME by its own build inputs, publishing only after signing succeeds."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import tempfile


def build_parts(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", value):
        return None
    parts = tuple(int(part) for part in value.split('.'))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


FINGERPRINT_KEY = 'MythlinkInputMethodFingerprint'


def input_fingerprint(root):
    """Exclude host-app code, build dates, signatures and temporary build paths."""
    paths = [root / 'native/ProxiMicInput/Info.plist',
             root / 'scripts/build-input-method.sh', root / 'tools/build_input_method.py']
    paths += sorted((root / 'native/ProxiMicInput/Sources').glob('*.swift'))
    resources = root / 'native/ProxiMicInput/Resources'
    paths += sorted(path for path in resources.rglob('*') if path.is_file() and path.name != '.DS_Store')
    digest = hashlib.sha256()
    for path in paths:
        content = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(str(len(content)).encode() + b'\0' + content)
    settings = {
        'architecture': platform.machine(),
        'deployment_target': os.environ.get('MACOSX_DEPLOYMENT_TARGET', '15.0'),
        'swift': subprocess.check_output(['xcrun', 'swiftc', '--version'], text=True).strip(),
        'sdk': subprocess.check_output(['xcrun', '--show-sdk-version'], text=True).strip(),
    }
    digest.update(json.dumps(settings, sort_keys=True).encode())
    return digest.hexdigest()


def next_build_number(template, previous, fingerprint=None):
    baseline = build_parts(template.get('CFBundleVersion'))
    if baseline is None:
        raise ValueError('输入法模板的 CFBundleVersion 无效')
    versions = [baseline]
    matching = []
    for path in previous:
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            info = plistlib.load(stream)
        if info.get('CFBundleIdentifier') == template['CFBundleIdentifier']:
            version = build_parts(info.get('CFBundleVersion'))
            if version is not None:
                versions.append(version)
                if fingerprint and info.get(FINGERPRINT_KEY) == fingerprint:
                    matching.append((version, info['CFBundleVersion']))
    parts = max(versions)
    if matching and max(matching)[0] == parts:
        return max(matching)[1]
    return '.'.join(map(str, (*parts[:-1], parts[-1] + 1)))


def publish(stage, destination):
    """Keep the previous app/helper pair intact if either replacement fails."""
    backup = stage / 'previous'
    backup.mkdir()
    replaced = []
    try:
        for name in ('ProxiMicInput.app', 'InputMethodAdmin'):
            target = destination / name
            old = backup / name
            if target.exists():
                os.replace(target, old)
            replaced.append(name)
            os.replace(stage / name, target)
    except BaseException:
        for name in reversed(replaced):
            target, old = destination / name, backup / name
            if target.exists():
                os.replace(target, stage / name)
            if old.exists():
                os.replace(old, target)
        raise


def build(root):
    output = root / '.build/input-method'
    output.mkdir(parents=True, exist_ok=True)
    # Hold this lock through publication so unchanged concurrent builds keep
    # the same number and cannot overwrite a newer successful build.
    with (output / 'build.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with (root / 'native/ProxiMicInput/Info.plist').open('rb') as stream:
            info = plistlib.load(stream)
        fingerprint = input_fingerprint(root)
        info['CFBundleVersion'] = next_build_number(info, [
            output / 'ProxiMicInput.app/Contents/Info.plist',
            Path.home() / 'Library/Input Methods/ProxiMicInput.app/Contents/Info.plist',
            Path('/Library/Input Methods/ProxiMicInput.app/Contents/Info.plist'),
        ], fingerprint)
        info[FINGERPRINT_KEY] = fingerprint
        with tempfile.TemporaryDirectory(prefix='.build-', dir=output) as directory:
            stage = Path(directory)
            contents = stage / 'ProxiMicInput.app/Contents'
            contents.mkdir(parents=True)
            with (contents / 'Info.plist').open('wb') as stream:
                plistlib.dump(info, stream, sort_keys=False)
            subprocess.run(['/bin/zsh', str(root / 'scripts/build-input-method.sh'), '--prepared'],
                           env={**os.environ, 'MYTHLINK_IME_BUILD_STAGE': str(stage)}, check=True)
            if input_fingerprint(root) != fingerprint:
                raise RuntimeError('输入法构建期间源码或配置发生变化，请重新构建；上一份组件保持不变。')
            publish(stage, output)
        print(f"已构建输入法：{info['CFBundleShortVersionString']} ({info['CFBundleVersion']})", flush=True)
        print(f"组件：{output / 'ProxiMicInput.app'}", flush=True)
        print('尚未安装或切换输入法。', flush=True)


if __name__ == '__main__':
    build(Path(__file__).resolve().parents[1])
