"""
The real DiskOps for Windows, used only by the elevated helper. Not unit
tested (it erases disks); it is covered by the manual checklist and every
step that decides *what* to write is tested through helper.run.

Partitioning is done with the Storage cmdlets. The new partition gets no drive
letter until the file system is written, so Explorer never offers its "format
this disk" popup for a RAW volume. The volume is then opened by its
\\\\?\\Volume{GUID} path, locked and dismounted, and the FAT32 structures are
written straight to it (sector-aligned, as raw volume I/O requires).

Disk numbers can be reused between processes, so each destructive step binds
itself to the checked stick: the erase script re-checks serial and size in the
same script that clears the disk, and the locked volume must be exactly the
partition just created (disk number, offset and length) before any write.
"""
from __future__ import annotations

import ctypes
import json
import msvcrt
import os
import re
import subprocess
import time
from contextlib import contextmanager
from ctypes import wintypes
from typing import BinaryIO, Iterator

from .disks import MAX_SIZE, POWERSHELL, UsbDisk, list_usb_disks
from .helper import CHANGED

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value
FSCTL_LOCK_VOLUME = 0x90018
FSCTL_DISMOUNT_VOLUME = 0x90020
FSCTL_UNLOCK_VOLUME = 0x9001C
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
_MBR_FAT32_LBA = 12  # 0x0C
_NOT_THIS_STICK = "The volume is not on the selected stick. Nothing was written."

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


class _DiskExtent(ctypes.Structure):
    _fields_ = [("DiskNumber", wintypes.DWORD),
                ("StartingOffset", ctypes.c_longlong),
                ("ExtentLength", ctypes.c_longlong)]


class _VolumeDiskExtents(ctypes.Structure):
    # Room for one extent: a volume spanning several makes the call fail with
    # ERROR_MORE_DATA, which is refused like any other mismatch.
    _fields_ = [("NumberOfDiskExtents", wintypes.DWORD), ("Extents", _DiskExtent * 1)]


_POSITION_LINE = re.compile(r"^At (line:\d+|.*:\d+) char:\d+")


def first_error_line(stderr: str) -> str:
    """The message of a PowerShell error, without its position/category noise."""
    for line in stderr.splitlines():
        text = line.strip()
        if (not text or text.startswith(("+", "~", "#< CLIXML"))
                or _POSITION_LINE.match(text)):
            continue
        return text
    return ""


def check_extents(
    extents: list[tuple[int, int, int]], *, disk_number: int, offset: int, size: int
) -> None:
    """Refuse unless the volume is exactly the partition just created."""
    if extents != [(disk_number, offset, size)]:
        raise OSError(_NOT_THIS_STICK)


def _ps(script: str, env: dict[str, str] | None = None) -> str:
    """Run a script; return the last non-empty line of its output."""
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
            env={**os.environ, **env} if env else None,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Windows did not answer in time") from None
    if proc.returncode != 0:
        raise RuntimeError(first_error_line(proc.stderr) or f"PowerShell failed ({proc.returncode})")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _ioctl(handle: int, code: int, out: ctypes.Structure | None = None) -> None:
    returned = wintypes.DWORD()
    out_ptr, out_size = (ctypes.byref(out), ctypes.sizeof(out)) if out is not None else (None, 0)
    if not _DeviceIoControl(handle, code, None, 0, out_ptr, out_size,
                            ctypes.byref(returned), None):
        raise ctypes.WinError(ctypes.get_last_error())


def _volume_extents(handle: int) -> list[tuple[int, int, int]]:
    out = _VolumeDiskExtents()
    try:
        _ioctl(handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, out)
    except OSError:
        raise OSError(_NOT_THIS_STICK) from None
    if out.NumberOfDiskExtents != 1:
        raise OSError(_NOT_THIS_STICK)
    e = out.Extents[0]
    return [(int(e.DiskNumber), int(e.StartingOffset), int(e.ExtentLength))]


def _open_handle(path: str) -> int:
    handle = _CreateFileW(path, _GENERIC_READ | _GENERIC_WRITE,
                          _FILE_SHARE_READ | _FILE_SHARE_WRITE, None, _OPEN_EXISTING, 0, None)
    if handle is None or handle == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _retry(action):
    # RuntimeError covers PowerShell lookups failing while Windows is still
    # mounting the new partition.
    for attempt in range(_RETRIES):
        try:
            return action()
        except (OSError, RuntimeError):
            if attempt == _RETRIES - 1:
                raise
            time.sleep(_RETRY_DELAY)


