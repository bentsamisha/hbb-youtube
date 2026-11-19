# HoneyBadger and Bees: video pipeline

Raw talking-head footage + episode script in, finished 4K YouTube episode + upload kit out. Modelled on the
Trust No Pack pipeline (`~/TNP/System/pipeline`), rebuilt for long landscape talking-head episodes.

## Files
- `hbb.py`: the autopilot (inbox watcher, Claude editor, review folders, change requests, approval, scheduling,
  YouTube upload, notifications). `hbb.py` without arguments lists the commands.
- `editor.py`: the editing engine. `analyze` (Whisper transcript, silence map, script matching, automatic edit),
  `render` (cut, audio chain, subtitles, thumbnail; the picture comes from `motion.py`). `stills` renders single frames.
- `motion.py` + `gfx.py`: the EP.11 v3 look. Camera (punch-ins, split-screen slide), every graphic template
  (title, name, lower, chapter + tracker, statement, list, card, quote, compare, cards, LinkedIn, subscribe, brand)
  and the end card, composited frame by frame and piped to ffmpeg. Graphics are listed in edit.json `graphics`.
- `scripts.py`: parses the Video_N_Script docs (prompter text + production table cues) and exports them from Drive.
- `publish.py`: YouTube Data API upload (schedule, thumbnail, captions, playlist) and Drive read access.
- `EDITOR.md`: what the headless Claude editor does. `EDITOR_NOTES.md`: lasting feedback.
- `config.json`: look, cut, audio, brand, schedule. `assets/`: logos and fonts (Fraunces and Outfit for the video, DM Serif Display and Inter kept for reference; SIL OFL).

## Manual use
    .venv/bin/python editor.py analyze "<video or folder>" --episode 12   # -> System/work/<job>/draft.json
    .venv/bin/python editor.py render <job>                               # -> System/work/<job>/final.mp4
    .venv/bin/python editor.py sample <job> 60 90                         # render just output seconds 60-90
    .venv/bin/python hbb.py run "<video or folder>" --episode 12          # whole thing into "2 Review"

All times in draft.json / edit.json are source seconds; the renderer maps them to the cut timeline, so chapters
and card times in YOUTUBE.txt are exact.
