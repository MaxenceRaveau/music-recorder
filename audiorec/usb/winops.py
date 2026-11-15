"""
The real DiskOps for Windows, used only by the elevated helper. Not unit
tested (it erases disks); it is covered by the manual checklist and every
step that decides *what* to write is tested through helper.run.

Partitioning is done with the Storage cmdlets. The new partition gets no drive
letter until the file system is written, so Explorer never offers its "format
this disk" popup for a RAW volume. The volume is then opened by its
\\\\?\\Volume{GUID} path, locked and dismounted, and the FAT32 structures are
written straight to it (sector-aligned, as raw volume I/O requires).
"""
from __future__ import annotations

import ctypes
import json
import msvcrt
import os
import subprocess
import time
from contextlib import contextmanager
from ctypes import wintypes
from typing import BinaryIO, Iterator

from .disks import POWERSHELL, UsbDisk, list_usb_disks

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value
FSCTL_LOCK_VOLUME = 0x90018
FSCTL_DISMOUNT_VOLUME = 0x90020
FSCTL_UNLOCK_VOLUME = 0x9001C
_MBR_FAT32_LBA = 12  # 0x0C

_PS_TIMEOUT = 300
# Windows may still be mounting the new partition when we ask for it.
_RETRIES = 10
_RETRY_DELAY = 1.0

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_CreateFileW.restype = wintypes.HANDLE
_DeviceIoControl = _kernel32.DeviceIoControl
_DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                             wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                             wintypes.LPVOID]
_DeviceIoControl.restype = wintypes.BOOL
_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL


def _ps(script: str) -> str:
    try:
        proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference = 'Stop'\n" + script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PS_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Windows did not answer in time") from None
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"PowerShell failed ({proc.returncode})")
    return proc.stdout.strip()


def _ioctl(handle: int, code: int) -> None:
    returned = wintypes.DWORD()
    if not _DeviceIoControl(handle, code, None, 0, None, 0, ctypes.byref(returned), None):
        raise ctypes.WinError(ctypes.get_last_error())


def _open_handle(path: str) -> int:
    handle = _CreateFileW(path, _GENERIC_READ | _GENERIC_WRITE,
                          _FILE_SHARE_READ | _FILE_SHARE_WRITE, None, _OPEN_EXISTING, 0, None)
    if handle is None or handle == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _retry(action):
    for attempt in range(_RETRIES):
        try:
            return action()
        except OSError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(_RETRY_DELAY)


class WinDiskOps:
    def list_disks(self) -> list[UsbDisk]:
        return list_usb_disks()

    def repartition(self, disk_number: int) -> tuple[int, int]:
        n = int(disk_number)
        # Clear-Disk refuses a never-initialised (RAW) disk, which has nothing
        # to clear anyway. Set-Partition makes sure the type is 0x0C (FAT32
        # LBA) whatever New-Partition chose.
        out = _ps(f"""
if ((Get-Disk -Number {n}).PartitionStyle -ne 'RAW') {{
    Clear-Disk -Number {n} -RemoveData -RemoveOEM -Confirm:$false
}}
Initialize-Disk -Number {n} -PartitionStyle MBR
$p = New-Partition -DiskNumber {n} -UseMaximumSize -Alignment 1048576 -MbrType FAT32
Set-Partition -DiskNumber {n} -PartitionNumber $p.PartitionNumber -MbrType {_MBR_FAT32_LBA}
$p = Get-Partition -DiskNumber {n} -PartitionNumber $p.PartitionNumber
[pscustomobject]@{{ Offset = [int64]$p.Offset; Size = [int64]$p.Size }} | ConvertTo-Json -Compress
""")
        data = json.loads(out)
        return int(data["Offset"]), int(data["Size"])

    def _volume_path(self, disk_number: int) -> str:
        n = int(disk_number)
        out = _ps(f"""
(Get-Partition -DiskNumber {n} -PartitionNumber 1).AccessPaths |
    Where-Object {{ $_ -like '\\\\?\\Volume{{*' }} | Select-Object -First 1
""")
        if not out.startswith("\\\\?\\Volume{"):
            raise OSError(f"No volume found on disk {n}")
        return out.rstrip("\\")

    @contextmanager
    def open_volume(self, disk_number: int) -> Iterator[BinaryIO]:
        path = _retry(lambda: self._volume_path(disk_number))

        def open_locked() -> int:
            handle = _open_handle(path)
            try:
                _ioctl(handle, FSCTL_LOCK_VOLUME)
                _ioctl(handle, FSCTL_DISMOUNT_VOLUME)
            except OSError:
                _CloseHandle(handle)
                raise
            return handle

        handle = _retry(open_locked)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except OSError:
            _CloseHandle(handle)
            raise
        try:
            dev_file = os.fdopen(fd, "r+b", buffering=0)
        except OSError:
            os.close(fd)
            raise
        # The file object owns fd, which owns the handle: closing it closes both.
        with dev_file as dev:
            try:
                yield dev
                dev.flush()
                os.fsync(dev.fileno())  # FlushFileBuffers
            finally:
                try:
                    _ioctl(msvcrt.get_osfhandle(dev.fileno()), FSCTL_UNLOCK_VOLUME)
                except OSError:
                    pass  # closing the handle releases the lock anyway

    def remount(self, disk_number: int) -> str:
        n = int(disk_number)
        # Windows may already have given removable media a letter on remount.
        out = _ps(f"""
$p = Get-Partition -DiskNumber {n} -PartitionNumber 1
if (-not [string]$p.DriveLetter -or [int][char]$p.DriveLetter -eq 0) {{
    Add-PartitionAccessPath -DiskNumber {n} -PartitionNumber 1 -AssignDriveLetter
    Start-Sleep -Seconds 1
    $p = Get-Partition -DiskNumber {n} -PartitionNumber 1
}}
[string]$p.DriveLetter
""")
        letter = out.strip().upper()
        if len(letter) != 1 or not letter.isalpha():
            raise RuntimeError("Formatted, but Windows did not assign a drive letter")
        return letter
