# Audio Recorder

Windows app that records whatever your PC is playing (WASAPI loopback). Record,
pause and resume, trim the start and end on a waveform, preview the selection,
tag it, then save once as MP3 (320 kbps), FLAC or WAV. A command-line recorder
is also included.

## Install

```
python -m pip install -r requirements.txt
```

## Run the GUI

```
python recorder_gui.py
```

Optional desktop shortcut that opens the window without a console:

```
powershell -NoProfile -ExecutionPolicy Bypass -File make_shortcut.ps1
```

Use `-Destination <folder>` to create the shortcut somewhere other than the Desktop.

## Run the CLI

Records straight to a 320 kbps MP3. Stop with Ctrl+C.

```
python record_system_audio.py                  # default output device
python record_system_audio.py --list           # show loopback devices
python record_system_audio.py --device 12 --outdir D:/recordings
```

## Where files go

- Saved recordings: the folder you choose in the window (default `recordings/`
  next to the script).
- In-progress work: `%LOCALAPPDATA%\AudioRecorder\sessions\<YYYYmmdd_HHMMSS>\`
  (`audio.pcm` and `session.json`). These files are deleted only after a
  successful Save or a confirmed Discard.
- Crash recovery: if the app was killed or crashed mid-recording, the next
  launch offers to open the leftover session (then trim and save as usual) or
  delete it.

## MP3 sample rate

MP3 only supports 32000, 44100 and 48000 Hz and nothing is resampled. If your
output device runs at another rate, MP3 is unavailable: switch the device to
48 kHz in Windows sound settings, or save as FLAC or WAV.

## Tests

```
python -m pytest
```
