import json
import subprocess

import pytest

from audiorec.usb.disks import UsbDisk, clean_label, list_usb_disks, parse_disks

GB = 1000**3
TB = 1024**4


def _disk(number, bus="USB", size=64 * GB, system=False, boot=False, serial="SN", **extra):
    d = {"Number": number, "FriendlyName": f"Disk {number}", "SerialNumber": serial,
         "Size": size, "BusType": bus, "IsSystem": system, "IsBoot": boot}
    d.update(extra)
    return d


def _text(disks, parts=None, vols=None):
    return json.dumps({"disks": disks, "parts": parts, "vols": vols})


def test_only_usb_non_system_under_2tb_are_kept():
    text = _text([
        _disk(0, bus="SATA", system=True),
        _disk(1, bus="NVMe"),
        _disk(2),
        _disk(3, boot=True),
        _disk(4, size=4 * TB),
    ])
    assert [d.number for d in parse_disks(text)] == [2]


def test_bus_type_accepts_number_seven():
    assert [d.number for d in parse_disks(_text([_disk(5, bus=7)]))] == [5]


def test_unknown_safety_fields_exclude_the_disk():
    missing_system = _disk(1)
    del missing_system["IsSystem"]
    null_boot = _disk(2, boot=None)
    no_size = _disk(3)
    del no_size["Size"]
    null_size = _disk(4, size=None)
    no_serial = _disk(5, serial=None)
    blank_serial = _disk(6, serial="  ")
    ok = _disk(7)
    text = _text([missing_system, null_boot, no_size, null_size, no_serial, blank_serial, ok])
    assert [d.number for d in parse_disks(text)] == [7]


def test_single_object_json_is_accepted():
    text = _text(
        _disk(2),
        {"DiskNumber": 2, "DriveLetter": "E"},
        {"DriveLetter": "E", "FileSystemLabel": "STICK", "FileSystem": "FAT32"},
    )
    (d,) = parse_disks(text)
    assert d == UsbDisk(2, "Disk 2", "SN", 64 * GB, ("E",), "STICK", "FAT32")


def test_letters_label_and_filesystem():
    text = _text(
        [_disk(2), _disk(9)],
        [{"DiskNumber": 2, "DriveLetter": "F"}, {"DiskNumber": 2, "DriveLetter": "E"},
         {"DiskNumber": 9, "DriveLetter": "G"}],
        [{"DriveLetter": "F", "FileSystemLabel": "OTHER", "FileSystem": "NTFS"},
         {"DriveLetter": "E", "FileSystemLabel": "MUSIC", "FileSystem": "exFAT"},
         {"DriveLetter": "G", "FileSystemLabel": "X", "FileSystem": "FAT32"}],
    )
    d = parse_disks(text)[0]
    assert d.letters == ("E", "F")
    assert (d.label, d.filesystem) == ("MUSIC", "exFAT")
    assert d.display == "E:  MUSIC  64.0 GB  exFAT"


def test_partitions_without_letter_are_ignored():
    # PowerShell serialises a missing letter as 0 or "\u0000"
    text = _text([_disk(2)], [{"DiskNumber": 2, "DriveLetter": 0},
                              {"DiskNumber": 2, "DriveLetter": "\u0000"}], None)
    assert parse_disks(text)[0].letters == ()


def test_disk_without_letter_displays_no_letter():
    (d,) = parse_disks(_text([_disk(2)]))
    assert d.display.startswith("(no letter)")
    assert d.display.endswith("no file system")


def test_empty_output_is_no_disks():
    assert parse_disks("") == []
    assert parse_disks("  \n") == []


def test_list_usb_disks_raises_on_error():
    fake = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "Access denied")
    with pytest.raises(RuntimeError, match="Access denied"):
        list_usb_disks(run=fake)


def test_list_usb_disks_parses_stdout():
    out = _text([_disk(2)])
    fake = lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")
    assert [d.number for d in list_usb_disks(run=fake)] == [2]


@pytest.mark.parametrize("raw,out", [
    ("my stick", "MY STICK"),
    ("a.b:c*d", "ABCD"),
    ("abcdefghijklmn", "ABCDEFGHIJK"),
    ("", "REKORDBOX"),
    ("...", "REKORDBOX"),
])
def test_clean_label(raw, out):
    assert clean_label(raw) == out


def test_clean_label_removes_backslash_and_brackets():
    assert clean_label("a" + chr(92) + "b[c]d|e\"f") == "ABCDEF"
