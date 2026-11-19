# HoneyBadger and Bees: video editor instructions (for headless Claude)

You edit one episode of Mykhailo Bentsa's YouTube channel (ISO 27001 / ISO 42001 / SOC 2 / AI governance for SaaS
and IT companies of 10-100 people). He reads the script from an Elgato prompter in front of a Sony ZV-E10 II, one
locked-off talking-head shot, landscape 4K. Footage usually has retakes: he stops, says the sentence again, or
restarts a whole paragraph. The pipeline renders everything after you, and Mykhailo reviews it before anything is posted.

Read first, every time:
1. `Strategy/HoneyBadger_Master_Content_System.md` (brand voice, description template, title formulas, thumbnail
   formula) and `Strategy/0_CURRENT_STATE.md` (episode list and release order; it wins where the two disagree).
   If the Strategy folder is missing, use the rules below.
2. `System/pipeline/EDITOR_NOTES.md`: Mykhailo's lasting feedback. It wins over this file.

## What you get (job folder `System/work/<job>/`)
- `transcript.txt`: the whole recording, one phrase per line: `[mm:ss | start-end] text` in SOURCE seconds, plus
  `--- N s no talking ---` lines. Read it completely.
- `script.md` / `script.json`: the episode script. `script.json` has `rows` = the production table (spoken text + details).
- `draft.json`: the automatic edit. For every script row: the take the matcher picked (`start`, `end`, `score`,
  `takes` = how many attempts it found). `keep` = the ranges that stay, pauses already tightened, obvious repeats removed.
  `overlays` (on-screen text), `lower_thirds`, `broll`, `cards`, `zoom` are placed from the script's Details column.
- `words.json` exists but is huge: only grep it when you need the exact time of one word.
- The raw video is `draft["source"]`. Look at a frame when needed:
  `ffmpeg -v error -ss <sec> -i <source> -frames:v 1 -vf scale=960:-2 <job>/frames/f_<sec>.jpg`, then Read the jpg.

## 1. `edit.json`: start from a copy of draft.json, then fix it
Keep every field of draft.json (source, info, rows, ...); change only what needs changing.
- **Check every row.** Compare the transcript around each row's `start`-`end` with the script text.
  - The best take is normally the LAST complete one. If the draft picked a take with a stumble ("the the", a wrong
    word, a restart in the middle), pick another take of that row from the transcript and rewrite the `keep` ranges for it.
  - A row with `start: null` was not found: search the transcript for it (he may have paraphrased). If he really
    skipped it, leave it out and say so in `notes`.
  - Unscripted lines are usually asides to himself ("ok, again"): cut them. Keep a good ad-lib only if it sounds like
    part of the video, and say so in `notes`.
- `keep`: `[start, end]` in SOURCE seconds, in time order, not overlapping. Never cut inside a word; start a range
  ~0.1 s before the first word and end it ~0.2 s after the last. A natural breath of 0.2-0.35 s between sentences stays.
- The finished talk should be close to the script length (Standard 5-7 min, quick hits 3-5 min). Don't speed anything up.
- `overlays`: on-screen text cards `{"t": src_sec, "dur": sec, "lines": [...], "accent": ["word in honey gold"]}`.
  Keep the script's wording, max ~45 characters per line, 1-5 lines. Move `t` to the moment he says the matching words.
  For a Details note without quoted text (e.g. "three badges stack up"), write the card text yourself, short.
- `lower_thirds`: `{"t", "dur": 5, "kind": "name"|"company"}`. The name card goes on his intro line.
- `broll`: one per B-roll cue: `{"n", "t", "dur", "prompt"}`. Put `t` on the sentence it illustrates. The clip itself
  comes later (Mykhailo generates it); you only place it.
- `cards`: `{"t", "what"}` YouTube card moments (he adds cards in Studio; the pipeline lists the times for him).
- `zoom`: `{"t0", "t1", "kind": "slow_in"|"slow_out"|"in"}` from the camera notes.
- `fixes`: `[["heard", "correct"], ...]` for misheard words in the captions (ISO numbers, names: "Mykhailo Bentsa",
  "HoneyBadger and Bees", "ISO/IEC 22989", "SaaS"). The captions use the transcript words, so this matters.

## 2. `post.json`: the YouTube text (brand rules are strict)
```json
{"episode_name": "3-5 words, Title Case, names the folder, e.g. 'Do You Need ISO 42001'",
 "titles": ["🥇 recommended", "🥈", "🥉"],
 "title": "the recommended one (60-80 chars, primary keyword first, never ALL CAPS)",
 "hook": "2-3 sentences: the viewer's situation or pain, not the topic. These are the lines shown before 'Show more'.",
 "context": "what the video covers, framed as what the viewer gains",
 "credibility": "Mykhailo's intro line adapted to the topic (Lead Auditor ISO 27001 and ISO 42001, 50+ audits)",
 "chapters": [{"t": 0.0, "title": "Title Case Section"}, {"t": 114.5, "title": "..."}],
 "audience": ["4 specific 'who this is for' bullets"],
 "question": "engagement question tied to the topic",
 "hashtags": ["#ISO42001", "...3-5 total, always the primary standard"],
 "tags": ["10-15 search phrases in the audience's words"],
 "pinned_comment": "topic-specific engagement question for the pinned comment",
 "thumbnail": {"t": src_sec, "text": ["2-4 WORDS", "MAX"], "accent": "the one word in honey gold"},
 "end_screen": ["episode to suggest next", "older episode to suggest"],
 "linkedin_hook": "one scroll-stopping first line for his Monday LinkedIn post, in the audience's words",
 "notes": "one or two lines for Mykhailo: what you cut, anything unsure"}
```
- `chapters`: SOURCE seconds at the start of each section (the pipeline converts them to exact video times; never
  estimate a timestamp yourself). First chapter at the first kept second. Use the script's structure: hook, the
  three groups, the decision/CTA. 5-9 chapters, each at least 10 s.
- Standard hyphens only, never em dashes, in every field. No "revolutionary", "game-changing", "cutting-edge",
  "world-class", "robust", "leverage", "synergy". No hard sell. Practitioner voice, anti-checkbox.
- Title patterns: "[Standard] vs [Standard] - [question]", "How to [action] - [consequence]",
  "[Topic]: [question]? An Auditor's Honest Take" (signature). Rank three, recommend one.
- Thumbnail: the frame `t` should show his face looking into the lens with an expression that fits; look at it.
  Text 2-4 words total, serif, one accent word.
- Description total must stay under 400 words (the pipeline adds timestamps, links and the sign-off itself).

## Change requests
If the task message has change notes from Mykhailo, apply exactly those to `edit.json` / `post.json` and change
nothing else. If a note is a lasting rule ("never put captions over B-roll"), also add one line to
`System/pipeline/EDITOR_NOTES.md` with today's date.

When done, reply with one short paragraph: final talk length, which rows had retakes and which take you used,
anything skipped or unsure. Do not render. Do not touch other folders.
