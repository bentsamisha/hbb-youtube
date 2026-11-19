# HBB autopilot: how it works

```
1 Inbox  ->  transcribe + match the script + Claude edit + render  ->  2 Review  ->  (you) 3 Approved  ->  4 Scheduled  ->  5 Posted
```

## Weekly use (about 15 minutes of your time after recording)
1. Record the episode from the prompter. Retakes are fine: just say the sentence or paragraph again,
   the pipeline keeps the **last complete take** of every script paragraph.
2. Copy the clips from the SD card (`PRIVATE/M4ROOT/CLIP/C0001.MP4` ...) into a new folder in **`HBB/1 Inbox`**
   named after the episode, e.g. `EP12` (one file is fine too: `EP12.MP4`).
   Once picked up, the footage moves to `Raw Footage`.
3. You get "New recording", and 20-40 minutes later "Ready for review" (4K takes a while on the MacBook).
4. Open **`HBB/2 Review/EP.12 <name>/`**:
   - watch `EP.12 <name>.mp4` and look at `cover.jpg`
   - read `YOUTUBE.txt` (title options, description, tags, pinned comment, card times)
   - B-roll: the script's B-roll moments are already covered by animated explainers (like EP.11 v3). If you still want
     a real clip somewhere, save it as `broll_01.mp4` ... in the episode's `B-roll` folder and say where in
     `CHANGES.txt`; the video is rendered again by itself ("Updated" notification).
   - **Want changes?** Create `CHANGES.txt` in the episode folder and write in plain words what to change
     ("use the first take of the intro", "the on-screen text at 02:10 should say ...", "title option 2").
     Claude redoes it. If it's a lasting rule, say so; it goes into `System/pipeline/EDITOR_NOTES.md` for every future video.
   - **Happy?** Drag the whole folder into **`3 Approved`**.
5. Approved: uploaded right away as private with a publish time (next free Monday 09:00 Lisbon), with the thumbnail,
   subtitles, description and tags. YouTube publishes it itself.
6. After it's live: pin the comment from `YOUTUBE.txt`, add the cards and end screen in YouTube Studio
   (the API can't set those), and use the LinkedIn hook for that day's post.

## What the edit does
- **Retakes and pauses**: every paragraph of the production table is matched to the transcript; the last complete
  take wins, repeated phrases inside a take are cut, pauses longer than about half a second are tightened.
- **The look is the published EP.11 v3**: navy cards with a gold edge, Fraunces + Outfit type, honeycomb hexes.
  Claude places the graphics from the script's Details column, timed to the words you say:
  hook title and name card, key lines, chapter stings with a 1-2-3 hex tracker, split screens (you slide right, a
  cream panel opens on the left) for agendas, lists, checklists and definitions, full-screen explainers instead of
  AI B-roll (clause on a paper sheet with a highlighter, two-column comparisons, citation cards), the LinkedIn card,
  the subscribe click and the brand card at the sign-off.
- **Camera feel**: slow zoom in at the opening, a 1.2x punch-in that alternates at bigger cuts, a very slow push,
  a gentle zoom out at the sign-off.
- **Captions**: a clean `.srt` from the transcript, uploaded as YouTube subtitles (not burned in, like v3).
- **End screen**: 12 s cream honeycomb card with the logo, the tagline and two frames for YouTube's end-screen videos.
- **Output**: 1080p 25 fps (the graphics are drawn at 1080p, like v3).
- **Sound**: rumble filter, light denoise (the LG air conditioner), gentle compression, loudness -14 LUFS for YouTube.

## Settings (`System/pipeline/config.json`)
- `autopilot.first_slot_date`: no video goes live before this day (set to 2026-11-30 so Ep 12 is the first). Clear it once it has passed.
- `autopilot.post_times`: e.g. `{"mon": ["09:00"], "thu": ["17:00"]}` for two slots a week.
- `look.face_x` / `face_y`: where your face sits in the frame (0..1), used for punch-ins and the thumbnail.
- `brand`: name, role line, tagline and colours used on the cards.
- `fixes`: words Whisper mishears, `[heard, correct]`.

## Check the queue
```bash
~/HBB/System/pipeline/.venv/bin/python ~/HBB/System/pipeline/hbb.py status
```
The Mac must be awake for the edit and the upload. If it sleeps, everything continues when it wakes up.
