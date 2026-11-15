"""
Starts the elevated format helper. The app itself never runs elevated: this
asks Windows (ShellExecuteExW, verb "runas") to start the helper, which shows
the UAC prompt. Declining the prompt is a normal outcome (None), not an error.
The returned Launch lets the GUI poll whether the helper has exited and with
which code; the helper's progress file carries everything else.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Protocol

from .helper import FormatRequest

# The helper runs as `-m audiorec.usb.helper`, so it needs the directory that
# contains the audiorec package as its working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

ERROR_CANCELLED = 1223
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
WAIT_OBJECT_0 = 0
STILL_ACTIVE = 259
WAIT_FAILED = 0xFFFFFFFF


class Launch(Protocol):
    def wait(self, timeout_ms: int) -> bool:
        """True when the helper has exited."""
        ...

    def exit_code(self) -> int | None: ...

    def close(self) -> None: ...


def helper_command(req: FormatRequest, python: str = sys.executable) -> tuple[str, str]:
    """(executable, parameters); pythonw.exe avoids a console window."""
    py = Path(python)
    pythonw = py.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else py
    # -E -s: ignore PYTHON* variables and user site-packages, which anyone
    # running as the user could plant. Not -I: that would also drop the
    # working directory (the project root) from sys.path, breaking -m.
    params = subprocess.list2cmdline(["-E", "-s", "-m", "audiorec.usb.helper",
                                      *req.to_args()])
    return str(exe), params


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),  # union with hMonitor
        ("hProcess", wintypes.HANDLE),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
_shell32.ShellExecuteExW.restype = wintypes.BOOL


class _ProcessLaunch:
    def __init__(self, handle: int) -> None:
        self._handle: int | None = handle

    def wait(self, timeout_ms: int) -> bool:
        if self._handle is None:
            return True
        result = _kernel32.WaitForSingleObject(self._handle, timeout_ms)
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == WAIT_OBJECT_0

    def exit_code(self) -> int | None:
        if self._handle is None:
            return None
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            return None
        return None if code.value == STILL_ACTIVE else code.value

    def close(self) -> None:
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _shell_execute_runas(exe: str, params: str, cwd: str) -> Launch | None:
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.lpDirectory = cwd
    info.nShow = SW_HIDE
    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            return None
        raise ctypes.WinError(err)
    if not info.hProcess:
        raise OSError("The helper started without a process handle")
    return _ProcessLaunch(info.hProcess)


def start(
    req: FormatRequest,
    shell_execute: Callable[[str, str, str], Launch | None] = _shell_execute_runas,
) -> Launch | None:
    """None when the user declined the UAC prompt; other failures raise OSError."""
    exe, params = helper_command(req)
    return shell_execute(exe, params, str(PROJECT_ROOT))
