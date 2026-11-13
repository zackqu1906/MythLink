#!/usr/bin/env python3
"""Replace the default firmware shipped by future builds (never flashes hardware)."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from proximic_ring.firmware.bundle import DEFAULT_DIRECTORY, prepare_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path, help='Ringo MCUboot DFU zip')
    parser.add_argument('--output', type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args()
    bundle = prepare_bundle(args.firmware.expanduser(), args.output)
    print(f'Bundled ring firmware: {bundle.version} ({bundle.size} bytes)\nSHA256: {bundle.sha256}')


if __name__ == '__main__':
    main()
