#!/bin/sh
cd "$(dirname "$0")/.." || exit 1
exec .runtime/venv/bin/python tools/eval_human_review.py --open
