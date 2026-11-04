#!/bin/zsh
set -eu
LAB_ROOT="${0:A:h}"
cd "$LAB_ROOT"
if [[ -x "$LAB_ROOT/.venv/bin/python" ]]; then
    LAB_PYTHON="$LAB_ROOT/.venv/bin/python"
elif [[ -x "$LAB_ROOT/../.build/packaging-venv/bin/python" ]]; then
    LAB_PYTHON="$LAB_ROOT/../.build/packaging-venv/bin/python"
else
    print '请先创建本目录虚拟环境并安装 requirements.txt，详见 README.md。'
    exit 1
fi
if [[ $# -eq 0 ]]; then
    set -- serve
fi
exec "$LAB_PYTHON" "$LAB_ROOT/lab.py" "$@"
