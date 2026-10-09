import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows executable locking")


@pytest.mark.parametrize("target, locked, sidecar_only", [
    (None, False, False),
    ("app/src-tauri/target/release/roxy-ai-backend.exe", False, False),
    ("app/src-tauri/target/release/roxy-ai-backend.exe", True, False),
    ("app/src-tauri/target/release/roxy-blender-ai.exe", True, False),
    ("backend/build/dist/roxy-ai-backend.exe", True, False),
    ("app/src-tauri/binaries/roxy-ai-backend-x86_64-pc-windows-msvc.exe", True, True),
    ("app/src-tauri/target/release/roxy-ai-backend.exe", True, True),
])
def test_build_checks_locks_before_freezing_backend(tmp_path, target, locked, sidecar_only):
    script = tmp_path / "scripts/build.ps1"
    script.parent.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts/build.ps1", script)
    (tmp_path / "backend/roxy_ai").mkdir(parents=True)
    build_file = tmp_path / target if target else None
    if build_file:
        build_file.parent.mkdir(parents=True, exist_ok=True)
        build_file.write_bytes(b"existing executable")
    handle = build_file.open("rb") if locked else None
    env = {k: v for k, v in os.environ.items() if k not in ("ROXY_SKIP_TAURI", "ROXY_SIGN_THUMBPRINT")}
    env["ROXY_BUILD_TEST_SCRIPT"] = str(script)
    if sidecar_only:
        env["ROXY_SKIP_TAURI"] = "1"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                 "try { & $env:ROXY_BUILD_TEST_SCRIPT } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    finally:
        if handle:
            handle.close()
    output = result.stdout + result.stderr
    assert result.returncode != 0
    if locked and not (sidecar_only and "target/release" in target):
        assert "Cannot update build file:" in output and "Close Roxy AI" in output
        assert str(build_file) in output
    else:
        # The fixture has no Python runtime: reaching it proves the lock check passed.
        assert "Cannot update build file:" not in output
        assert str(tmp_path / "backend/.venv/Scripts/python.exe") in output
    if build_file:
        assert build_file.read_bytes() == b"existing executable"
    assert not (tmp_path / "backend/roxy_ai/_build_flags.py").exists()