class WinDiskOps:
    def list_disks(self) -> list[UsbDisk]:
        return list_usb_disks()

    def repartition(self, disk_number: int, serial: str, size: int) -> tuple[int, int, int]:
        n, size = int(disk_number), int(size)
        # The serial comes from the device, so it is passed as data (an
        # environment variable), never as script text. Clear-Disk refuses a
        # never-initialised (RAW) disk, which has nothing to clear anyway.
        # Set-Partition makes sure the type is 0x0C (FAT32 LBA) whatever
        # New-Partition chose.
        out = _ps(f"""
$d = Get-Disk -Number {n}
if ([string]::IsNullOrWhiteSpace($env:AR_SERIAL) -or
    ([string]$d.SerialNumber).Trim() -cne $env:AR_SERIAL -or
    [int64]$d.Size -ne {size} -or [int64]$d.Size -ge {MAX_SIZE} -or
    $d.BusType.ToString() -ne 'USB' -or
    $d.IsBoot -ne $false -or $d.IsSystem -ne $false) {{
    throw '{CHANGED}'
}}
if ($d.PartitionStyle -ne 'RAW') {{
    Clear-Disk -Number {n} -RemoveData -RemoveOEM -Confirm:$false
}}
Initialize-Disk -Number {n} -PartitionStyle MBR
$p = New-Partition -DiskNumber {n} -UseMaximumSize -Alignment 1048576 -MbrType FAT32
Set-Partition -DiskNumber {n} -PartitionNumber $p.PartitionNumber -MbrType {_MBR_FAT32_LBA}
$p = Get-Partition -DiskNumber {n} -PartitionNumber $p.PartitionNumber
[pscustomobject]@{{ Offset = [int64]$p.Offset; Size = [int64]$p.Size;
    Number = [int]$p.PartitionNumber }} | ConvertTo-Json -Compress
""", env={"AR_SERIAL": serial})
        data = json.loads(out)
        return int(data["Offset"]), int(data["Size"]), int(data["Number"])

    def _volume_path(self, disk_number: int, partition_number: int) -> str:
        n, p = int(disk_number), int(partition_number)
        out = _ps(f"""
(Get-Partition -DiskNumber {n} -PartitionNumber {p}).AccessPaths |
    Where-Object {{ $_ -like '\\\\?\\Volume{{*' }} | Select-Object -First 1
""")
        if not out.startswith("\\\\?\\Volume{"):
            raise OSError(f"No volume found on disk {n}")
        return out.rstrip("\\")

    @contextmanager
    def open_volume(
        self, disk_number: int, partition_number: int, offset: int, size: int
    ) -> Iterator[BinaryIO]:
        path = _retry(lambda: self._volume_path(disk_number, partition_number))

        def open_locked() -> int:
            handle = _open_handle(path)
            try:
                _ioctl(handle, FSCTL_LOCK_VOLUME)
            except OSError:
                _CloseHandle(handle)
                raise
            return handle

        handle = _retry(open_locked)
        try:
            # Locked, so the volume cannot change under us: it must be exactly
            # the partition just created before anything is written.
            check_extents(_volume_extents(handle), disk_number=int(disk_number),
                          offset=int(offset), size=int(size))
            _ioctl(handle, FSCTL_DISMOUNT_VOLUME)
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            _CloseHandle(handle)
            raise
        try:
            dev_file = os.fdopen(fd, "r+b", buffering=0)
        except BaseException:
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

    def remount(self, disk_number: int, partition_number: int) -> str:
        n, p = int(disk_number), int(partition_number)
        # Windows may already have given removable media a letter on remount.
        out = _ps(f"""
$p = Get-Partition -DiskNumber {n} -PartitionNumber {p}
if (-not [string]$p.DriveLetter -or [int][char]$p.DriveLetter -eq 0) {{
    Add-PartitionAccessPath -DiskNumber {n} -PartitionNumber {p} -AssignDriveLetter
    Start-Sleep -Seconds 1
    $p = Get-Partition -DiskNumber {n} -PartitionNumber {p}
}}
[string]$p.DriveLetter
""")
        letter = out.strip().upper()
        if len(letter) != 1 or not letter.isalpha():
            raise RuntimeError("Formatted, but Windows did not assign a drive letter")
        return letter
