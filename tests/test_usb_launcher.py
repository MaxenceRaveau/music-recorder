import ctypes
from pathlib import Path

import pytest

from audiorec.usb import launcher
from audiorec.usb.helper import FormatRequest
from audiorec.usb.launcher import start


def _req(tmp_path, label="MY STICK"):
    return FormatRequest(
        disk_number=3, serial="ABC123", size=16_000_000_000, label=label,
        progress_path=tmp_path / "my progress" / "p.jsonl",
    )


def test_helper_command_uses_pythonw_and_quotes(tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    req = _req(tmp_path)
    exe, params = launcher.helper_command(req, python=str(tmp_path / "python.exe"))
    assert exe.endswith("pythonw.exe")
    assert params.startswith("-m audiorec.usb.helper")
    assert '"--label=MY STICK"' in params
    assert f'"--progress={req.progress_path}"' in params
    assert "--disk=3" in params


def test_helper_command_falls_back_to_python(tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    exe, _ = launcher.helper_command(_req(tmp_path), python=str(tmp_path / "python.exe"))
    assert exe.endswith("python.exe") and not exe.endswith("pythonw.exe")


def test_project_root_contains_package():
    assert (launcher.PROJECT_ROOT / "audiorec" / "usb" / "launcher.py").exists()


def test_start_passes_command_and_root(tmp_path):
    seen = []
    sentinel = object()

    def fake(exe, params, cwd):
        seen.append((exe, params, cwd))
        return sentinel

    assert start(_req(tmp_path), shell_execute=fake) is sentinel
    assert seen[0][1].startswith("-m audiorec.usb.helper")
    assert Path(seen[0][2]) == launcher.PROJECT_ROOT


def test_declined_uac_returns_none(tmp_path):
    assert start(_req(tmp_path), shell_execute=lambda *a: None) is None


def test_launch_failure_raises(tmp_path):
    def boom(*a):
        raise OSError("The system cannot find the file specified")

    with pytest.raises(OSError):
        start(_req(tmp_path), shell_execute=boom)


def test_shellexecuteinfo_size():
    assert ctypes.sizeof(launcher._SHELLEXECUTEINFOW) == (112 if ctypes.sizeof(ctypes.c_void_p) == 8 else 60)
