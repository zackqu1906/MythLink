#!/usr/bin/env python3
"""Project compatibility entry point for the reusable skill renderer."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / 'skills/llm-eval-report/scripts/render_report.py'
_spec = importlib.util.spec_from_file_location('llm_eval_report_renderer', _path)
_impl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_impl)
globals().update({k:v for k,v in vars(_impl).items() if not k.startswith('_')})
if __name__ == '__main__':
    main()
