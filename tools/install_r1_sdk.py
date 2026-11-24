"""Install the supplied private R1 wheel for a source run or internal build."""

import os
from pathlib import Path
import subprocess
import sys


def main():
    requested = os.environ.get("MYTHLINK_R1_SDK_WHEEL", "").strip()
    if not requested:
        return
    wheel = Path(requested).expanduser().resolve()
    if wheel.suffix != ".whl" or not wheel.is_file():
        raise SystemExit("MYTHLINK_R1_SDK_WHEEL must point to a local R1-PC wheel")
    subprocess.run([sys.executable, "-m", "pip", "install", f"{wheel}[touchpad]",
                    "bleak==3.1.0", "smpclient[ble]==7.4.0"], check=True)


if __name__ == "__main__":
    main()
