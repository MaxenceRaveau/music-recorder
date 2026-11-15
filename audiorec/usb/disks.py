"""
Which disks the user may be offered for erasing. This filter is a safety
boundary: the picker shows only what passes it, and the elevated helper uses
the same code to re-identify the disk before writing anything.

A disk qualifies only if it is on the USB bus, is neither system nor boot, is
under 2 TB, and reports both a size and a serial number (the helper needs the
serial to make sure it is still talking to the same stick). Anything unknown
or missing is treated as "not safe" and excluded.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

MAX_SIZE = 2 * 1024**4
_USB_BUS_TYPE = 7
_FORBIDDEN_LABEL_CHARS = set('"*+,./:;<=>?[' + chr(92) + ']|')
_MAX_LABEL = 11
_DEFAULT_LABEL = "REKORDBOX"

# Windows PowerShell 5.1 serialises a lone item as an object rather than a
# one-element array, and a [char] as a number, so the script casts letters to
# string and the parser accepts both shapes.
LIST_SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$disks = Get-Disk | Select-Object Number, FriendlyName, SerialNumber, Size, IsSystem, IsBoot,
    @{Name='BusType'; Expression={ $_.BusType.ToString() }}
$parts = Get-Partition | Select-Object DiskNumber, @{Name='DriveLetter'; Expression={ [string]$_.DriveLetter }}
$vols = Get-Volume | Select-Object @{Name='DriveLetter'; Expression={ [string]$_.DriveLetter }},
    FileSystemLabel, FileSystem
[pscustomobject]@{ disks = $disks; parts = $parts; vols = $vols } | ConvertTo-Json -Depth 3 -Compress
"""


@dataclass(frozen=True)
class UsbDisk:
    number: int
    model: str
    serial: str
    size: int
    letters: tuple[str, ...]
    label: str
    filesystem: str

    @property
    def display(self) -> str:
        letter = f"{self.letters[0]}:" if self.letters else "(no letter)"
        return (
            f"{letter}  {self.label or self.model}  {self.size / 1e9:.1f} GB  "
            f"{self.filesystem or 'no file system'}"
        )


def clean_label(text: str) -> str:
    kept = "".join(c for c in text.upper() if c not in _FORBIDDEN_LABEL_CHARS)
    kept = kept[:_MAX_LABEL].strip()
    return kept or _DEFAULT_LABEL


def _as_list(value: Any) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return [v for v in value if isinstance(v, dict)]


def _letter(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = chr(value) if value > 0 else ""
    text = str(value or "").strip().upper()
    return text if len(text) == 1 and text.isalpha() else ""


def _is_usb(bus: Any) -> bool:
    if isinstance(bus, bool):
        return False
    if isinstance(bus, int):
        return bus == _USB_BUS_TYPE
    return isinstance(bus, str) and bus.strip().upper() in ("USB", str(_USB_BUS_TYPE))


def parse_disks(text: str) -> list[UsbDisk]:
    if not text.strip():
        return []
    data = json.loads(text)
    parts = _as_list(data.get("parts"))
    vols = _as_list(data.get("vols"))
    result = []
    for d in _as_list(data.get("disks")):
        size, serial = d.get("Size"), d.get("SerialNumber")
        # `is not False` so that null/missing counts as unknown -> excluded.
        if (
            not _is_usb(d.get("BusType"))
            or d.get("IsSystem") is not False
            or d.get("IsBoot") is not False
            or not isinstance(size, int) or isinstance(size, bool)
            or not 0 < size < MAX_SIZE
            or not isinstance(serial, str) or not serial.strip()
            or not isinstance(d.get("Number"), int)
        ):
            continue
        letters = tuple(sorted({
            _letter(p.get("DriveLetter")) for p in parts
            if p.get("DiskNumber") == d["Number"]
        } - {""}))
        label = filesystem = ""
        if letters:
            vol = next((v for v in vols if _letter(v.get("DriveLetter")) == letters[0]), {})
            label = vol.get("FileSystemLabel") or ""
            filesystem = vol.get("FileSystem") or ""
        result.append(UsbDisk(
            number=d["Number"],
            model=str(d.get("FriendlyName") or ""),
            serial=serial.strip(),
            size=size,
            letters=letters,
            label=label,
            filesystem=filesystem,
        ))
    return result


def list_usb_disks(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[UsbDisk]:
    proc = run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", LIST_SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return parse_disks(proc.stdout)
