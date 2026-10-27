"""Launch the release-time eight-direction selection feasibility collection."""
import argparse
from pathlib import Path
import tkinter as tk

from ring_selection_collector import CollectionWindow, DEFAULT_SDK, ROOT
import eight_direction_selection as profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="Ringo")
    parser.add_argument("--sdk-root", type=Path, default=DEFAULT_SDK)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "recordings" / "selection-eight")
    args = parser.parse_args()
    root = tk.Tk()
    CollectionWindow(root, args.output_dir, args.sdk_root, args.device, profile=profile)
    root.mainloop()


if __name__ == "__main__":
    main()
