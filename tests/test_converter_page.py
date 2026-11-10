"""ConverterPage tests: a real library built with ffmpeg, dialogs replaced
through the page's wrapper methods, scan and convert run on their threads."""
from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QSettings, Qt

from audiorec.convert import runner as runner_mod
from audiorec.convert.scan import Action, Job
from audiorec.gui.converter_page import ConverterPage
from audiorec.gui.settings import AppSettings
from tests.audio_files import make_audio

FLAC = ["-c:a", "flac"]
MP3 = ["-c:a", "libmp3lame", "-b:a", "192k"]
WAV = ["-c:a", "pcm_s16le"]


@pytest.fixture
def make_page(qtbot, tmp_path):
    pages = []

    def make(lib=None, dest=None, **kwargs):
        qs = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        settings = AppSettings(qs)
        if lib is not None:
            settings.conv_library, settings.conv_dest = lib, dest
        page = ConverterPage(settings, **kwargs)
        qtbot.addWidget(page)
        page.calls = []
        page._error = lambda title, text: page.calls.append(("error", text))
        page._confirm = lambda title, text: page.calls.append(("confirm", text)) or True
        page._set_clipboard = lambda text: page.calls.append(("clipboard", text))
        pages.append(page)
        return page

    yield make
    for page in pages:
        page.shutdown()


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    make_audio(lib / "House" / "A" / "t1.flac", codec_args=FLAC)
    make_audio(lib / "House" / "t2.mp3", codec_args=MP3)
    make_audio(lib / "Techno" / "t3.wav", codec_args=WAV)
    (lib / "cover.jpg").write_bytes(b"x")
    return lib


@pytest.fixture
def page_with_library(make_page, library, tmp_path):
    return make_page(library, tmp_path / "out")


def scanned(qtbot, page):
    page.scan_button.click()
    qtbot.waitUntil(lambda: page.tree.topLevelItemCount() > 0 and page._thread is None,
                    timeout=10000)
    return page


def convert(qtbot, page):
    page.convert_button.click()
    qtbot.waitUntil(lambda: page._thread is None, timeout=20000)


def top_items(page):
    return {page.tree.topLevelItem(i).text(0): page.tree.topLevelItem(i)
            for i in range(page.tree.topLevelItemCount())}


def test_scan_fills_tree_all_checked_and_summary(qtbot, page_with_library):
    page = scanned(qtbot, page_with_library)
    items = top_items(page)
    assert list(items) == ["House", "Techno"]
    assert all(it.checkState(0) == Qt.Checked for it in items.values())
    house = items["House"]
    assert [house.child(i).text(0) for i in range(house.childCount())] == \
        ["(tracks in this folder)", "A"]
    assert house.text(1) == "2" and items["Techno"].text(1) == "1"
    assert page.summary_convert.text() == "2"
    assert page.summary_copy.text() == "1"
    assert page.summary_done.text() == "0"
    assert page.size_label.text().startswith("Estimated output: ~")
    assert "Free on" in page.size_label.text()
    assert page.convert_button.text() == "Convert 3 files"
    assert page.convert_button.isEnabled()


def test_untick_folder_updates_summary(qtbot, page_with_library):
    page = scanned(qtbot, page_with_library)
    top_items(page)["Techno"].setCheckState(0, Qt.Unchecked)
    qtbot.waitUntil(lambda: page.convert_button.text() == "Convert 2 files")
    assert page.summary_convert.text() == "1"
    house = top_items(page)["House"]
    house.child(0).setCheckState(0, Qt.Unchecked)       # loose tracks of House
    assert house.checkState(0) == Qt.PartiallyChecked
    qtbot.waitUntil(lambda: page.convert_button.text() == "Convert 1 files")
    page.select_none.click()
    assert not page.convert_button.isEnabled()
    assert page.summary_convert.text() == "0" and page.summary_copy.text() == "0"
    page.select_all.click()
    assert page.convert_button.text() == "Convert 3 files"


def test_invalid_destination_shows_error(qtbot, page_with_library, library):
    page = page_with_library
    page.dest_edit.setText(str(library / "House"))
    page.scan_button.click()
    assert page.calls == [("error", "Destination can't be inside the library folder.")]
    assert page._thread is None and page.tree.topLevelItemCount() == 0


def test_changing_format_clears_tree(qtbot, page_with_library):
    page = scanned(qtbot, page_with_library)
    page.format_combo.setCurrentIndex(page.format_combo.findData("wav"))
    assert page.tree.topLevelItemCount() == 0
    assert not page.convert_button.isEnabled()


def test_convert_runs_and_reports(qtbot, page_with_library, tmp_path):
    page = scanned(qtbot, page_with_library)
    convert(qtbot, page)
    out = tmp_path / "out"
    for rel in ("House/A/t1.mp3", "House/t2.mp3", "Techno/t3.mp3"):
        assert (out / rel).exists()
    assert "Converted 2 · Copied 1 · Already done 0 · Failed 0" == page.counts_label.text()
    assert page.log.count() == 3
    assert page.progress.value() == page.progress.maximum() == 3
    assert not page.copy_failures_button.isEnabled()
    assert page.scan_button.isEnabled() and page.library_edit.isEnabled()


