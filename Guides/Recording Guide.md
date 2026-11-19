# Recording for the pipeline (Sony ZV-E10 II + Elgato Prompter)

- **One episode per session**, clips in order. Several clips are fine; they are joined by file name (C0001, C0002 ...).
- **Retakes**: don't stop the camera. Pause a second, then say the whole sentence (or paragraph) again from its start.
  The last complete take wins, so never "fix" a line by saying only the second half.
- **Read the script as written**: the pipeline matches what you say to the production table. Small paraphrases are fine;
  a whole paragraph said very differently may not be found (the editor then searches the transcript for it).
- **Framing**: keep your face in the same place every week (right of centre, eyes about a third from the top).
  If you move it, update `look.face_x` / `face_y` in `config.json` so punch-ins and thumbnails stay on your face.
- **Camera**: 4K 25p, XAVC S 4:2:0 8-bit is plenty (10-bit 4:2:2 also works, just bigger). Picture profile off or a
  natural one: the pipeline doesn't colour-grade.
- **Audio**: DJI Mic 3 into the camera (or the Yeti via Wave XLR into the camera's mic input). Switch the air conditioner
  off while recording; the denoise only handles a light hum.
- **Silence before the first line**: 2 seconds, so the first word isn't clipped.
- Anything you say to yourself ("ok, again", "wait") is cut automatically when it's not in the script.
