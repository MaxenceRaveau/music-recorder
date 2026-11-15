"""UsbPage tests: fake disk listing, fake launcher and Launch; nothing is ever
started for real. The erase dialog is replaced except in its own test."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialogButtonBox

from audiorec.gui.usb_page import EraseDialog, UsbPage
from audiorec.usb import progress
from audiorec.usb.disks import UsbDisk

STICK = UsbDisk(number=3, model="SanDisk Ultra", serial="AA11", size=64_000_000_000,
                letters=("E",), label="sandisk", filesystem="exFAT")


class FakeLaunch:
    def __init__(self, exited: bool = True, code: int | None = 0) -> None:
        self.exited, self.code = exited, code
        self.calls: list[str] = []

    def wait(self, timeout_ms: int) -> bool:
        self.calls.append("wait")
        return self.exited

    def exit_code(self) -> int | None:
        self.calls.append("exit_code")
        return self.code

    def close(self) -> None:
        self.calls.append("close")


class FakeStart:
    """Records requests; writes the scripted progress lines, returns ``launch``."""

    def __init__(self, launch: FakeLaunch | None, lines: list[dict] = ()) -> None:
        self.launch, self.lines = launch, list(lines)
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        for line in self.lines:
            progress.write_line(req.progress_path, **line)
        return self.launch


@pytest.fixture
def make_page(qtbot, tmp_path):
    pages = []

    def make(disks=(STICK,), start=None, ask_erase=True):
        listed = []

        def list_disks():
            listed.append(1)
            if isinstance(disks, Exception):
                raise disks
            return list(disks)

        page = UsbPage(list_disks=list_disks, start=start or FakeStart(None),
                       progress_dir=tmp_path / "usb")
        qtbot.addWidget(page)
        page.listed = listed
        page.calls = []
        page._ask_erase = lambda disk: page.calls.append(("ask", disk)) or ask_erase
        page._error = lambda title, text: page.calls.append(("error", text))
        page._confirm = lambda title, text: page.calls.append(("confirm", text)) or True
        pages.append(page)
        page.refresh()
        qtbot.waitUntil(lambda: page._thread is None and bool(listed), timeout=5000)
        return page

    yield make
    for page in pages:
        page.shutdown()


def test_lists_disks_and_sets_label(qtbot, make_page):
    page = make_page()
    assert page.disk_combo.count() == 1
    assert page.disk_combo.itemText(0) == STICK.display
    assert page.label_edit.text() == "SANDISK"
    assert page.info_label.text() == "FAT32 · MBR · whole stick"
    assert page.format_button.text() == "Format…"
    assert page.format_button.isEnabled()
    page.label_edit.setText("my stick.v2")
    page.label_edit.editingFinished.emit()
    assert page.label_edit.text() == "MY STICKV2"


def test_no_disk_disables_format(qtbot, make_page):
    page = make_page(disks=[])
    assert page.disk_combo.count() == 1
    assert page.disk_combo.itemText(0) == "No USB stick found"
    assert not page.format_button.isEnabled()


def test_list_error_shown(qtbot, make_page):
    page = make_page(disks=RuntimeError("PowerShell timed out"))
    assert page.status_label.text() == "Couldn't list USB sticks: PowerShell timed out"
    assert not page.format_button.isEnabled()


def test_loads_when_first_shown(qtbot, tmp_path):
    listed = []
    page = UsbPage(list_disks=lambda: listed.append(1) or [STICK], start=FakeStart(None),
                   progress_dir=tmp_path)
    qtbot.addWidget(page)
    assert listed == []
    page.show()
    qtbot.waitUntil(lambda: page._thread is None and page.disk_combo.count() == 1)
    page.hide()
    page.show()
    qtbot.wait(50)
    assert listed == [1]
    page.shutdown()


def test_erase_dialog_requires_exact_letter(qtbot):
    disk = UsbDisk(number=3, model="SanDisk Ultra", serial="AA11", size=64_000_000_000,
                   letters=("E", "F"), label="", filesystem="")
    dialog = EraseDialog(disk)
    qtbot.addWidget(dialog)
    assert dialog.text_label.text() == \
        "Everything on SanDisk Ultra (64.0 GB, drives E:, F:) will be erased."
    assert dialog.confirm_edit.placeholderText() == "Type E to confirm"
    erase = dialog.erase_button
    cancel = dialog.buttons.button(QDialogButtonBox.Cancel)
    assert cancel.isDefault() and not erase.isDefault()
    for text, enabled in (("", False), ("F", False), (" e  ", True)):
        dialog.confirm_edit.setText(text)
        assert erase.isEnabled() is enabled, text


def test_erase_dialog_without_letter_uses_model_word(qtbot):
    disk = UsbDisk(number=3, model="Kingston DataTraveler", serial="AA11", size=8_000_000_000,
                   letters=(), label="", filesystem="")
    dialog = EraseDialog(disk)
    qtbot.addWidget(dialog)
    assert dialog.confirm_edit.placeholderText() == "Type KINGSTON to confirm"
    dialog.confirm_edit.setText("kingston")
    assert dialog.erase_button.isEnabled()


def test_declined_erase_does_nothing(qtbot, make_page):
    start = FakeStart(FakeLaunch())
    page = make_page(start=start, ask_erase=False)
    page.format_button.click()
    assert page.calls == [("ask", STICK)]
    assert start.requests == []


def test_declined_uac_shows_cancelled(qtbot, make_page):
    page = make_page(start=FakeStart(None))
    page.format_button.click()
    assert page.status_label.text() == "Cancelled — nothing was changed."
    assert page.format_button.isEnabled() and page.disk_combo.isEnabled()
    assert page.confirm_close() and page.calls == [("ask", STICK)]


def test_start_failure_shown(qtbot, make_page):
    def start(req):
        raise OSError("The system cannot find the file specified")

    page = make_page(start=start)
    page.format_button.click()
    assert page.status_label.text() == \
        "Couldn't start the format: The system cannot find the file specified"
    assert page.format_button.isEnabled()


def test_success_flow(qtbot, make_page, tmp_path):
    launch = FakeLaunch(exited=False)
    done = "E: is ready — FAT32, 64.0 GB, label SANDISK"
    start = FakeStart(launch, [{"fraction": 0.5, "message": "Writing FAT32…"}])
    page = make_page(start=start)
    page.format_button.click()
    (req,) = start.requests
    assert (req.disk_number, req.serial, req.size, req.label) == (3, "AA11", 64_000_000_000,
                                                                  "SANDISK")
    assert req.progress_path.parent == tmp_path / "usb"
    assert req.progress_path.name.startswith("format-3-")
    assert req.progress_path.suffix == ".jsonl"
    assert not page.format_button.isEnabled() and not page.disk_combo.isEnabled()
    assert not page.refresh_button.isEnabled() and not page.label_edit.isEnabled()
    qtbot.waitUntil(lambda: page.status_label.text() == "Writing FAT32…")
    assert page.progress.value() == page.progress.maximum() // 2
    # The final line lands just before the helper exits.
    progress.write_line(req.progress_path, fraction=1.0, message=done, done=True)
    launch.exited = True
    qtbot.waitUntil(lambda: page.status_label.text() == done)
    assert launch.calls.count("close") == 1
    qtbot.waitUntil(lambda: page._thread is None and len(page.listed) == 2)
    assert page.format_button.isEnabled() and page.disk_combo.isEnabled()
    assert page.progress.value() == page.progress.maximum()
    assert page.status_label.text() == done


def test_helper_error_shown(qtbot, make_page):
    launch = FakeLaunch(exited=True, code=1)
    start = FakeStart(launch, [{"message": "Checking the stick…"},
                               {"error": "Access is denied."}])
    page = make_page(start=start)
    page.format_button.click()
    qtbot.waitUntil(lambda: page.status_label.text() == "Format failed: Access is denied.")
    assert "#b35c00" in page.status_label.styleSheet()
    assert launch.calls.count("close") == 1
    qtbot.waitUntil(lambda: page._thread is None and len(page.listed) == 2)
    assert page.format_button.isEnabled()


def test_helper_died_without_result(qtbot, make_page):
    launch = FakeLaunch(exited=True, code=1)
    page = make_page(start=FakeStart(launch, [{"fraction": 0.2, "message": "Partitioning…"}]))
    page.format_button.click()
    qtbot.waitUntil(lambda: page.status_label.text() ==
                    "Format failed: the helper stopped unexpectedly.")
    assert launch.calls.count("close") == 1


def test_wait_error_treated_as_failure(qtbot, make_page):
    class BrokenLaunch(FakeLaunch):
        def wait(self, timeout_ms):
            raise OSError("The handle is invalid")

    launch = BrokenLaunch()
    page = make_page(start=FakeStart(launch))
    page.format_button.click()
    qtbot.waitUntil(lambda: page.status_label.text().startswith("Format failed:"))
    assert launch.calls.count("close") == 1
    qtbot.waitUntil(lambda: page._thread is None and len(page.listed) == 2)
    assert page.format_button.isEnabled()


def test_close_while_formatting_asks(qtbot, make_page):
    launch = FakeLaunch(exited=False)
    page = make_page(start=FakeStart(launch))
    page.format_button.click()
    assert page.confirm_close()
    assert page.calls == [("ask", STICK), ("confirm", "A USB stick is being formatted.\n"
                                           "It will finish in the background. Quit anyway?")]
    calls_before = list(launch.calls)
    page.shutdown()
    qtbot.wait(300)
    assert launch.calls == calls_before
