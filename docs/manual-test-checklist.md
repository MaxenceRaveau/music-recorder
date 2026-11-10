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
- [ ] 9. Shortcut: run `make_shortcut.ps1`; the Desktop shortcut opens the window with no console.
- [ ] 10. Sidebar: click Converter and back to Recorder; each page shows, and a recording in progress keeps running while you are on Converter.
- [ ] 11. Converter with a real library (FLAC, ALAC, M4A, WMA and MP3 files, plus a cover.jpg): Scan lists the folders; Convert; the destination has the same folder tree, no cover.jpg, the MP3s already at 320 kbps are byte-identical to the source, and converted files keep title, artist, album and track number. The source library is unchanged.
- [ ] 12. Untick a folder and the "(tracks in this folder)" entry; the counts and the Convert button update, and only the ticked folders appear in the destination.
- [ ] 13. Add a corrupt or renamed audio file: it shows as Failed, the run continues, and Copy failures puts the failure list on the clipboard.
- [ ] 14. Stop mid-run: the run ends, finished files are kept, no `.part` files are left in the destination. Convert again: only the unfinished files are processed.
- [ ] 15. Close the app mid-conversion: a question appears; No keeps it running; Yes stops it cleanly and no `.part` files are left.
- [ ] 16. Record while converting a large folder: the waveform keeps drawing, the meter moves, and the saved recording has no dropout or glitch.
- [ ] 17. Quit while recording and converting: the recording question comes first, then the conversion question. Answer No to either and both keep running.
- [ ] 18. Import the destination folder into Rekordbox: tracks, folders and tags show, and a converted track plays. If a CDJ-2000 is available, copy to USB and play one track on it.
