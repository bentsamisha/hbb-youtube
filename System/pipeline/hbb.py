"""HoneyBadger and Bees autopilot: raw footage -> Claude edit -> render -> your review -> scheduled YouTube upload.

  hbb.py tick               one pass over every folder (launchd runs this every 5 min and when a folder changes)
  hbb.py status             what's where, and the next free posting slot
  hbb.py auth youtube|drive connect the HoneyBadger and Bees channel / read-only Drive access for the scripts
  hbb.py scripts            export the Video_N_Script docs from Google Drive into HBB/Scripts
  hbb.py run <video|folder> [--episode N]   process one recording now, in the foreground
  hbb.py rerender <folder>  render an episode in "2 Review" again (same edit, e.g. after adding B-roll)
  hbb.py retry <folder>     clear an upload error so the next tick tries again
  hbb.py install|uninstall  start/stop the background agent

Folders (in HBB/):
  1 Inbox       drop the camera files here: one video, or a folder with all clips of one episode (C0001.MP4, C0002.MP4...).
                Put the episode number in the name ("12", "EP12", "Video 12") or let the pipeline find the script itself.
  2 Review      one folder per episode, e.g. "EP.12 The Fine-Tuning Trap": watch it, then
                - move the folder into "3 Approved" to schedule it, or
                - write what to change into CHANGES.txt inside the folder (Claude re-edits it), or
                - drop B-roll clips into its "B-roll" folder as broll_01.mp4, broll_02.mp4 ... (re-rendered automatically)
  3 Approved    uploaded to YouTube on the next tick, scheduled for the next free slot (Mondays 09:00 Lisbon by default)
  4 Scheduled   uploaded and waiting for its publish time (YouTube publishes it itself, the Mac can be off)
  5 Posted      live; "Additional info/job.json" has the link
"""
import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PIPE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, PIPE)
import editor  # noqa: E402
import publish as pub  # noqa: E402
import scripts  # noqa: E402
import shorts  # noqa: E402

ROOT = editor.ROOT
SYSTEM = editor.SYSTEM
DIRS = {"inbox": os.path.join(ROOT, "1 Inbox"), "review": os.path.join(ROOT, "2 Review"),
        "approved": os.path.join(ROOT, "3 Approved"), "scheduled": os.path.join(ROOT, "4 Scheduled"),
        "posted": os.path.join(ROOT, "5 Posted"), "raw": os.path.join(ROOT, "Raw Footage"),
        "scripts": scripts.SCRIPTS_DIR, "logs": os.path.join(SYSTEM, "logs"), "work": editor.WORK}
INFO = "Additional info"
AGENT = "com.honeybadgerandbees.autopilot"
PLIST = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % AGENT)
read_json, write_json = editor.read_json, editor.write_json


def log(*a):
    line = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), " ".join(str(x) for x in a))
    print(line, flush=True)
    os.makedirs(DIRS["logs"], exist_ok=True)
    with open(os.path.join(DIRS["logs"], "hbb.log"), "a") as f:
        f.write(line + "\n")


def notify(title, msg):
    log("NOTIFY", title, "-", msg)
    if sys.platform == "darwin":
        script = 'display notification %s with title %s sound name "Glass"' % (json.dumps(msg), json.dumps("HBB: " + title))
        subprocess.run(["osascript", "-e", script], capture_output=True)


def cfg():
    return editor.load_config()


def acfg():
    return cfg().get("autopilot", {})


def info(folder, fn=""):
    return os.path.join(folder, INFO, fn)


def jobs_in(stage):
    out = []
    for d in sorted(glob.glob(os.path.join(DIRS[stage], "*", INFO, "job.json"))):
        out.append((os.path.dirname(os.path.dirname(d)), read_json(d, {})))
    return out


def main_video(folder):
    vids = sorted(glob.glob(os.path.join(folder, "*.mp4")))
    return vids[0] if vids else None


def find_folder(job_name):
    for stage in ("review", "approved", "scheduled", "posted"):
        for folder, job in jobs_in(stage):
            if job.get("name") == job_name:
                return folder
    return None


def stable(path, age=60):
    """Camera files copy slowly from the SD card: wait until nothing in it changed for a minute."""
    paths = [path] if os.path.isfile(path) else [p for p in glob.glob(os.path.join(path, "**", "*"), recursive=True)] + [path]
    newest = max(os.stat(p).st_mtime for p in paths)
    size = sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
    # a camera clock set ahead stamps files in the future: those count as old, the size check below still catches a copy in progress
    if 0 <= time.time() - newest < age:
        return False
    time.sleep(3)
    return size == sum(os.path.getsize(p) for p in paths if os.path.isfile(p))


# ---------------------------------------------------------------- Claude editor

