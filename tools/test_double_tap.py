#!/usr/bin/env python3
"""Standalone firmware double-tap test, targeting ring 8F56 by default."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import test_firmware_gestures as firmware  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return firmware.main(["--double-tap", *arguments], default_name="8F56")


if __name__ == "__main__":
    raise SystemExit(main())
