"""Check the actual packaging interpreter before freezing or changing the release version."""
from pathlib import Path
import shutil
import sys
import sysconfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proximic_ring.platforms.macos.macho import inspect_macho, version_tuple


def verify_python(requested, minimum_macos):
    requested_path = shutil.which(requested)
    if not requested_path or not Path(sys._base_executable).samefile(requested_path):
        raise SystemExit(f"Packaging environment uses {sys._base_executable}, expected {requested}. "
                         "Recreate .build/packaging-venv-macos13 with the selected Python.")
    if sys.version_info[:2] != (3, 11) or sysconfig.get_config_var("LDLIBRARY") != "libpython3.11.dylib":
        raise SystemExit("Packaging requires standalone CPython 3.11 with libpython3.11.dylib. "
                         "Use the project-local Python from scripts/install-python-macos.sh.")
    library = Path(sysconfig.get_config_var("LIBDIR")) / "libpython3.11.dylib"
    slices = inspect_macho(library)
    if not any(s["architecture"] == "arm64" and s["minimum_macos"] and
               version_tuple(s["minimum_macos"]) <= version_tuple(minimum_macos) for s in slices):
        raise SystemExit(f"{library} does not support arm64 macOS {minimum_macos}.")


if __name__ == "__main__":
    verify_python(*sys.argv[1:])
