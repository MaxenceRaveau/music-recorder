# Manual test checklist

Run `python recorder_gui.py` and play a real song (for example in Spotify).

- [ ] 1. Ready: waveform empty, Stop disabled.
- [ ] 2. Record, play a song in Spotify; the waveform draws live and the meter moves.
- [ ] 3. Pause 5 s, resume; Stop at the end.
- [ ] 4. Drag markers to cut lead-in and tail; Play previews only the selection; clicking the waveform seeks.
- [ ] 5. Title and Artist typed, file name auto-fills; open More details, add album and cover.
- [ ] 6. Save as MP3, then repeat with FLAC. Open both in a music player: correct length (pause excluded), tags and cover shown, no audible glitch at the pause point.
- [ ] 7. Record again and kill the process from Task Manager; relaunch, recovery dialog appears; Open, trim, Save works.
- [ ] 8. Save into a file that is open or locked elsewhere: an error is shown and the recording is still there.
- [ ] 9. CLI: `python record_system_audio.py --list` lists devices; then run `python record_system_audio.py`, play audio about 10 s, pause playback 3 s, press Ctrl+C. An MP3 appears in `recordings/`, plays, and its length is about the wall-clock time including the 3 s of silence.
- [ ] 10. Shortcut: run `make_shortcut.ps1`; the Desktop shortcut opens the window with no console.
