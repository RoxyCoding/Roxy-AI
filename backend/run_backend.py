"""Entry point for the frozen (PyInstaller) backend sidecar.

A windowed (no-console) build would show a blocking error dialog on an
unhandled exception; write the traceback to the log folder and exit instead,
so the desktop shell can report the failure.
"""
import sys
import traceback


def _preload_pywin32() -> None:
    """Bind pywin32 to the bundled pywintypes/pythoncom DLLs.

    win32api.pyd resolves pywintypes3XX.dll by name, so an older copy on PATH
    (e.g. from a system-wide Python) would be picked up and fail with
    "procedure not found". Loading ours by full path first prevents that.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base or sys.platform != "win32":
        return
    import ctypes
    import os

    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    # PyInstaller's dependency scan may also put an older copy at the bundle
    # root (from the build machine's base Python); pywin32's hook keeps the
    # right one in pywin32_system32, so prefer that.
    for name in (f"pywintypes{ver}.dll", f"pythoncom{ver}.dll"):
        for folder in (os.path.join(base, "pywin32_system32"), base):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                ctypes.WinDLL(path)
                break


def _run() -> int:
    try:
        _preload_pywin32()
        from roxy_ai.__main__ import main

        main()
        return 0
    except SystemExit as e:
        return int(e.code or 0)
    except BaseException:
        try:
            from roxy_ai.config import data_dir

            (data_dir() / "logs").mkdir(parents=True, exist_ok=True)
            with open(data_dir() / "logs" / "startup-error.log", "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(_run())
