"""Child-process hygiene and CLI discovery.

On Windows the backend puts itself in a Job Object with KILL_ON_JOB_CLOSE, so
every process it starts (AI CLIs, their MCP servers, the monitor's MCP server,
node launchers of `codex.cmd` ...) is killed by the OS when the backend exits,
even if it crashes or is killed by the desktop shell.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

_job_handle = None


def install_kill_on_close_job() -> bool:
    global _job_handle
    if os.name != "nt" or _job_handle is not None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            return False
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            # Already in a job that forbids nesting; the parent's job still applies.
            logger.info("AssignProcessToJobObject failed (%s)", ctypes.get_last_error())
            return False
        _job_handle = job  # keep the handle open for the life of the process
        return True
    except Exception as e:
        logger.warning("Job object setup failed: %s", e)
        return False


def watch_parent(parent_pid: int, on_exit) -> None:
    """Call on_exit() once the desktop shell process is gone."""

    def alive(pid: int) -> bool:
        if os.name == "nt":
            import ctypes

            SYNCHRONIZE = 0x00100000
            h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if not h:
                return False
            try:
                return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def loop():
        while alive(parent_pid):
            time.sleep(2)
        logger.info("Parent process %s exited; shutting down", parent_pid)
        on_exit()

    threading.Thread(target=loop, name="parent-watch", daemon=True).start()


def resolve_cli(configured: str, name: str) -> str | None:
    if configured:
        return configured if os.path.isfile(configured) else shutil.which(configured)
    return shutil.which(name)


NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


async def run_capture(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
    """Run a short command (e.g. `--version`) without a console window."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "timeout"
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


def python_exe() -> str:
    return sys.executable
