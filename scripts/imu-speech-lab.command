#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
for candidate in .build/packaging-venv/bin/python .venv/bin/python python3; do
  if "$candidate" -c 'import numpy, bleak, sounddevice' >/dev/null 2>&1; then
    exec "$candidate" tools/imu_speech.py "$@"
  fi
done
print '未找到完整 Python 环境。请在项目虚拟环境中安装 numpy、bleak、sounddevice、cryptography 后重试。'
exit 1
