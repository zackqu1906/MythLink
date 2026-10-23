#!/usr/bin/env python3
"""Source checkout entry point for the same launcher used by recommendations."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proximic_ring.agent_workflow.recording.codex_launch import main

if __name__ == "__main__":
    raise SystemExit(main())