def test_failures_and_collisions_reported_and_copied(qtbot, make_page, tmp_path):
    lib = tmp_path / "lib"
    make_audio(lib / "Song.flac", codec_args=FLAC)
    make_audio(lib / "Song.mp3", codec_args=MP3)                # wins the collision
    (lib / "bad.flac").write_bytes(b"not audio" * 100)
    page = scanned(qtbot, make_page(lib, tmp_path / "out"))
    assert page.convert_button.text() == "Convert 2 files"
    convert(qtbot, page)
    assert page.counts_label.text() == "Converted 0 · Copied 1 · Already done 0 · Failed 2"
    assert page.copy_failures_button.isEnabled()
    page.copy_failures_button.click()
    kind, text = page.calls[-1]
    assert kind == "clipboard"
    lines = text.splitlines()
    assert len(lines) == 2
    assert any(l.startswith(str(lib / "Song.flac") + " — same output name as Song.mp3")
               for l in lines)
    assert any(l.startswith(str(lib / "bad.flac") + " — ") for l in lines)


def test_untick_parent_recomputes_summary_once(qtbot, make_page, tmp_path):
    page = make_page()
    runs = []
    original = page._update_summary
    page._update_summary = lambda: runs.append(1) or original()
    lib, out = tmp_path / "lib", tmp_path / "out"
    jobs = [Job(src=lib / f"Genre/Album{i:03d}/t.flac", dst=out / f"Genre/Album{i:03d}/t.mp3",
                group=f"Genre/Album{i:03d}", action=Action.CONVERT, info=None, est_bytes=1000)
            for i in range(200)]
    page._scanned = (lib, out, "mp3")
    page._on_scan_finished(jobs)
    qtbot.waitUntil(lambda: page.convert_button.text() == "Convert 200 files")
    runs.clear()
    page.tree.topLevelItem(0).setCheckState(0, Qt.Unchecked)
    qtbot.waitUntil(lambda: page.convert_button.text() == "Convert 0 files")
    qtbot.wait(50)
    assert len(runs) == 1


def test_current_label_shows_file_being_processed(qtbot, page_with_library, monkeypatch):
    release = threading.Event()

    def slow_job(job, target, *, register, cancelled):
        release.wait(10)

    monkeypatch.setattr(runner_mod, "process_job", slow_job)
    page = scanned(qtbot, page_with_library)
    page._workers = 1
    page.convert_button.click()
    qtbot.waitUntil(lambda: page.current_label.text() not in ("", "Starting…"))
    first = next(j for j in page._jobs if j.action in (Action.CONVERT, Action.COPY))
    assert page.current_label.text() == first.src.relative_to(page._scanned[0]).as_posix()
    assert page.log.count() == 0                        # still running, nothing finished
    release.set()
    qtbot.waitUntil(lambda: page._thread is None, timeout=10000)


def test_runner_crash_does_not_leave_page_converting(qtbot, page_with_library, monkeypatch):
    def boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_mod.Runner, "run", boom)
    page = scanned(qtbot, page_with_library)
    page.convert_button.click()
    qtbot.waitUntil(lambda: page._thread is None, timeout=10000)
    assert any("boom" in page.log.item(i).text() for i in range(page.log.count()))
    assert "boom" in page.current_label.text()
    assert page.scan_button.isEnabled() and page.confirm_close() is True


def test_rescan_after_convert_shows_all_done(qtbot, page_with_library):
    page = scanned(qtbot, page_with_library)
    convert(qtbot, page)
    page.tree.clear()
    scanned(qtbot, page)
    assert page.summary_done.text() == "3"
    assert page.summary_convert.text() == "0" and page.summary_copy.text() == "0"
    assert not page.convert_button.isEnabled()


def test_close_while_converting_asks_and_stops(qtbot, make_page, tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    for n in "abc":
        make_audio(lib / f"{n}.flac", codec_args=FLAC, seconds=30)
    page = scanned(qtbot, make_page(lib, out, workers=1))
    assert page.confirm_close() is True and page.calls == []        # idle: no question
    page.convert_button.click()
    page._confirm = lambda title, text: page.calls.append(("confirm", text)) or False
    assert page.confirm_close() is False
    assert page._thread is not None                                 # refusing has no side effects
    page._confirm = lambda title, text: page.calls.append(("confirm", text)) or True
    assert page.confirm_close() is True
    assert page.calls[-1] == ("confirm", "A conversion is running. Stop it and quit?\n"
                                         "Finished files are kept.")
    page.shutdown()
    assert page._thread is None
    assert not list(out.rglob("*.part"))
    assert len(list(out.rglob("*.mp3"))) < 3


def test_close_while_scanning_does_not_ask(qtbot, page_with_library):
    page = page_with_library
    page.scan_button.click()
    assert page._mode == "scanning"
    assert page.confirm_close() is True and page.calls == []
    page.shutdown()
    assert page._thread is None


def test_format_and_paths_remembered(qtbot, make_page, tmp_path):
    page = make_page()
    page.library_edit.setText(str(tmp_path / "music"))
    page.dest_edit.setText(str(tmp_path / "cdj"))
    page.format_combo.setCurrentIndex(page.format_combo.findData("aiff"))
    again = make_page()
    assert again.library_edit.text() == str(tmp_path / "music")
    assert again.dest_edit.text() == str(tmp_path / "cdj")
    assert again.format_combo.currentData() == "aiff"
