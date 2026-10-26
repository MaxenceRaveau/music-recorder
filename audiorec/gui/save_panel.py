"""
The save panel: Title, Artist, File name, Save-to folder, a collapsible
"More details" section (Album, Year, Genre, Track #, Cover) and Discard/Save.
It owns the file-name auto-fill; the window owns dialogs, state and saving.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton,
                               QToolButton, QWidget)

from audiorec.export import Tags
from audiorec.naming import build_filename, with_extension

FIELD_NAMES = ("title", "artist", "album", "year", "genre", "track", "cover", "filename", "folder")


def _row(*widgets: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        h.addWidget(x, 1 if isinstance(x, QLineEdit) else 0)
    return w


class SavePanel(QGroupBox):
    edited = Signal()           # any field changed (by the user or by auto-fill)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Save", parent)
        self._started: datetime | None = None
        self._fmt = "mp3"
        self._filename_edited = False

        form = QFormLayout(self)
        self.title_edit, self.artist_edit, self.filename_edit = QLineEdit(), QLineEdit(), QLineEdit()
        self.folder_edit = QLineEdit()
        self.browse_button = QPushButton("Browse…")
        form.addRow("Title", self.title_edit)
        form.addRow("Artist", self.artist_edit)
        form.addRow("File name", self.filename_edit)
        form.addRow("Save to", _row(self.folder_edit, self.browse_button))

        self.details_toggle = QToolButton(text="More details", checkable=True,
                                          toolButtonStyle=Qt.ToolButtonTextBesideIcon,
                                          arrowType=Qt.RightArrow, autoRaise=True)
        form.addRow(self.details_toggle)
        self.details = QWidget()
        details = QFormLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        self.album_edit, self.year_edit, self.genre_edit = QLineEdit(), QLineEdit(), QLineEdit()
        self.track_edit, self.cover_edit = QLineEdit(), QLineEdit()
        self.year_edit.setPlaceholderText("e.g. 2026")
        self.track_edit.setPlaceholderText("e.g. 3 or 3/12")
        self.cover_button = QPushButton("Choose…")
        details.addRow("Album", self.album_edit)
        details.addRow("Year", self.year_edit)
        details.addRow("Genre", self.genre_edit)
        details.addRow("Track #", self.track_edit)
        details.addRow("Cover image", _row(self.cover_edit, self.cover_button))
        self.details.hide()
        form.addRow(self.details)

        self.discard_button = QPushButton("Discard")
        self.save_button = QPushButton("Save", default=True)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.discard_button)
        buttons.addWidget(self.save_button)
        form.addRow(buttons)

        self._edits = dict(zip(FIELD_NAMES, (
            self.title_edit, self.artist_edit, self.album_edit, self.year_edit, self.genre_edit,
            self.track_edit, self.cover_edit, self.filename_edit, self.folder_edit)))
        self.details_toggle.toggled.connect(self._on_details_toggled)
        self.title_edit.textChanged.connect(self._on_name_changed)
        self.artist_edit.textChanged.connect(self._on_name_changed)
        self.filename_edit.textEdited.connect(self._on_filename_edited)
        for edit in self._edits.values():
            edit.textChanged.connect(self.edited)

    def set_names_only(self, on: bool) -> None:
        """While recording: only Title and Artist can be typed into."""
        for w in (self.filename_edit, self.folder_edit, self.browse_button, self.details_toggle,
                  self.details, self.discard_button, self.save_button):
            w.setEnabled(not on)

    def _on_details_toggled(self, shown: bool) -> None:
        self.details.setVisible(shown)
        self.details_toggle.setArrowType(Qt.DownArrow if shown else Qt.RightArrow)

    # ---- file-name auto-fill ----------------------------------------------
    def start_naming(self, started: datetime, fmt: str) -> None:
        """A recording exists: fill the file name from now on."""
        self._started, self._fmt = started, fmt
        self._autofill()

    def set_format(self, fmt: str) -> None:
        self._fmt = fmt
        if self._started is None:
            return
        if self._filename_edited:
            self.filename_edit.setText(with_extension(self.filename_edit.text(), fmt))
        else:
            self._autofill()

    def _autofill(self) -> None:
        if self._started is not None and not self._filename_edited:
            self.filename_edit.setText(build_filename(self.artist_edit.text(), self.title_edit.text(),
                                                      self._fmt, self._started))

    def _on_name_changed(self) -> None:
        self._autofill()

    def _on_filename_edited(self) -> None:
        self._filename_edited = True    # the user's name wins; only the extension follows

    # ---- values -----------------------------------------------------------
    def fields(self) -> dict[str, str]:
        return {name: edit.text() for name, edit in self._edits.items()}

    def tags(self) -> Tags:
        v = {name: edit.text().strip() for name, edit in self._edits.items()}
        return Tags(title=v["title"], artist=v["artist"], album=v["album"], year=v["year"],
                    genre=v["genre"], track=v["track"],
                    cover=Path(v["cover"]) if v["cover"] else None)

    def reset(self) -> None:
        """Back to empty for the next recording; the folder is kept."""
        self._started = None
        self._filename_edited = False
        for name, edit in self._edits.items():
            if name != "folder":
                edit.clear()
