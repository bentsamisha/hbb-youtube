# Stream Deck: the HoneyBadger and Bees page

Page 4 of the Default Profile on the 15-key Stream Deck (page 1 general, page 2 prompter controls, page 3 Trust No Pack).
Every key shows live status and refreshes every 30 seconds. Gold means "look at this", red means "something is wrong"
or "press again to confirm".

| Key | Shows | Press |
|---|---|---|
| Review | episodes waiting in 2 Review (gold when new) | opens 2 Review |
| Pipeline | IDLE, or what the autopilot is doing (editing, re-edit, 4K render, Shorts); red after a failure | opens the log |
| Runway | weeks of episodes already scheduled, and until when; red at 0 | opens 4 Scheduled |
| Next | next episode going live and when | opens it in YouTube Studio |
| Comments | new YouTube comments since the last press | opens Studio comments and marks them seen. It never replies |
| Pulse | subscribers, views of the newest live episode | opens channel analytics |
| HBB REC | session timer (red, counting) | starts the timer and opens Elgato Studio; press again to stop and log it |
| Prompter | the next script to record (lowest HBB number above the episodes already made) | opens Elgato Studio on that script |
| Watch | newest episode in 2 Review | plays it in QuickTime |
| LinkedIn | next LinkedIn launch post and when it goes (gold if it still needs your OK, red if late) | opens its LINKEDIN.txt |
| Previous Page | | back to Trust No Pack |
| Thumbnail | the review episode's thumbnail: ready, or still to make | opens the thumbnail (or its prompt) |
| Approve | the episode(s) ready to approve | press twice: moves them to 3 Approved, which approves the video, thumbnail, LinkedIn post and Shorts. Same as moving the folder yourself |
| Studio | cards and end screens still to add in YouTube Studio | opens the next video to link |
| Auto-OK | which episode the Friday 18:00 auto-approve will take (gold in the last 24 hours) | opens that episode |

## Prompter

Elgato Studio has no "open this script" command, so the Prompter key quits Studio, points its settings at the script and
starts it again. Only the open script changes; text size, spacing, margin, speed and highlight stay as you set them.
While a REC timer is running (HBB or Trust No Pack) it never restarts Studio, it only brings it forward.
To read a different script, open it in Studio as usual.

## Where things are

- Logic: `System/pipeline/deck.py` (`deck.py status`, `deck.py press <key>`). Changing what a key shows or does only
  needs an edit there.
- Plugin: `System/pipeline/streamdeck/com.honeybadgerbees.deck.sdPlugin`, installed as a copy in
  `~/Library/Application Support/com.elgato.StreamDeck/Plugins/`. A new key or a changed name needs the manifest
  edited, the folder copied over and Stream Deck restarted.
- State: `System/logs/deck_state.json` (REC timer, approve arming, comments seen), `deck_cache.json` (YouTube numbers,
  10 minutes), `rec_sessions.json` (every REC session with its script).
