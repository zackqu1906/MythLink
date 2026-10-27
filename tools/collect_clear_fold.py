"""Capture fold-hook versus clear-all boundaries in the local SDK window."""
import argparse
from pathlib import Path
import sys
import tkinter as tk

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ring_selection_collector import CollectionWindow, DEFAULT_SDK
import clear_fold_collection as profile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device',default='Ringo')
    parser.add_argument('--sdk-root',type=Path,default=DEFAULT_SDK)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'data'/'recordings'/'clear-fold')
    args=parser.parse_args()
    root=tk.Tk()
    CollectionWindow(root,args.output_dir,args.sdk_root,args.device,profile=profile)
    root.mainloop()


if __name__=='__main__':main()
