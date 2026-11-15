# Audio Recorder

Windows app that records whatever your PC is playing (WASAPI loopback). Record,
pause and resume, trim the start and end on a waveform, preview the selection,
tag it, then save once as MP3 (320 kbps), FLAC or WAV.

## Install

```
uv sync
```

## Run the GUI

```
uv run recorder_gui.py
```

or `make run`.

Optional desktop shortcut that opens the window without a console:

```
powershell -NoProfile -ExecutionPolicy Bypass -File make_shortcut.ps1
```

Use `-Destination <folder>` to create the shortcut somewhere other than the Desktop.

## Where files go

- Saved recordings: the folder you choose in the window (default `recordings/`
  next to the app).
- In-progress work: `%LOCALAPPDATA%\AudioRecorder\sessions\<YYYYmmdd_HHMMSS>\`
  (`audio.pcm` and `session.json`). These files are deleted only after a
  successful Save or a confirmed Discard.
- Crash recovery: if the app was killed or crashed mid-recording, the next
  launch offers to open the leftover session (then trim and save as usual) or
  delete it.

## Converter

The Converter page (sidebar, next to Recorder) copies a music library into
another folder in formats that play on Pioneer CDJ decks, down to the original
CDJ-2000. Set the library and destination, pick a format, Scan, untick any
folders you don't want, then Convert. The folder tree is kept.

- Formats: MP3 320 kbps (default), AAC/M4A 320 kbps, WAV, AIFF. All four play
  on a CDJ-2000 and later.
- A file already in the chosen format, at or below its quality and within the
  CDJ-2000 limits, is copied unchanged; everything else is converted. Non-audio
  files (cover art, playlists, logs) are not copied.
- Output is capped at 48 kHz and 24-bit.
- Running it again only processes new files: anything already in the
  destination is skipped.
- The destination is a folder on your PC, meant to be imported into Rekordbox,
  which then exports to USB.
- ffmpeg is bundled, so there is nothing else to install.

## MP3 sample rate

MP3 only supports 32000, 44100 and 48000 Hz and nothing is resampled. If your
output device runs at another rate, MP3 is unavailable: switch the device to
48 kHz in Windows sound settings, or save as FLAC or WAV.

## Tests

```
uv run pytest
```
