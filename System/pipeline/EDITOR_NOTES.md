# Editor notes (Mykhailo's lasting feedback)

The Claude editor reads this before every edit and appends a dated line when a change request says it's a lasting rule.

- 2026-11-19: Episodes 1-11 are published and locked; never re-edit or re-upload them.
- 2026-11-19: Descript labels the speaker "Michael"; captions and text always say "Mykhailo".
- 2026-11-19 (Ep 12 review): **Retakes: use his LATEST take** of a line. He re-records until it's right, so the last
  complete attempt is the one he wants. Don't stitch an earlier take with a later one.
- 2026-11-19 (Ep 12 review): **His face must never go down** (looking at notes/prompter between attempts). End a kept
  range BEFORE his head starts to move and start the next one only once he is looking at the lens again. Check with
  frames: for every cut, look at a frame ~0.3 s before each range end and ~0.2 s after each range start; if he is
  looking down, move the cut.
- 2026-11-19 (Ep 12 review): **No jumpy joins.** Never join two pieces of one sentence across a long pause or a
  head/position change ("Add one question to your change | or release process" jumped in sound and picture). Use one
  continuous take of the whole sentence; if none exists, cut at a sentence boundary in the silence, not mid-sentence.
- 2026-11-19 (Ep 12 review): **Don't rush the ending.** In the sign-off and the next-video tease keep natural pauses
  (0.5-0.8 s) between sentences, and a full beat before "link below" / the last line. Fast-cut endings feel like
  he's being chopped off. Elsewhere the normal tight pauses are fine.
- 2026-11-19 (Ep 12 review): the company card (`brand`) is small and sits low in the bottom-left; the `subscribe`
  graphic is only the button, bottom-right, no extra line of text. Both are built that way in motion.py.
- 2026-11-19 (Ep 12 review): every video gets the channel's own generated music (assets/music, mixed automatically):
  a calm bed under the talk and a track that builds during the next-video tease and lands on the end card. Nothing to
  do in edit.json; just keep the tease as the last spoken part before the end card.
- 2026-11-20 (Ep 13 review): **Text inside every yellow (or navy) pill is centred, with the same space before and after
  the words.** motion.py draws all pills through `pill()`, which centres by the real ink. A `tag` under a list sits
  right under the last item; never let a pill float alone at the bottom of a panel.
- 2026-11-20 (Ep 13 review): when he repeats a phrase ("in July ... in July") or restarts a sentence, keep only the
  last complete version; never leave both. Drop throwaway fragments between two lines (e.g. a half-started aside
  before "I'm not a lawyer") so one line runs straight into the next.
