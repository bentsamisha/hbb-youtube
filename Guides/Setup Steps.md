# One-time setup (about 30 minutes)

Same idea as Trust No Pack. Every login file goes into `HBB/System/secrets/` (never share that folder; it's not in git).

## 1. Python environment
```bash
cd ~/HBB/System/pipeline && ./setup.sh
```
Needs `ffmpeg` with libass (`brew install ffmpeg`) - TNP's ffmpeg works.

## 2. Claude login for the background editor
If TNP already runs, reuse its token:
```bash
cp ~/TNP/System/secrets/claude_token.txt ~/HBB/System/secrets/
```
Otherwise: `~/.local/bin/claude setup-token`, copy the token, then `pbpaste > ~/HBB/System/secrets/claude_token.txt`.

## 3. Google login (YouTube upload + reading the scripts)
1. Reuse the Google Cloud OAuth client from TNP, or make a new one: console.cloud.google.com -> project
   "HoneyBadger and Bees" -> enable **YouTube Data API v3** and **Google Drive API** -> OAuth consent screen
   (External, add bentsamisha@gmail.com as test user, then **Publish app**) -> Credentials -> OAuth client ID ->
   Desktop app -> Download JSON.
2. Save it as `~/HBB/System/secrets/google_client.json`
   (or `cp ~/TNP/System/secrets/youtube_client.json ~/HBB/System/secrets/google_client.json`).
3. Connect:
   ```bash
   ~/HBB/System/pipeline/.venv/bin/python ~/HBB/System/pipeline/hbb.py auth youtube   # pick the HoneyBadger and Bees channel
   ~/HBB/System/pipeline/.venv/bin/python ~/HBB/System/pipeline/hbb.py auth drive     # read-only, for the scripts
   ~/HBB/System/pipeline/.venv/bin/python ~/HBB/System/pipeline/hbb.py scripts        # first export of all scripts
   ```
**Important:** a new, unaudited Google Cloud project gets uploads locked to private. If you use a new project, submit the
free "YouTube API Services - Audit and Quota Extension Form". The TNP project is already past that, so reusing it is quicker.

## 4. Start the autopilot
```bash
~/HBB/System/pipeline/.venv/bin/python ~/HBB/System/pipeline/hbb.py install
```
It runs every 5 minutes and whenever something lands in 1 Inbox, 2 Review or 3 Approved.
If nothing happens after dropping a video, check `HBB/System/logs/agent.err.log`. "Operation not permitted" means:
System Settings -> Privacy & Security -> Full Disk Access -> add the Python in `HBB/System/pipeline/.venv/bin/`.
Stop it with `hbb.py uninstall`.
