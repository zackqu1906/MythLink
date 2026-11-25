"""Install the R1 SDK wheel shipped with the project."""

import argparse
from pathlib import Path
import subprocess
import sys


def bundled_wheel() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    wheel_dir = project_root / "third_party" / "r1-sdk"
    wheels = list(wheel_dir.glob("ring_python_sdk_r1-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Keep exactly one ring_python_sdk_r1-*.whl in {wheel_dir}.")
    wheel = wheels[0].resolve()
    if not wheel.is_relative_to(project_root) or not wheel.is_file():
        raise SystemExit(f"The R1 SDK wheel must be a file inside {project_root}.")
    return wheel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-deps", action="store_true", help="Reuse the installed dependencies.")
    args = parser.parse_args()
    wheel = bundled_wheel()
    pip_install = [sys.executable, "-m", "pip", "install"]
    if not args.no_deps:
        subprocess.run([*pip_install, f"{wheel}[touchpad,ota]", "smpclient[ble]>=5.2.0,<8"], check=True)
    # pip can keep an already-installed wheel with the same version.
    subprocess.run([*pip_install, "--no-index", "--no-deps", "--force-reinstall", str(wheel)], check=True)


if __name__ == "__main__":
    main()
