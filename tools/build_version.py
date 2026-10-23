"""Select and persist the host-app version before a packaging build starts."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib


def validate_version(value: str) -> str:
    if not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', value):
        raise ValueError('版本号须为三段非负整数，例如 0.6.10')
    return value


def next_version(current: str) -> str:
    parts = validate_version(current).split('.')
    parts[-1] = str(int(parts[-1]) + 1)
    return '.'.join(parts)


def select_version(current, explicit=None, automatic=False):
    if explicit is not None:
        return validate_version(explicit)
    default = next_version(current)
    if automatic:
        print(f'主应用版本：{current} → {default}', file=sys.stderr)
        return default
    if not sys.stdin.isatty():
        raise ValueError('非交互构建请传 --version X.Y.Z 或 --auto-version；未修改版本号。')
    while True:
        print(f'主应用版本号（当前 {current}，回车使用 {default}）：', end='', file=sys.stderr, flush=True)
        try:
            value = input().strip() or default
        except EOFError as exc:
            raise ValueError('已取消版本输入；未修改版本号。') from exc
        try:
            return validate_version(value)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)


def prepare_version(root, explicit=None, automatic=False):
    path = root / 'pyproject.toml'
    source = path.read_text(encoding='utf-8')
    current = tomllib.loads(source)['project']['version']
    selected = select_version(current, explicit, automatic)
    # Scope the replacement to [project], preserving all other source formatting.
    section = re.search(r'(?ms)^\[project\]\s*\n(?P<body>.*?)(?=^\[|\Z)', source)
    if section is None:
        raise ValueError('缺少 [project] 配置')
    body, count = re.subn(r'(?m)^(version\s*=\s*)[\"\'][^\"\']+[\"\']',
                          lambda match: match[1] + f'"{selected}"', section['body'])
    if count != 1:
        raise ValueError('无法唯一定位主应用版本号')
    updated = source[:section.start('body')] + body + source[section.end('body'):]
    # A failed package attempt keeps its chosen version, so the next attempt
    # still defaults to the next last-dot component as requested.
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=root, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(updated)
    try:
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--version')
    group.add_argument('--auto-version', action='store_true')
    args = parser.parse_args()
    explicit = args.version or os.environ.get('MYTHLINK_BUILD_VERSION')
    if explicit is None and os.environ.get('GITHUB_REF_TYPE') == 'tag':
        explicit = os.environ['GITHUB_REF_NAME'].removeprefix('v')
    automatic = args.auto_version or os.environ.get('CI', '').lower() == 'true'
    try:
        print(prepare_version(Path(__file__).resolve().parents[1], explicit, automatic))
    except ValueError as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    main()
