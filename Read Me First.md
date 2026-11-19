# HoneyBadger and Bees: what's where

| Folder | What it's for |
|---|---|
| **1 Inbox** | Drop the camera files here. One video, or a folder with all clips of one episode (`C0001.MP4`, `C0002.MP4` ...). Put the episode number in the name (`12`, `EP12`, `Video 12`), or the pipeline finds the script by itself. |
| **2 Review** | Finished episodes waiting for you, one folder each (e.g. `EP.12 The Fine-Tuning Trap`). Watch it, then drag the folder to **3 Approved**, or write `CHANGES.txt` inside it. |
| **3 Approved** | Uploaded to YouTube within minutes and scheduled for the next free slot (Mondays 09:00 Lisbon by default). |
| **4 Scheduled** | Uploaded, waiting for its publish time. YouTube publishes it itself, the Mac can be off. |
| **5 Posted** | Live. `Additional info/job.json` has the link. |
| **Raw Footage** | Your original camera files, untouched. |
| **Scripts** | `Video_N_Script.md`, exported from the Drive "Audit Videos" folder every hour. |
| **Strategy** | Master Content System and current state. Read by the editor, never uploaded anywhere, not in git. |
| **Prompter** | Clean prompter versions of the scripts (Elgato Camera Hub and the browser prompter). |
| **Guides** | `Setup Steps.md` (one-time), `Autopilot Guide.md` (how it works), `Recording Guide.md` (how to film for it). |
| **System** | The machinery: pipeline code, work files, logins (`secrets`), logs. No need to open it. |

## Inside an episode folder
- `EP.NN Name.mp4`: the finished video (4K, captions, on-screen text, name card, end screen)
- `cover.jpg`: the thumbnail (cream panel, serif text, one honey-gold word, your face, logo)
- `YOUTUBE.txt`: 3 ranked titles, the full description with exact timestamps, tags, pinned comment, card times, end screen
- `B-ROLL.txt` + `B-roll/`: the B-roll shots from the script with their prompts; drop `broll_01.mp4` ... into `B-roll/`
- `Additional info/`: subtitles, `post.json` (master text; edit it to change titles or description), `edit.json`, Claude's notes
