"""Collect natural joined strokes and confusable single-stroke controls."""
import argparse
import gc
from pathlib import Path
import sys
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ring_selection_collector import CollectionWindow, DEFAULT_SDK
import joined_stroke_collection as profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='Ringo')
    parser.add_argument('--sdk-root', type=Path, default=DEFAULT_SDK)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'data'/'recordings'/'joined-strokes')
    parser.add_argument('--plan', choices=('walk', 'silk', 'additional'), default='walk')
    parser.add_argument('--protocol', choices=('auto', 'raw2', 'legacy'), default='auto')
    args = parser.parse_args()
    sys.path.insert(0, str(args.sdk_root.resolve()))
    from ring_python_sdk.touchpad.raw2_model import raw2_platform_supported

    protocol = args.protocol
    if protocol == 'auto':
        protocol = 'raw2' if raw2_platform_supported() else 'legacy'
    elif protocol == 'raw2' and not raw2_platform_supported():
        parser.error('当前运行环境不支持 Raw2。可用 --protocol legacy 启动旧协议采集。')
    if protocol == 'raw2':
        # Load the baseline before starting the time-sensitive SDK stream,
        # including when the window opens an older session for resuming.
        profile.baseline()
        gc.collect()
        gc.freeze()
    root = tk.Tk()
    CollectionWindow(root, args.output_dir, args.sdk_root, args.device,
                     profile=profile.collection_profile(args.plan) or profile, protocol=protocol)
    root.mainloop()


if __name__ == '__main__':
    main()