def claude_bin():
    c = acfg().get("claude_bin")
    for p in [c, shutil.which("claude"), os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.claude/local/claude")]:
        if p and os.path.exists(p):
            return p
    bundled = sorted(glob.glob(os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude")))
    if bundled:
        return bundled[-1]
    return None


def run_editor(job, notes=None, error=None, timeout=60 * 60, turns=150):
    jd = os.path.join(DIRS["work"], job)
    msg = ["Read System/pipeline/EDITOR.md and System/pipeline/EDITOR_NOTES.md and follow them.",
           "Job folder: System/work/%s/" % job]
    msg.append("This is a CHANGE REQUEST. Mykhailo's change notes:\n" + notes if notes else "This is a first edit of a new recording.")
    if error:
        msg.append("Your previous edit failed validation, fix it: " + error)
    binary = claude_bin()
    if not binary:
        raise RuntimeError("Claude Code CLI not found (Guides/Setup Steps.md, step 2)")
    env = dict(os.environ)
    token = os.path.join(pub.SECRETS, "claude_token.txt")
    if os.path.exists(token):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = open(token).read().strip()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    cmd = [binary, "-p", "\n".join(msg), "--permission-mode", "acceptEdits", "--max-turns", str(turns),
           "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep", "Bash(ffmpeg:*)", "Bash(ffprobe:*)", "Bash(mkdir:*)",
           "Bash(cp:*)", "Bash(System/pipeline/stills:*)"]
    if acfg().get("claude_model"):
        cmd += ["--model", acfg()["claude_model"]]
    log("editor:", job, "(change request)" if notes else "")
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    with open(os.path.join(jd, "editor_log.txt"), "a") as f:
        f.write("\n==== %s\n%s\n%s\n" % (datetime.now(), r.stdout, r.stderr))
    if r.returncode != 0 or "Not logged in" in r.stdout:
        raise RuntimeError("Claude editor failed: %s" % (r.stdout + r.stderr).strip()[-400:])
    return r.stdout.strip()


def validate(job):
    jd = os.path.join(DIRS["work"], job)
    edit = read_json(os.path.join(jd, "edit.json"))
    post = read_json(os.path.join(jd, "post.json"))
    if not edit or not edit.get("keep"):
        return "edit.json missing, not valid JSON, or without keep ranges"
    if not post or not post.get("title"):
        return "post.json missing or has no title"
    last = -1.0
    for r in edit["keep"]:
        if len(r) < 2 or r[1] <= r[0] or r[0] < last - 0.01:
            return "keep ranges must be [start, end] in time order without overlaps (problem at %s)" % r
        last = r[1]
    dur = editor.Timeline(edit["keep"]).duration
    if not 30 <= dur <= 30 * 60:
        return "talk length %.0f s looks wrong" % dur
    text = json.dumps(post, ensure_ascii=False)
    if "—" in text:
        return "post.json contains an em dash; use standard hyphens"
    if "\u2014" in json.dumps(edit.get("graphics", []), ensure_ascii=False):
        return "a graphic contains an em dash; use standard hyphens"
    for g in edit.get("graphics", []):
        if g.get("type") not in KNOWN_GRAPHICS:
            return "unknown graphic type %r (see EDITOR.md)" % g.get("type")
        if g.get("at") in (None, ""):
            return "a %s graphic has no 'at'" % g.get("type")
    return None


KNOWN_GRAPHICS = ("title", "name", "lower", "related", "next", "warning", "statement", "chapter", "linkedin", "brand",
                  "subscribe", "list", "card", "quote", "compare", "cards")


def fallback_post(job):
    """No Claude available: a plain post.json from the script so the video still reaches Review."""
    jd = os.path.join(DIRS["work"], job)
    sc = read_json(os.path.join(jd, "script.json"), {}) or {}
    draft = read_json(os.path.join(jd, "draft.json"))
    title = re.sub(r"^Video\s*\d+\s*-\s*", "", sc.get("title", job)).strip()
    chapters = [{"t": draft["keep"][0][0], "title": "Intro"}]
    for r in draft["rows"]:
        m = re.match(r"(Group (?:one|two|three|number one|\d))[.,]?\s*(.*?)[.]", r["text"], re.I)
        if m and r.get("start") is not None:
            chapters.append({"t": r["start"], "title": m.group(2)[:50].strip().capitalize() or m.group(1).title()})
    write_json(os.path.join(jd, "post.json"), {
        "episode_name": title[:40], "title": title, "titles": [title], "hook": "", "context": "", "credibility": "",
        "chapters": chapters, "audience": [], "question": "", "hashtags": ["#ISO27001"], "tags": [],
        "thumbnail": {"t": draft["keep"][0][0] + 3, "text": title.split()[:3]},
        "notes": "Written without the Claude editor (not available): please write the description yourself."})
    shutil.copy(os.path.join(jd, "draft.json"), os.path.join(jd, "edit.json"))


def edit_and_render(job, notes=None):
    jd = os.path.join(DIRS["work"], job)
    summary, err = "", None
    if claude_bin():
        for attempt in range(2):
            summary = run_editor(job, notes, err)
            err = validate(job)
            if not err:
                break
            log("validation:", err)
        if err:
            raise RuntimeError(err)
    else:
        if not os.path.exists(os.path.join(jd, "post.json")):
            fallback_post(job)
        summary = "Automatic edit only (Claude editor not set up): check the cuts and write the description."
    editor.render(job, log=log)
    return summary


# ---------------------------------------------------------------- the episode folder

def youtube_text(post, edit, cfg_):
    """YouTube title/description/tags from post.json, in the house style; timestamps are exact (from the edit)."""
    b = cfg_["brand"]
    tl = editor.Timeline(edit["keep"])
    fix = editor.no_em_dash
    parts = [fix(post.get(k, "")).strip() for k in ("hook", "context", "credibility")]
    desc = [p for p in parts if p]
    chap = editor.chapters_text(post, tl)
    if chap:
        desc.append("⏱️ TIMESTAMPS\n" + "\n".join(chap))
    if post.get("audience"):
        desc.append("\U0001f3af WHO THIS VIDEO IS FOR\n" + "\n".join("✅ " + fix(a) for a in post["audience"]))
    desc.append("\U0001f517 GET IN TOUCH\n\U0001f310 Free Estimate: %s\n\U0001f4bc LinkedIn DM: %s" % (b["website"], b["linkedin"]))
    if post.get("question"):
        desc.append("\U0001f4e9 " + fix(post["question"]))
    desc.append("\U0001f41d HoneyBadger & Bees Technologies\n%s." % fix(b["signoff"]).rstrip("."))
    tags = post.get("hashtags", [])[:5]
    if tags:
        desc.append(" ".join(tags))
    yt_tags = list(post.get("tags", []))
    while len(",".join(yt_tags)) > 480:
        yt_tags.pop()
    return {"title": fix(post["title"])[:100], "description": "\n\n".join(desc), "tags": yt_tags, "chapters": chap}


def write_upload_kit(folder):
    post = read_json(info(folder, "post.json"))
    job = read_json(info(folder, "job.json"), {})
    edit = read_json(info(folder, "edit.json"))
    if not post or not edit:
        return
    c = cfg()
    yt = youtube_text(post, edit, c)
    tl = editor.Timeline(edit["keep"])
    rend = read_json(info(folder, "render.json"), {}) or {}
    if job.get("slot"):
        when = datetime.fromisoformat(job["slot"]).strftime("%A %d %B %Y, %H:%M") + " (Lisbon)"
    else:
        when = 'set automatically when you move this folder to "3 Approved" (next free %s)' % ", ".join(
            "%s %s" % (d.capitalize(), " / ".join(t)) for d, t in acfg().get("post_times", {}).items())
    line = "-" * 64
    alts = "\n".join(post.get("titles", [])) or yt["title"]
    cards = "\n".join("- %s  %s" % (editor.mmss(tl.out(x["t"])), x["what"]) for x in edit.get("cards", [])) or "- none in the script"
    ends = "\n".join("- " + x for x in post.get("end_screen", [])) or "- your latest episode + the one this video builds on"
    yt_status = (job.get("youtube") or {})
    text = """YOUTUBE VIDEO  ({ep})
Posting time: {when}
The autopilot uploads this for you when you approve it. Use this file only to check the text, or to upload by hand.
To change the text: edit "Additional info/post.json" (this file is rebuilt from it), or write it in CHANGES.txt.
{status}
{line}
TITLE (recommended, then the alternatives):
{title}

{alts}

{line}
DESCRIPTION (copy):
{desc}

{line}
TAGS (Show more > Tags, copy):
{tags}

{line}
PINNED COMMENT (post it after the video is live, then pin it):
{pinned}

{line}
CARDS (YouTube Studio > Editor > Cards, add at these times):
{cards}
- Add a card to the Free Estimate page at the highest-intent moment (usually the LinkedIn DM line).

{line}
END SCREEN (Studio > Editor > End screen: the last {end_s} s are made for it, from {end_at}):
{ends}
- Subscribe button

{line}
SETTINGS (set automatically on upload)
- File: {video} ({size}, {dur})
- Thumbnail: {thumb}
- Subtitles: Additional info/subtitles.srt (English)
- Audience: No, it's not made for kids
- Altered or synthetic content: {synthetic}
- Category: Education
- Visibility: Schedule > {when}

LinkedIn hook idea for publish day: {li}
""".format(ep=job.get("episode", ""), when=when, line=line, title=yt["title"], alts=alts, desc=yt["description"],
           tags=", ".join(yt["tags"]), pinned=editor.no_em_dash(post.get("pinned_comment", "")), cards=cards, ends=ends,
           end_s=c["end_screen_seconds"], end_at=editor.mmss(tl.duration), video=os.path.basename(main_video(folder) or "video.mp4"),
           size="x".join(str(x) for x in rend.get("size", [])) or "4K", dur=editor.mmss(rend.get("duration", tl.duration)),
           synthetic="Yes (AI B-roll is used)" if rend.get("broll_used") else "No",
           li=editor.no_em_dash(post.get("linkedin_hook", "")) or "-", thumb=os.path.basename(thumb_file(folder)),
           status=("Uploaded: %s (publishes %s)\n" % (yt_status.get("url"), when)) if yt_status.get("id") else "")
    with open(os.path.join(folder, "YOUTUBE.txt"), "w") as f:
        f.write(text)
    write_linkedin(folder, post, job)
    write_thumbnail_prompt(folder, post, job)
    # B-roll shot list
    brolls = edit.get("broll", [])
    used = set(rend.get("broll_used", []))
    lines = ["B-ROLL SHOTS  (%s)" % job.get("episode", ""), "",
             "Generate each clip (Veo / Gemini, or any stock clip), save it into the \"B-roll\" folder next to this file",
             "as broll_01.mp4, broll_02.mp4 ... (a .jpg/.png works too). The video is rendered again by itself within",
             "a few minutes and you get a notification. Clips are trimmed to the length below; 16:9, 1080p or 4K.", ""]
    for b_ in brolls:
        at = editor.mmss(tl.out(b_["t"])) if b_.get("t") is not None else "(row not in the footage)"
        lines += ["%s  broll_%02d   at %s for %d s" % ("DONE" if b_["n"] in used else "TODO", b_["n"], at, b_["dur"]),
                  "   prompt: " + b_.get("prompt", ""), ""]
    if not brolls:
        lines.append("No B-roll in this script.")
    with open(os.path.join(folder, "B-ROLL.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def episode_label(job, episode):
    post = read_json(os.path.join(DIRS["work"], job, "post.json"), {}) or {}
    title = post.get("episode_name") or job
    title = re.sub(r"[^\w\s'&+-]", "", editor.no_em_dash(title)).strip()
    return ("%s %s" % (episode, title)).strip()


def to_review(job, episode, summary=""):
    """Build (or refresh) the episode folder in "2 Review" from the work folder."""
    jd = os.path.join(DIRS["work"], job)
    label = episode_label(job, episode)
    folder = os.path.join(DIRS["review"], label)
    old = find_folder(job)
    if old and old != folder:
        shutil.move(old, folder)
    os.makedirs(info(folder), exist_ok=True)
    os.makedirs(os.path.join(folder, "B-roll"), exist_ok=True)
    for f in glob.glob(os.path.join(folder, "*.mp4")):
        os.remove(f)
    shutil.move(os.path.join(jd, "final.mp4"), os.path.join(folder, label + ".mp4"))
    shutil.copy(os.path.join(jd, "thumbnail.jpg"), os.path.join(folder, "cover.jpg"))
    shutil.copy(os.path.join(jd, "captions.srt"), info(folder, "subtitles.srt"))
    for fn in ("post.json", "render.json"):
        shutil.copy(os.path.join(jd, fn), info(folder, fn))
    edit = read_json(os.path.join(jd, "edit.json")) or read_json(os.path.join(jd, "draft.json"))
    write_json(info(folder, "edit.json"), {k: v for k, v in edit.items() if k not in ("rows",)})
    job_rec = read_json(info(folder, "job.json"), {}) or {"name": job, "episode": episode, "created": time.time()}
    job_rec.update(stage="review", label=label, summary=summary, rendered=time.time(),
                   broll_seen=sorted(os.path.basename(p) for p in glob.glob(os.path.join(folder, "B-roll", "*"))))
    write_json(info(folder, "job.json"), job_rec)
    if summary:
        with open(info(folder, "Claude notes.txt"), "w") as f:
            f.write(summary + "\n")
    write_upload_kit(folder)
    try:
        render_shorts(folder, job_rec)
    except Exception as e:
        log("shorts:", traceback.format_exc())
        notify("Shorts failed", "%s: %s" % (episode, str(e)[:120]))
    try:
        thumb_queue_add(folder, job_rec)
    except Exception as e:
        log("thumbnail queue:", e)
    return folder


# ---------------------------------------------------------------- stages

def process_inbox():
    for path in sorted(glob.glob(os.path.join(DIRS["inbox"], "*"))):
        base = os.path.basename(path)
        if base.startswith(".") or (os.path.isfile(path) and not path.lower().endswith(editor.VIDEO_EXT)):
            continue
        if not stable(path):
            continue
        if os.path.isdir(path) and not editor.gather(path):
            continue
        raw = os.path.join(DIRS["raw"], base)
        if os.path.exists(raw):
            raw = os.path.join(DIRS["raw"], "%s %s" % (time.strftime("%Y%m%d-%H%M"), base))
        shutil.move(path, raw)
        job = os.path.splitext(os.path.basename(raw))[0] if os.path.isfile(raw) else os.path.basename(raw)
        notify("New recording", "%s: transcribing and editing (about 20-40 minutes)" % base)
        try:
            jd = editor.analyze(raw, job=job, log=log)
            draft = read_json(os.path.join(jd, "draft.json"))
            episode = "EP.%02d" % draft["episode"] if draft.get("episode") else "EP.xx"
            if not draft.get("episode"):
                notify("No script found", "%s: editing without a script. Put the episode number in the name next time." % base)
            summary = edit_and_render(job)
            folder = to_review(job, episode, summary)
            notify("Ready for review", "%s is in \"2 Review\". Approve it or write CHANGES.txt" % os.path.basename(folder))
        except Exception as e:
            log(traceback.format_exc())
            notify("Edit failed", "%s: %s" % (base, str(e)[:150]))
            fail = os.path.join(DIRS["review"], "%s (FAILED)" % job)
            os.makedirs(info(fail), exist_ok=True)
            write_json(info(fail, "job.json"), {"name": job, "stage": "failed", "error": str(e)[:1500],
                                                "raw": raw, "created": time.time()})


def process_changes():
    for folder, job in jobs_in("review"):
        if job.get("stage") == "failed" or job.get("sample"):
            continue
        name = job["name"]
        jd = os.path.join(DIRS["work"], name)
        changes = os.path.join(folder, "CHANGES.txt")
        broll_now = sorted(os.path.basename(p) for p in glob.glob(os.path.join(folder, "B-roll", "*")) if not os.path.basename(p).startswith("."))
        # post.json edited by hand in the folder: take it over
        fp, wp = info(folder, "post.json"), os.path.join(jd, "post.json")
        if os.path.exists(fp) and os.path.exists(wp) and os.path.getmtime(fp) > os.path.getmtime(wp) + 2:
            shutil.copy(fp, wp)
            write_upload_kit(folder)
        try:
            if os.path.exists(changes) and stable(changes, age=20):
                notes = open(changes).read().strip()
                os.rename(changes, os.path.join(folder, "CHANGES done %s.txt" % time.strftime("%Y-%m-%d %H%M")))
                if not notes:
                    continue
                notify("Changes", "%s: re-editing" % job["episode"])
                shutil.copy(info(folder, "post.json"), wp)
                summary = edit_and_render(name, notes=notes)
                to_review(name, job["episode"], summary)
                notify("Updated", "%s is ready again in \"2 Review\"" % job["episode"])
            elif broll_now != job.get("broll_seen", []) and broll_now and all(stable(os.path.join(folder, "B-roll", b), age=20) for b in broll_now):
                edit = read_json(os.path.join(jd, "edit.json"))
                edit["broll_dirs"] = [os.path.join(folder, "B-roll")]
                write_json(os.path.join(jd, "edit.json"), edit)
                notify("B-roll", "%s: adding %d clip(s), rendering again" % (job["episode"], len(broll_now)))
                editor.render(name, log=log)
                to_review(name, job["episode"], job.get("summary", ""))
                notify("Updated", "%s now has its B-roll" % job["episode"])
        except Exception as e:
            log(traceback.format_exc())
            notify("Update failed", "%s: %s" % (job.get("episode"), str(e)[:150]))


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def taken_slots(exclude=None):
    out = set()
    for stage in ("approved", "scheduled", "posted"):
        for _, j in jobs_in(stage):
            if j.get("slot") and j.get("name") != exclude:
                out.add(datetime.fromisoformat(j["slot"]))
    return out


def next_slot(now=None, exclude=None):
    tz = ZoneInfo(acfg().get("timezone", "Europe/Lisbon"))
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    earliest = now + timedelta(minutes=acfg().get("min_lead_minutes", 90))
    if acfg().get("first_slot_date"):  # e.g. "2026-11-30": keep a buffer, nothing goes live before this day
        earliest = max(earliest, datetime.fromisoformat(acfg()["first_slot_date"]).replace(tzinfo=tz))
    taken = taken_slots(exclude)
    times = acfg().get("post_times") or {"mon": ["09:00"]}
    for day in range(0, 120):
        d = (now + timedelta(days=day)).date()
        for hm in sorted(times.get(DAYS[d.weekday()], [])):
            h, m = map(int, hm.split(":"))
            slot = datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
            if slot >= earliest and slot not in taken:
                return slot
    raise RuntimeError("no free posting slot: check autopilot.post_times in config.json")


def process_approved():
    for folder, job in jobs_in("approved"):
        if (job.get("youtube") or {}).get("id"):
            shutil.move(folder, os.path.join(DIRS["scheduled"], os.path.basename(folder)))
            notify("Already uploaded", "%s was uploaded before: moved back to 4 Scheduled" % job.get("episode"))
            continue
        if job.get("stage") == "failed" or job.get("hold"):
            continue
        if job.get("sample"):
            if not job.get("sample_note"):
                notify("Sample only", "%s is a test package and is never uploaded" % os.path.basename(folder))
                job["sample_note"] = time.time()
                write_json(info(folder, "job.json"), job)
            continue
        slot = datetime.fromisoformat(job["slot"]) if job.get("slot") else next_slot(exclude=job.get("name"))
        job.update(slot=slot.isoformat(), approved=job.get("approved") or time.time(), stage="approved")
        if job.get("shorts_rendered") and not job.get("shorts_ok"):
            job["shorts_ok"] = time.time()  # approving the folder approves the Shorts in it
        write_json(info(folder, "job.json"), job)
        if not acfg().get("youtube", True):
            job["stage"] = "scheduled"
            write_json(info(folder, "job.json"), job)
            write_upload_kit(folder)
            shutil.move(folder, os.path.join(DIRS["scheduled"], os.path.basename(folder)))
            notify("Scheduled", "%s: upload it by hand for %s (YOUTUBE.txt)" % (job["episode"], slot.strftime("%a %d %b %H:%M")))
            continue
        if cfg()["output"].get("final_4k", True) and not job.get("final_4k"):
            # review rounds are 1080p (fast); the approved version is rendered once in 4K for YouTube
            try:
                notify("Rendering 4K", "%s: final 4K render before the upload" % job["episode"])
                t4 = time.time()
                out = editor.render(job["name"], final4k=True, log=log)
                shutil.move(out, main_video(folder))
                job["final_4k"] = time.time()
                log("4K render done in %d min" % ((time.time() - t4) / 60))
            except Exception as e:
                log("4K render failed, uploading the 1080p version:", e)
                notify("4K failed", "%s: uploading the 1080p version instead" % job["episode"])
                job["final_4k"] = "failed"
            write_json(info(folder, "job.json"), job)
        try:
            post = read_json(info(folder, "post.json"))
            edit = read_json(info(folder, "edit.json"))
            yt = youtube_text(post, edit, cfg())
            yt["synthetic"] = bool((read_json(info(folder, "render.json"), {}) or {}).get("broll_used"))
            res = pub.youtube_upload(main_video(folder), yt, slot.astimezone(timezone.utc),
                                     thumbnail=thumb_file(folder),
                                     captions=info(folder, "subtitles.srt") if acfg().get("upload_captions", True) else None,
                                     category=acfg().get("category_id", "27"), playlist_id=acfg().get("playlist_id", ""))
            job.update(youtube=res, stage="scheduled", uploaded=time.time(), error=None)
            write_json(info(folder, "job.json"), job)
            write_upload_kit(folder)
            try:
                studio_after_upload(folder, job)
            except Exception as e:
                log("studio queue:", e)
            try:
                linkedin_after_upload(folder, job, slot)
            except Exception as e:
                log("linkedin queue:", e)
            dest = os.path.join(DIRS["scheduled"], os.path.basename(folder))
            shutil.move(folder, dest)
            log("uploaded", job["episode"], res)
            notify("Scheduled", "%s uploaded, goes live %s" % (job["episode"], slot.strftime("%a %d %b %H:%M")))
            if res.get("warning"):
                notify("Check YouTube Studio", res["warning"])
        except pub.NeedsAuth as e:
            if job.get("error") != str(e):
                notify("Login needed", str(e))
            job["error"] = str(e)
            write_json(info(folder, "job.json"), job)
        except Exception as e:
            log(traceback.format_exc())
            job["attempts"] = job.get("attempts", 0) + 1
            job["error"] = str(e)[:500]
            write_json(info(folder, "job.json"), job)
            if any(r in str(e) for r in ("uploadLimitExceeded", "quotaExceeded")):
                notify("YouTube daily limit", "%s uploads automatically later" % job["episode"])
            elif job["attempts"] in (1, 5):
                notify("Upload failed", "%s: %s" % (job["episode"], str(e)[:120]))


# ---------------------------------------------------------------- LinkedIn launch post
# Posted from his own LinkedIn about 10 minutes after the video goes live, YouTube link in the first comment.
# Claude posts it through Claude in Chrome only after Mykhailo says yes to that week's post in the chat
# (see Guides/LinkedIn Posting.md); the queue is System/linkedin_queue.json.

def first_comment(url):
    b = cfg()["brand"]
    return "\U0001f3ac Watch the full breakdown here:\n%s\n\U0001f41d More about HoneyBadger & Bees:\n%s" % (
        url or "[YouTube link]", b.get("website", "https://www.honeybadgerandbees.com/"))


def write_linkedin(folder, post, job):
    posts = [editor.no_em_dash(p) for p in post.get("linkedin_posts", []) if p]
    kept = linkedin_text(folder)  # his own edit of the POST section survives re-renders and the upload
    if kept and not kept.startswith("(no post yet"):
        posts = [kept] + [p for p in posts if p.strip() != kept]
    url = (job.get("youtube") or {}).get("url")
    line = "-" * 64
    out = ["LINKEDIN LAUNCH POST  (%s)" % job.get("episode", ""),
           "Goes out from your profile about 10 minutes after the video is live, once you've said yes in the chat.",
           "The post under POST is what gets published: edit it here if you want different wording.", line, "POST:", "",
           posts[0] if posts else "(no post yet: Claude writes one before publish day)", "", line,
           "IMAGE: the episode thumbnail (%s) is attached to the post." % os.path.basename(thumb_file(folder)), line,
           "FIRST COMMENT (added right after posting, then pinned):", "", first_comment(url)]
    for i, p in enumerate(posts[1:], 2):
        out += ["", line, "ALTERNATIVE %d (copy it over the POST above to use it):" % i, "", p]
    with open(os.path.join(folder, "LINKEDIN.txt"), "w") as f:
        f.write("\n".join(out) + "\n")


def linkedin_text(folder):
    """The POST section of LINKEDIN.txt, as he may have edited it."""
    try:
        s = open(os.path.join(folder, "LINKEDIN.txt")).read()
    except OSError:
        return ""
    m = re.search(r"\nPOST:\n(.*?)\n-{20,}", s, re.S)
    return m.group(1).strip() if m else ""


def linkedin_queue_path():
    return os.path.join(ROOT, "System", "linkedin_queue.json")


def linkedin_after_upload(folder, job, slot):
    q = read_json(linkedin_queue_path(), []) or []
    yt = job.get("youtube") or {}
    if any(x.get("video_id") == yt.get("id") for x in q):
        return
    q.append({"episode": job["episode"], "video_id": yt.get("id"), "video_url": yt.get("url"),
              "post_at": (slot + timedelta(minutes=acfg().get("linkedin_delay_minutes", 10))).isoformat(),
              "text": linkedin_text(folder), "first_comment": first_comment(yt.get("url")), "pin_comment": True,
              "image": linkedin_image(folder, job),
              "image_url": "https://i.ytimg.com/vi/%s/maxresdefault.jpg" % yt.get("id") if yt.get("id") else "",
              # moving the episode to "3 Approved" approves its LINKEDIN.txt too (autopilot.linkedin_approve_with_episode)
              "status": "approved" if acfg().get("linkedin_approve_with_episode", True) else "needs_ok",
              "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if acfg().get("linkedin_approve_with_episode", True) else None,
              "added": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    write_json(linkedin_queue_path(), q)


def linkedin_image(folder, job):
    """Copy the thumbnail to System/linkedin/ so it outlives the episode folder moving between stages."""
    src = thumb_file(folder)
    if not os.path.exists(src):
        return ""
    d = os.path.join(ROOT, "System", "linkedin")
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, re.sub(r"[^A-Za-z0-9._-]+", "_", job.get("episode", "episode")) + os.path.splitext(src)[1].lower())
    shutil.copy(src, dst)
    return os.path.relpath(dst, ROOT)


# ---------------------------------------------------------------- Thumbnail (made in his ChatGPT image chat)
# The pipeline writes THUMBNAIL_PROMPT.txt and queues the episode in System/thumbnail_queue.json. A Claude session with
# Claude in Chrome sends the prompt in his thumbnail chat (it already has his photo), downloads the image and saves it
# as THUMBNAIL.jpg in the Review folder (see Guides/Thumbnails.md). THUMBNAIL.jpg/.png, when present, is the YouTube
# thumbnail and the LinkedIn image; cover.jpg (a frame with text) is only the fallback.

def thumb_file(folder):
    for fn in ("THUMBNAIL.jpg", "THUMBNAIL.jpeg", "THUMBNAIL.png"):
        if os.path.exists(os.path.join(folder, fn)):
            return os.path.join(folder, fn)
    return os.path.join(folder, "cover.jpg")


def thumbnail_prompt(post, job, edit=None):
    """His own way of asking for a thumbnail in that chat: the request line, then the title and the YouTube description.
    The chat already holds his photos and earlier thumbnails, so it keeps the face and the style."""
    t = cfg().get("thumbnail", {})
    yt = youtube_text(post, edit, cfg()) if edit else {"title": post.get("title", ""), "description": ""}
    title = re.sub(r"^[^\w\[(]+", "", yt["title"]).strip()
    out = [t.get("ask", "Make a thumbnail for this video description"), title, "", yt["description"].strip()]
    b = post.get("thumbnail_brief") or {}
    if b and t.get("add_brief", True):
        bits = [b.get(k) for k in ("headline", "big", "sub") if b.get(k)]
        out += ["", "Thumbnail text idea (optional): " + " / ".join(bits)
                + ("; checklist: " + "; ".join(b["checklist"]) if b.get("checklist") else "")
                + ("; badge: " + b["badge"] if b.get("badge") else "")]
    return editor.no_em_dash("\n".join(out)).strip()


def write_thumbnail_prompt(folder, post, job):
    with open(os.path.join(folder, "THUMBNAIL_PROMPT.txt"), "w") as f:
        f.write("THUMBNAIL PROMPT  (%s)\n" % job.get("episode", "")
                + "Claude sends this in your ChatGPT thumbnail chat and saves the result here as THUMBNAIL.jpg.\n"
                + "Want a different one? Edit the prompt below, delete THUMBNAIL.jpg, or write it in CHANGES.txt.\n"
                + "-" * 64 + "\n" + thumbnail_prompt(post, job, read_json(info(folder, "edit.json"))) + "\n")


def thumb_queue_path():
    return os.path.join(ROOT, "System", "thumbnail_queue.json")


def thumb_queue_add(folder, job):
    if not cfg().get("thumbnail", {}).get("on", True) or os.path.basename(thumb_file(folder)) != "cover.jpg":
        return
    q = read_json(thumb_queue_path(), []) or []
    ep = job.get("episode", "")
    if any(x.get("episode") == ep and x.get("status") == "todo" for x in q):
        return
    q.append({"episode": ep, "folder": os.path.relpath(folder, ROOT), "status": "todo",
              "added": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    write_json(thumb_queue_path(), q)


# ---------------------------------------------------------------- YouTube Studio links (done in the browser)
# The YouTube API can't add cards or end-screen elements, so every upload queues them in System/studio_queue.json.
# A Claude session with Claude in Chrome works through that queue in YouTube Studio (see Guides/Studio Links.md)
# and marks each item done. Nothing here touches the browser.

def studio_queue_path():
    return os.path.join(ROOT, "System", "studio_queue.json")


def studio_queue():
    return read_json(studio_queue_path(), []) or []


def studio_add(item):
    q = studio_queue()
    if any(x.get("key") == item["key"] for x in q):
        return
    item.update(status="todo", added=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    q.append(item)
    write_json(studio_queue_path(), q)


def studio_after_upload(folder, job):
    """Cards at the script's link moments + the end screen, for the video just uploaded."""
    edit = read_json(info(folder, "edit.json")) or {}
    post = read_json(info(folder, "post.json")) or {}
    tl = editor.Timeline(edit["keep"])
    rend = read_json(info(folder, "render.json"), {}) or {}
    yt = job.get("youtube") or {}
    cards, later = [], []
    for x in edit.get("cards", []):
        c = {"at": editor.mmss(tl.out(x["t"])), "target": x["what"]}
        (later if re.search(r"next video|once it is published|coming soon", x["what"], re.I) else cards).append(c)
    end_at = editor.mmss(rend.get("body", tl.duration))
    studio_add({"key": "%s/links" % yt.get("id", job["name"]), "episode": job["episode"], "video_id": yt.get("id"),
                "studio_url": "https://studio.youtube.com/video/%s/edit" % yt.get("id", ""),
                "title": (job.get("youtube") or {}).get("title") or job.get("label", ""),
                "cards": [c for c in cards if not re.search(r"estimate|honeybadgerandbees", c["target"], re.I)],  # link cards need YPP
                "end_screen": {"from": end_at, "elements": post.get("end_screen", []) + ["Subscribe"]},
                "waiting_for_next_video": later})


def studio_after_live(folder, job):
    """This video is now public: the previous episode's 'next video' card and end screen can point to it."""
    yt = job.get("youtube") or {}
    q = studio_queue()
    prev = [x for x in q if x.get("waiting_for_next_video") and x.get("video_id") != yt.get("id")]
    for p in prev[-1:]:
        studio_add({"key": "%s/next/%s" % (p["video_id"], yt.get("id")), "episode": p["episode"], "video_id": p["video_id"],
                    "studio_url": p["studio_url"], "title": p.get("title", ""),
                    "cards": [{"at": c["at"], "target": "%s (%s)" % (job["episode"], yt.get("url", ""))}
                              for c in p["waiting_for_next_video"]],
                    "end_screen": {"from": p["end_screen"]["from"],
                                   "elements": ["%s (%s), replace the oldest video element" % (job["episode"], yt.get("url", ""))]}})
        q = studio_queue()
        for x in q:
            if x["key"] == p["key"]:
                x["waiting_for_next_video"] = []
        write_json(studio_queue_path(), q)


# ---------------------------------------------------------------- Shorts (2-3 vertical clips per episode)
# The editor picks them into edit.json "shorts" (EDITOR.md); shorts.py renders them into the episode folder's
# "Shorts" subfolder for review. Approving the episode approves them; after the main upload they are scheduled on
# the following days (autopilot.shorts.days_after at autopilot.shorts.time, Lisbon).

def shorts_cfg():
    return acfg().get("shorts") or {}


def work_edit(job):
    return read_json(os.path.join(DIRS["work"], job["name"], "edit.json")) or {}


def render_shorts(folder, job):
    if not shorts_cfg().get("on", True):
        return False
    edit = work_edit(job)
    if not edit.get("shorts"):
        return False
    notify("Rendering Shorts", "%s: %d Shorts" % (job.get("episode"), len(edit["shorts"])))
    files = shorts.render_all(job["name"], log=log)
    sd = os.path.join(folder, "Shorts")
    os.makedirs(sd, exist_ok=True)
    for old in glob.glob(os.path.join(sd, "*.mp4")):
        os.remove(old)
    for f in files:
        shutil.move(f, os.path.join(sd, os.path.basename(f)))
    job.update(shorts_rendered=time.time(), shorts_n=len(files))
    if job.get("stage") in ("approved", "scheduled") and not job.get("shorts_yt"):
        job.pop("shorts_ok", None)  # rendered after approval: they wait for their own yes
    write_json(info(folder, "job.json"), job)
    write_shorts_txt(folder, job, edit)
    return True


def short_slots(job):
    sc = shorts_cfg()
    tz = ZoneInfo(acfg().get("timezone", "Europe/Lisbon"))
    main = datetime.fromisoformat(job["slot"]).astimezone(tz)
    h, m = map(int, sc.get("time", "12:00").split(":"))
    out = []
    for d in sc.get("days_after", [2, 4, 6]):
        day = (main + timedelta(days=d)).date()
        out.append(datetime(day.year, day.month, day.day, h, m, tzinfo=tz))
    return out


def short_text(s, job, edit):
    url = (job.get("youtube") or {}).get("url") or "[link to the full episode]"
    tags = s.get("hashtags") or ["#ISO42001", "#AIGovernance"]
    if "#Shorts" not in tags:
        tags = list(tags) + ["#Shorts"]
    desc = editor.no_em_dash(s.get("description", s.get("title", "")))
    desc += "\n\nFull episode: %s\n\n%s" % (url, " ".join(tags))
    post = read_json(info(find_folder(job["name"]) or "", "post.json"), {}) or {}
    return {"title": editor.no_em_dash(s.get("title", ""))[:95], "description": desc,
            "tags": (post.get("tags") or [])[:15]}


def write_shorts_txt(folder, job, edit):
    lines = ["SHORTS FOR %s (vertical, in the Shorts folder)" % job.get("episode"), ""]
    slots = short_slots(job) if job.get("slot") else []
    for i, s in enumerate(edit.get("shorts", [])):
        yt = short_text(s, job, edit)
        when = slots[i].strftime("%a %d %b %H:%M") if i < len(slots) else "after the episode is scheduled"
        done = (job.get("shorts_yt") or {}).get(str(s.get("n")))
        lines += ["-" * 64, "Short %s.mp4  |  goes live: %s%s" % (s.get("n"), when, ("  |  " + done["url"]) if done else ""),
                  "TITLE: " + yt["title"], "DESCRIPTION:", yt["description"], ""]
    lines += ["-" * 64, "Approving the episode approves these Shorts. To change one, write it in CHANGES.txt "
              "(e.g. \"Short 2: start at ...\")."]
    with open(os.path.join(folder, "SHORTS.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def process_shorts():
    for stage in ("review", "approved", "scheduled"):
        for folder, job in jobs_in(stage):
            if job.get("stage") == "failed" or job.get("sample"):
                continue
            ep = os.path.join(DIRS["work"], job["name"], "edit.json")
            if not os.path.exists(ep) or not work_edit(job).get("shorts"):
                continue
            if not job.get("shorts_rendered") or os.path.getmtime(ep) > job["shorts_rendered"] + 5:
                if (job.get("shorts_yt") or {}) or job.get("shorts_failed") == os.path.getmtime(ep):
                    continue  # already on YouTube (never re-render behind its back), or this edit already failed
                try:
                    render_shorts(folder, job)
                    notify("Shorts ready", "%s: %d Shorts in the Shorts folder" % (job["episode"], job.get("shorts_n", 0)))
                except Exception as e:
                    log("shorts:", traceback.format_exc())
                    notify("Shorts failed", "%s: %s" % (job["episode"], str(e)[:120]))
                    job["shorts_failed"] = os.path.getmtime(ep)
                    write_json(info(folder, "job.json"), job)
                continue
            if stage == "scheduled" and job.get("shorts_ok") and (job.get("youtube") or {}).get("id"):
                upload_shorts(folder, job)


def upload_shorts(folder, job):
    edit = work_edit(job)
    done = job.get("shorts_yt") or {}
    slots = short_slots(job)
    for i, s in enumerate(edit.get("shorts", [])):
        key = str(s.get("n", i + 1))
        f = os.path.join(folder, "Shorts", "Short %s.mp4" % key)
        if key in done or not os.path.exists(f) or i >= len(slots):
            continue
        try:
            res = pub.youtube_upload(f, short_text(s, job, edit), slots[i].astimezone(timezone.utc),
                                     category=acfg().get("category_id", "27"))
        except Exception as e:
            log("short upload:", traceback.format_exc())
            if not job.get("shorts_error"):
                notify("Short upload failed", "%s Short %s: %s" % (job["episode"], key, str(e)[:120]))
            job["shorts_error"] = str(e)[:300]
            write_json(info(folder, "job.json"), job)
            return
        done[key] = res
        job.update(shorts_yt=done, shorts_error=None)
        write_json(info(folder, "job.json"), job)
        log("uploaded short", job["episode"], key, res)
        studio_add({"key": "%s/related" % res["id"], "episode": job["episode"], "video_id": res["id"],
                    "studio_url": "https://studio.youtube.com/video/%s/edit" % res["id"],
                    "title": s.get("title", ""), "cards": [], "end_screen": None,
                    "related_video": (job.get("youtube") or {}).get("url"), "waiting_for_next_video": []})
        notify("Short scheduled", "%s Short %s goes live %s" % (job["episode"], key, slots[i].strftime("%a %d %b %H:%M")))
    write_shorts_txt(folder, job, edit)


def reschedule(folder, job):
    """job.json "move_to" (e.g. "2026-11-23 09:00", Lisbon time): move an uploaded episode to a new go-live time.
    Updates YouTube publishAt, the job slot, YOUTUBE.txt and the LinkedIn queue item."""
    tz = ZoneInfo(acfg().get("timezone", "Europe/Lisbon"))
    new = datetime.fromisoformat(str(job["move_to"]).replace(" ", "T"))
    new = new.replace(tzinfo=tz) if new.tzinfo is None else new.astimezone(tz)
    if new < datetime.now(timezone.utc) + timedelta(minutes=30):
        raise ValueError("move_to %s is in the past or too close" % new.isoformat())
    yt = job.get("youtube") or {}
    if yt.get("id"):
        pub.youtube_reschedule(yt["id"], new.astimezone(timezone.utc))
        yt["publish_at"] = new.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        job["youtube"] = yt
    old = job.get("slot")
    job.update(slot=new.isoformat(), rescheduled={"from": old, "to": new.isoformat(), "at": time.time()})
    job.pop("move_to", None)
    write_json(info(folder, "job.json"), job)
    q = read_json(linkedin_queue_path(), []) or []
    for x in q:
        if yt.get("id") and x.get("video_id") == yt.get("id") and x.get("status") != "posted":
            x["post_at"] = (new + timedelta(minutes=acfg().get("linkedin_delay_minutes", 10))).isoformat()
    write_json(linkedin_queue_path(), q)
    write_upload_kit(folder)
    log("rescheduled", job.get("episode"), old, "->", new.isoformat())
    notify("Rescheduled", "%s now goes live %s" % (job.get("episode"), new.strftime("%a %d %b %H:%M")))


def process_scheduled():
    now = datetime.now(timezone.utc)
    for folder, job in jobs_in("scheduled"):
        if not job.get("slot"):
            shutil.move(folder, os.path.join(DIRS["approved"], os.path.basename(folder)))
            continue
        if job.get("move_to"):
            try:
                reschedule(folder, job)
            except Exception as e:
                log("reschedule failed:", e)
                notify("Reschedule failed", "%s: %s" % (job.get("episode"), e))
                job["move_to_error"] = str(e)
                job.pop("move_to", None)
                write_json(info(folder, "job.json"), job)
            continue
        if now < datetime.fromisoformat(job["slot"]) + timedelta(minutes=10):
            continue
        yt = job.get("youtube") or {}
        if yt.get("id"):
            try:
                st = pub.youtube_status(yt["id"])
                if st.get("privacyStatus") != "public":
                    if not job.get("not_public_note"):
                        notify("Not public yet", "%s is still %s on YouTube: check Studio" % (job["episode"], st.get("privacyStatus")))
                        job["not_public_note"] = time.time()
                        write_json(info(folder, "job.json"), job)
                    continue
            except Exception as e:
                log("status check failed:", e)
                continue
        job.update(stage="posted", posted_at=time.time())
        write_json(info(folder, "job.json"), job)
        try:
            studio_after_live(folder, job)
        except Exception as e:
            log("studio queue:", e)
        shutil.move(folder, os.path.join(DIRS["posted"], os.path.basename(folder)))
        notify("Live", "%s is live: %s" % (job["episode"], yt.get("url", "")))


def cleanup_posted():
    """Some days after going live, delete the work caches (the raw footage stays in "Raw Footage")."""
    days = acfg().get("keep_raw_days_after_post", 14)
    for folder, job in jobs_in("posted"):
        if job.get("cleaned") or time.time() - job.get("posted_at", time.time()) < days * 86400:
            continue
        jd = os.path.join(DIRS["work"], job["name"])
        for junk in ("segments", "body.mov", "end_card.mov", "audio16k.wav"):
            p = os.path.join(jd, junk)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.remove(p)
        job["cleaned"] = time.time()
        write_json(info(folder, "job.json"), job)


def check_queue():
    """Once a day: warn when nothing is scheduled for the next posting slot (1 video a week)."""
    stamp = os.path.join(DIRS["logs"], "queue_warning.txt")
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(stamp) and open(stamp).read().strip() == today:
        return
    slot = next_slot()
    upcoming = [j for _, j in jobs_in("scheduled") + jobs_in("approved") if j.get("slot")]
    if len(upcoming) >= acfg().get("warn_when_queue_below", 1):
        return
    waiting = len(jobs_in("review"))
    msg = "Nothing scheduled for %s. " % slot.strftime("%a %d %b")
    msg += ("%d episode(s) waiting in 2 Review." % waiting) if waiting else "Time to record the next episode."
    notify("Queue empty", msg)
    with open(stamp, "w") as f:
        f.write(today)


def sync_scripts():
    """Once an hour: pull changed scripts from Drive (silently skipped until `hbb.py auth drive` was run)."""
    stamp = os.path.join(DIRS["logs"], "scripts_sync.txt")
    if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 3600:
        return
    with open(stamp, "w") as f:
        f.write(datetime.now().isoformat())
    if not os.path.exists(os.path.join(pub.SECRETS, "drive_token.json")):
        return
    scripts.sync(acfg().get("drive_scripts_folder"), log=log)


def collect_thumbnails():
    """Claude saves the ChatGPT thumbnail through Chrome as ~/Downloads/HBB_<episode>_THUMBNAIL.jpg; move it into
    that episode's folder (any stage before posting) as THUMBNAIL.jpg and mark the queue item done."""
    dl = os.path.expanduser("~/Downloads")
    for p in sorted(glob.glob(os.path.join(dl, "HBB_*_THUMBNAIL*.*"))):
        m = re.match(r"HBB_(.+?)_THUMBNAIL.*\.(jpe?g|png)$", os.path.basename(p), re.I)
        if not m or not stable(p, 10):
            continue
        ep = m.group(1)
        target = None
        for stage in ("review", "approved", "scheduled"):
            for folder, job in jobs_in(stage):
                if job.get("episode") == ep:
                    target = folder
        if not target:
            continue
        for old in glob.glob(os.path.join(target, "THUMBNAIL.*")):
            os.remove(old)
        ext = ".png" if m.group(2).lower() == "png" else ".jpg"
        shutil.move(p, os.path.join(target, "THUMBNAIL" + ext))
        q = read_json(thumb_queue_path(), []) or []
        for x in q:
            if x.get("episode") == ep and x.get("status") == "todo":
                x.update(status="done", done=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        write_json(thumb_queue_path(), q)
        try:
            write_upload_kit(target)
        except Exception as e:
            log("thumbnail kit:", e)
        log("thumbnail: %s -> %s" % (os.path.basename(p), os.path.basename(target)))
        notify("Thumbnail added", "%s: THUMBNAIL%s is in the folder" % (ep, ext))


def tick():
    for d in DIRS.values():
        os.makedirs(d, exist_ok=True)
    try:
        collect_thumbnails()  # quick file move, runs even while a long edit holds the lock
    except Exception:
        log(traceback.format_exc())
    lock = open(os.path.join(DIRS["logs"], "hbb.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # another tick is still working (a render takes a while)
    for step in (process_approved, process_scheduled, process_shorts, sync_scripts, process_changes, process_inbox, cleanup_posted, check_queue):
        try:
            step()
        except Exception:
            log(traceback.format_exc())


# ---------------------------------------------------------------- commands

def status():
    for stage in ("review", "approved", "scheduled", "posted"):
        for folder, job in jobs_in(stage):
            line = "%-10s %-48s" % (stage, os.path.basename(folder))
            if job.get("slot"):
                line += " slot %s" % datetime.fromisoformat(job["slot"]).strftime("%a %d %b %H:%M")
            if (job.get("youtube") or {}).get("url"):
                line += "  " + job["youtube"]["url"]
            if job.get("error"):
                line += "  ERROR: " + job["error"][:80]
            print(line)
    inbox = [p for p in glob.glob(os.path.join(DIRS["inbox"], "*")) if not os.path.basename(p).startswith(".")]
    print("inbox: %d item(s)" % len(inbox))
    print("next free slot:", next_slot().strftime("%a %d %b %Y %H:%M %Z"))


def run_now(path, episode=None):
    job = os.path.splitext(os.path.basename(path.rstrip("/")))[0] if os.path.isfile(path) else os.path.basename(path.rstrip("/"))
    jd = editor.analyze(path, job=job, episode=episode, log=log)
    draft = read_json(os.path.join(jd, "draft.json"))
    ep = "EP.%02d" % draft["episode"] if draft.get("episode") else "EP.xx"
    summary = edit_and_render(job)
    print(to_review(job, ep, summary))


def rerender(folder_name):
    folder = os.path.join(DIRS["review"], folder_name)
    job = read_json(info(folder, "job.json"))
    if not job:
        sys.exit("not found in 2 Review: " + folder_name)
    shutil.copy(info(folder, "post.json"), os.path.join(DIRS["work"], job["name"], "post.json"))
    edit_path = os.path.join(DIRS["work"], job["name"], "edit.json")
    edit = read_json(edit_path)
    edit["broll_dirs"] = [os.path.join(folder, "B-roll")]
    write_json(edit_path, edit)
    editor.render(job["name"], log=log)
    print(to_review(job["name"], job["episode"], job.get("summary", "")))


def retry(folder_name):
    for stage in ("approved", "scheduled", "review"):
        f = info(os.path.join(DIRS[stage], folder_name), "job.json")
        job = read_json(f)
        if job:
            job.update(error=None, attempts=0)
            write_json(f, job)
            print("reset", folder_name)
            return
    sys.exit("not found: " + folder_name)


def install():
    py = os.path.join(PIPE, ".venv", "bin", "python")
    watch = "".join("<string>%s</string>" % DIRS[k] for k in ("inbox", "review", "approved"))
    plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key><array><string>%s</string><string>%s</string><string>tick</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>WatchPaths</key><array>%s</array>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>%s</string>
  <key>StandardErrorPath</key><string>%s</string>
</dict></plist>
""" % (AGENT, py, os.path.realpath(__file__), watch,
       os.path.join(DIRS["logs"], "agent.out.log"), os.path.join(DIRS["logs"], "agent.err.log"))
    for d in DIRS.values():
        os.makedirs(d, exist_ok=True)
    with open(PLIST, "w") as f:
        f.write(plist)
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), AGENT)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", "gui/%d" % os.getuid(), PLIST], check=True)
    print("autopilot installed:", PLIST)


def uninstall():
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), AGENT)], capture_output=True)
    if os.path.exists(PLIST):
        os.remove(PLIST)
    print("autopilot removed")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["tick"]:
        tick()
    elif a[:1] == ["status"]:
        status()
    elif a[:1] == ["auth"] and len(a) == 2 and a[1] in ("youtube", "drive"):
        print(pub.auth(a[1]))
    elif a[:1] == ["scripts"]:
        print("updated:", scripts.sync(acfg().get("drive_scripts_folder"), log=log) or "nothing new")
    elif a[:1] == ["run"] and len(a) >= 2:
        run_now(a[1], int(a[a.index("--episode") + 1]) if "--episode" in a else None)
    elif a[:1] == ["rerender"] and len(a) == 2:
        rerender(a[1])
    elif a[:1] == ["retry"] and len(a) == 2:
        retry(a[1])
    elif a[:1] == ["install"]:
        install()
    elif a[:1] == ["uninstall"]:
        uninstall()
    else:
        print(__doc__)
