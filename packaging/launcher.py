"""Frozen application entry point with persistent startup diagnostics."""

from datetime import datetime, timezone
import multiprocessing
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import traceback


_SELF_CHECK_OPTIONS = {"--self-check-adpcm", "--self-check-package"}
_BUNDLED_QML_FILES = (
    "QtQml/qmldir",
    "QtQuick/qmldir",
    "QtQuick/Controls/qmldir",
    "QtQuick/Controls/Material/qmldir",
    "QtQuick/Layouts/qmldir",
    "QtQuick/Window/qmldir",
)


def _self_check_requested() -> bool:
    return bool(_SELF_CHECK_OPTIONS.intersection(sys.argv[1:]))


def _verify_bundled_qml_runtime(resource_root: Path) -> None:
    candidates = (
        resource_root / "PySide6" / "Qt" / "qml",
        resource_root / "PySide6" / "qml",
    )
    qml_root = next((item for item in candidates if item.is_dir()), candidates[0])
    missing = [name for name in _BUNDLED_QML_FILES if not (qml_root / name).is_file()]
    if missing:
        raise RuntimeError(
            "Bundled PySide6 QML runtime is incomplete: " + ", ".join(missing)
        )


def _open_diagnostic_log() -> tuple[Path, object]:
    try:
        from proximic_ring.runtime_diagnostics import configure_diagnostics
        capture = configure_diagnostics()
        return capture.writer.path, capture
    except Exception:
        # Keep early broken-package/import failures diagnosable using stdlib
        # alone, even when the logging module itself cannot be loaded.
        path = Path(tempfile.gettempdir()) / 'Mythlink-diagnostic.log'
        handle = path.open('a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = handle
        traceback.print_exc(file=handle)
        return path, handle


def _show_fatal_startup_error(error: BaseException, log_path: Path) -> None:
    detail = str(error).strip() or type(error).__name__
    message = (
        f"Mythlink 无法启动：\n{detail}\n\n"
        f"诊断日志：{log_path}"
    )
    try:
        if sys.platform == "darwin":
            script = (
                "on run argv\n"
                "display alert (item 1 of argv) message (item 2 of argv) "
                "as critical buttons {\"好\"} default button \"好\"\n"
                "end run"
            )
            subprocess.run(
                ["/usr/bin/osascript", "-e", script, "Mythlink", message],
                check=False,
                timeout=20,
            )
        elif sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, message, "Mythlink 启动失败", 0x10
            )
    except BaseException:
        # The persistent traceback remains available if even the native dialog
        # service is unavailable during early process startup.
        pass


def run() -> int:
    log_path, log_capture = _open_diagnostic_log()

    print("\n=== Mythlink startup ===")
    print(f"time_utc={datetime.now(timezone.utc).isoformat()}")
    print(f"platform={platform.platform()} machine={platform.machine()}")
    print(f"python={sys.version.split()[0]} frozen={bool(getattr(sys, 'frozen', False))}")
    print(f"executable={sys.executable}")
    try:
        from proximic_ring.runtime_paths import (
            configure_runtime_environment,
            is_frozen,
            resource_root,
        )

        configure_runtime_environment()
        print("[startup] runtime environment ready")
        package_self_check = "--self-check-package" in sys.argv[1:]
        if "--self-check-adpcm" in sys.argv[1:] or package_self_check:
            from ring_python_sdk.public_protocol import decode_adpcm
            import struct
            block = struct.pack("<hBBH", 0, 0, 0, 1600) + bytes(800)
            if len(decode_adpcm(block) or b"") != 3200:
                raise RuntimeError("内置 ADPCM 解码自检失败")
            print("[startup] bundled ADPCM decoder ready")
            if not package_self_check:
                return 0
        if package_self_check:
            if is_frozen():
                _verify_bundled_qml_runtime(resource_root())
            if sys.platform == "darwin":
                from proximic_ring.app_shortcuts import verify_native_api
                from proximic_ring.ime_bridge import default_socket_path

                verify_native_api()
                from proximic_ring.mac_permissions import read_permission_state

                permissions = read_permission_state()
                if permissions.error:
                    raise RuntimeError("macOS permission APIs unavailable: " + permissions.error)
                print("[startup] macOS app gesture bridges ready")
                if is_frozen():
                    from proximic_ring.input_method_install import InputMethodInstaller

                    InputMethodInstaller().verify_payload()
                    print("[startup] bundled input method installer ready")
                    from proximic_ring.ui.proximity_controller import helper_path
                    import subprocess
                    presence = helper_path()
                    if not presence.is_file():
                        raise RuntimeError("missing bundled proximity helper")
                    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(presence.parents[2])],
                                   check=True, capture_output=True, timeout=10)
                    print("[startup] bundled proximity helper ready (not enabled)")

                print(
                    "[startup] macOS input method transport ready; "
                    f"socket={default_socket_path()} (input source selection is manual)"
                )
            os.environ["PROXIMIC_STARTUP_PROBE"] = "1"
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            print("[startup] bundled QML files ready")
        from proximic_ring.ui.main import main

        print("[startup] UI modules imported")
        exit_code = int((main([sys.argv[0]]) if package_self_check else main()) or 0)
        print(f"[startup] Qt event loop exited with code {exit_code}")
        return exit_code
    except KeyboardInterrupt:
        print("[startup] interrupted")
        return 130
    except BaseException as exc:
        print("[startup] fatal error")
        traceback.print_exc()
        if not _self_check_requested():
            _show_fatal_startup_error(exc, log_path)
        return 1
    finally:
        try:
            log_capture.flush()
        except BaseException:
            pass


def _entrypoint() -> int:
    multiprocessing.freeze_support()
    if len(sys.argv) == 2 and sys.argv[1] == "--permission-status":
        import json
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from proximic_ring.mac_permissions import permission_status_report
        app = QApplication([sys.argv[0]])
        report = permission_status_report(app, QSettings("ProxiMic", "ProxiMic Voice"))
        print(json.dumps(report, ensure_ascii=False))
        return 1 if report["backend_errors"] else 0
    if len(sys.argv) == 2 and sys.argv[1] == "--native-access-worker":
        from proximic_ring.native_access_worker import main

        return main()
    if len(sys.argv) >= 2 and sys.argv[1] == "--input-method":
        from proximic_ring.input_method_install import main

        return main(sys.argv[2:])
    return run()


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
