# Links in YouTube Studio (cards and end screen)

The YouTube API can't add cards or end-screen elements, so this step happens in the browser.

**How it works**
1. When the autopilot uploads a video, it adds a to-do to `System/studio_queue.json`: the video's Studio link, the
   cards (time + which video or page they point to) and the end-screen elements. The same list is in the
   episode's `YOUTUBE.txt`.
2. A "next video" card can only point to a video that is public. So when the next episode goes live, the autopilot
   adds a second to-do for the previous episode: point its "next video" card and one end-screen slot at the new one.
3. Claude does the to-dos in YouTube Studio with Claude in Chrome on the Mac, after each upload and when an episode
   goes live, and marks each one `"status": "done"` (with the date) in the queue.

**What Mykhailo needs**
- Chrome on the Mac with the Claude extension connected.
- In that Chrome profile, be signed in to the Google account that owns the HoneyBadger & Bees channel
  (bentsamisha@gmail.com), and pick that channel in YouTube Studio.
- Claude never changes anything else in Studio (title, visibility, monetisation, comments).

**Steps for Claude in Studio** (per to-do)
1. Open `studio_url` (Studio > Content > the video > Details).
2. Cards: Editor (or the "Cards" box on the Details page) > + Video > search the target's title or paste its URL >
   set the card start at the given time > Save. Add a teaser text only if the to-do gives one.
   A link to honeybadgerandbees.com needs the YouTube Partner Program; if Studio doesn't offer "Link", skip it and
   note "needs YPP".
3. End screen: Editor > End screen > use the template with 2 videos + subscribe, or add elements one by one;
   start at `end_screen.from` (the last 12 s of the video are designed for it, the two boxes sit over the empty
   slots on the end card). "Best for viewer" is fine when a named video doesn't exist yet. Save.
4. Check the preview once, then set the to-do's status to "done" and write a one-line `result`.
