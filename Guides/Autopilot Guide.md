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
   - `B-ROLL.txt` lists the B-roll shots with their AI prompts. Generate them (Veo / Gemini, the B-roll generator HTML,
     or a stock clip), save as `broll_01.mp4`, `broll_02.mp4` ... into the episode's `B-roll` folder.
     The video is rendered again by itself ("Updated" notification).
   - **Want changes?** Create `CHANGES.txt` in the episode folder and write in plain words what to change
     ("use the first take of the intro", "the on-screen text at 02:10 should say ...", "title option 2").
     Claude redoes it. If it's a lasting rule, say so; it goes into `System/pipeline/EDITOR_NOTES.md` for every future video.
   - **Happy?** Drag the whole folder into **`3 Approved`**.
5. Approved: uploaded right away as private with a publish time (next free Monday 09:00 Lisbon), with the thumbnail,
   subtitles, description and tags. YouTube publishes it itself.
6. After it's live: pin the comment from `YOUTUBE.txt`, add the cards and end screen in YouTube Studio
   (the API can't set those), and use the LinkedIn hook for Monday's post.

## What the edit does
- **Retakes and pauses**: every paragraph of the production table is matched to the transcript; the last complete
  take wins, repeated phrases inside a take are cut, pauses longer than about half a second are tightened.
- **Camera feel**: the script's "slow zoom in / gentle zoom out" become slow zooms; after bigger cuts the frame
  switches between normal and a 12 % punch-in, so jump cuts look deliberate.
- **Captions**: burned in (Inter, white on charcoal), plus a clean `.srt` uploaded as YouTube subtitles.
- **On-screen text**: every quoted ON-SCREEN text from the Details column, on a cream card with a honey-gold bar.
- **Name card** on your intro line, company card on the CTA line.
- **B-roll** where the script says B-roll, once you drop the clips in.
- **End screen**: 20 s cream card with the logo and two video frames for YouTube's end-screen elements.
- **Sound**: rumble filter, light denoise (the LG air conditioner), gentle compression, loudness -14 LUFS for YouTube.

## Settings (`System/pipeline/config.json`)
- `autopilot.post_times`: e.g. `{"mon": ["09:00"], "thu": ["17:00"]}` for two slots a week.
- `output.height`: 2160 (4K) or 1080 (faster render).
- `captions.burn`: `false` to only upload subtitles.
- `look.face_x` / `face_y`: where your face sits in the frame (0..1), used for punch-ins and the thumbnail.
- `fixes`: words Whisper mishears, `[heard, correct]`.

## Check the queue
```bash
~/HBB/System/pipeline/.venv/bin/python ~/HBB/System/pipeline/hbb.py status
```
The Mac must be awake for the edit and the upload. If it sleeps, everything continues when it wakes up.
