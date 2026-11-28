"""HoneyBadger and Bees - Stream Deck page 4 (plugin: System/pipeline/streamdeck/com.honeybadgerbees.deck.sdPlugin).

  deck.py status          JSON for every live key (the plugin polls it every 30 s)
  deck.py press <key>     what a key does when pressed (open folders, approve, rec timer, prompter...)

Reads the pipeline folders and queue files directly (fast, no rendering imports). YouTube numbers are cached for
10 minutes. Nothing here posts anything: the comments key only opens YouTube Studio, LinkedIn and Studio links stay
with the Claude sessions that work those queues, and approving is the same as moving the folders into "3 Approved".
"""
import fcntl, glob, json, os, re, shutil, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PIPE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.expanduser("~/HBB")
SYSTEM = os.path.join(ROOT, "System")
LOGS = os.path.join(SYSTEM, "logs")
STATE = os.path.join(LOGS, "deck_state.json")
CACHE = os.path.join(LOGS, "deck_cache.json")
TNP_STATE = os.path.expanduser("~/TNP/System/logs/deck_state.json")   # its REC timer also blocks a Studio restart
CHANNEL = "UCQ-CbiutpUh7X59G7Sj-gQw"
INFO = "Additional info"
STAGES = {"inbox": "1 Inbox", "review": "2 Review", "approved": "3 Approved", "scheduled": "4 Scheduled",
          "posted": "5 Posted"}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def rj(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def wj(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def stage_dir(stage):
    return os.path.join(ROOT, STAGES[stage])


def jobs(stage):
    out = []
    for p in sorted(glob.glob(os.path.join(stage_dir(stage), "*", INFO, "job.json"))):
        j = rj(p, {}) or {}
        j["_folder"] = os.path.dirname(os.path.dirname(p))
        out.append(j)
    return out


def slot_of(j):
    try:
        return datetime.fromisoformat(j["slot"])
    except Exception:
        return None


def ep_no(j):
    m = re.search(r"(\d+)", j.get("episode") or "")
    return int(m.group(1)) if m else 999


def main_video(folder):
    vids = sorted(glob.glob(os.path.join(folder, "*.mp4")))
    return vids[0] if vids else None


def acfg():
    return (rj(os.path.join(PIPE, "config.json"), {}) or {}).get("autopilot", {})


def tz():
    return ZoneInfo(acfg().get("timezone", "Europe/Lisbon"))


def log_tail(n=400):
    try:
        with open(os.path.join(LOGS, "hbb.log"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 80000))
            return f.read().decode(errors="ignore").splitlines()[-n:]
    except Exception:
        return []


def line_time(line):
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return 0


def busy():
    """True while an autopilot tick holds logs/hbb.lock (editing, rendering, uploading)."""
    try:
        fd = open(os.path.join(LOGS, "hbb.lock"), "a")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except (BlockingIOError, OSError):
        return True


def notices(tail):
    """NOTIFY lines as (time, title, message), oldest first."""
    out = []
    for l in tail:
        m = re.match(r"(\S+ \S+)\s+NOTIFY (.+?) - (.*)$", l)
        if m:
            out.append((line_time(l), m.group(2), m.group(3)))
    return out


def in_progress(tail):
    """Episodes the autopilot is re-editing or re-rendering in 2 Review right now (never move those)."""
    eps = {}
    for _, title, msg in notices(tail):
        ep = msg.split(":")[0].strip()
        if title in ("Changes", "B-roll"):
            eps[ep] = True
        elif title in ("Updated", "Update failed"):
            eps.pop(ep, None)
    return set(eps) if busy() else set()


def short_title(name, n=16):
    t = re.sub(r"^(EP\.\d+|HBB \d+)\s*", "", name)
    t = re.sub(r"\.md$", "", t)
    t = re.sub(r"^(The|A|An)\s+", "", t)
    if len(t) <= n:
        return t
    cut = t[:n + 1].rsplit(" ", 1)[0]   # whole words: "AI Impact", not "AI Impact As."
    return cut if len(cut) >= 6 else t[:n - 1].rstrip() + "."


def when(dt):
    local = dt.astimezone(tz())
    now = datetime.now(tz())
    if local.date() == now.date():
        return "today %s" % local.strftime("%H:%M")
    if 0 < (local.date() - now.date()).days < 7:
        return local.strftime("%a %H:%M")
    return local.strftime("%d %b")


def review_candidates(tail=None):
    """What "approve" would move: like hbb.py review_candidates, minus anything being re-edited right now."""
    skip = in_progress(tail if tail is not None else log_tail())
    out = []
    for j in jobs("review"):
        f = j["_folder"]
        if j.get("stage") == "failed" or j.get("sample") or j.get("hold") or "(FAILED)" in f:
            continue
        if os.path.exists(os.path.join(f, "CHANGES.txt")) or not main_video(f) or j.get("episode") in skip:
            continue
        out.append(j)
    return sorted(out, key=lambda j: (ep_no(j), j.get("created", 0)))


def next_free_slot(after):
    """hbb.py next_slot without importing the editor: the first posting time at or after `after` nobody holds."""
    c = acfg()
    z = tz()
    earliest = after.astimezone(z) + timedelta(minutes=c.get("min_lead_minutes", 90))
    if c.get("first_slot_date"):
        earliest = max(earliest, datetime.fromisoformat(c["first_slot_date"]).replace(tzinfo=z))
    taken = {slot_of(j) for st in ("approved", "scheduled", "posted") for j in jobs(st) if slot_of(j)}
    times = c.get("post_times") or {"mon": ["09:00"]}
    for day in range(0, 120):
        d = (earliest + timedelta(days=day)).date()
        for hm in sorted(times.get(DAYS[d.weekday()], [])):
            h, m = map(int, hm.split(":"))
            s = datetime(d.year, d.month, d.day, h, m, tzinfo=z)
            if s >= earliest and s not in taken:
                return s
    return None


def find_episode(ep):
    for st in ("review", "approved", "scheduled", "posted"):
        for j in jobs(st):
            if j.get("episode") == ep:
                return j["_folder"]
    return None


# ---------------------------------------------------------------- YouTube (cached)

def youtube_numbers():
    cache = rj(CACHE, {}) or {}
    if time.time() - cache.get("at", 0) < 600:
        return cache
    out = {"at": time.time()}
    sys.path.insert(0, PIPE)
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import publish
        yt = publish.youtube()   # its token already has youtube.force-ssl, so it can read comments too
        ch = yt.channels().list(part="statistics", id=CHANNEL).execute()["items"][0]["statistics"]
        out["subs"] = int(ch.get("subscriberCount", 0))
        out["views"] = int(ch.get("viewCount", 0))
        last = latest_live()
        if last:
            v = yt.videos().list(part="statistics", id=last["id"]).execute().get("items", [])
            out["last"] = dict(last, views=int(v[0]["statistics"].get("viewCount", 0)) if v else None)
        th = yt.commentThreads().list(part="snippet", allThreadsRelatedToChannelId=CHANNEL, maxResults=50,
                                      order="time", textFormat="plainText").execute().get("items", [])
        out["comments"] = [{"at": t["snippet"]["topLevelComment"]["snippet"]["publishedAt"],
                            "video": t["snippet"].get("videoId"),
                            "own": (t["snippet"]["topLevelComment"]["snippet"].get("authorChannelId") or {}).get("value") == CHANNEL}
                           for t in th]
    except Exception as e:
        out.update({k: cache[k] for k in ("subs", "views", "last", "comments") if k in cache}, error=str(e)[:200])
        out["at"] = time.time() - 420   # retry in 3 min
    wj(CACHE, out)
    return out


def latest_live():
    """The newest episode that is already public."""
    now = datetime.now(timezone.utc)
    best = None
    for st in ("posted", "scheduled"):
        for j in jobs(st):
            s = slot_of(j)
            yid = (j.get("youtube") or {}).get("id")
            if s and s <= now and yid and (best is None or s > best["slot"]):
                best = {"slot": s, "id": yid, "episode": j.get("episode", "")}
    if best:
        best["slot"] = best["slot"].isoformat()
    return best


# ---------------------------------------------------------------- prompter

PROMPTER_LIB = os.path.join(ROOT, "Prompter", "Scripts")   # Elgato Studio's prompter library
STUDIO_PREFS = os.path.expanduser("~/Library/Containers/com.elgato.studio/Data/Library/Preferences/com.elgato.studio")


def prompter_script():
    """The next HBB script to record: the lowest 'HBB NN ....md' above every episode already in the pipeline."""
    done = [ep_no(j) for st in ("review", "approved", "scheduled", "posted") for j in jobs(st) if ep_no(j) < 999]
    for p in glob.glob(os.path.join(stage_dir("inbox"), "*")):   # a recording waiting in 1 Inbox ("EP15", "15", "Video 15")
        m = re.search(r"(\d+)", os.path.basename(p))
        if m and int(m.group(1)) < 100 and not os.path.basename(p).upper().startswith("C0"):
            done.append(int(m.group(1)))
    top = max(done) if done else 0
    scripts = []
    for p in glob.glob(os.path.join(PROMPTER_LIB, "HBB *.md")):
        m = re.match(r"HBB (\d+) ", os.path.basename(p))
        if m:
            scripts.append((int(m.group(1)), p))
    ahead = sorted(s for s in scripts if s[0] > top)
    if ahead:
        return ahead[0][1]
    return max(scripts)[1] if scripts else None


def recording():
    st = rj(STATE, {}) or {}
    return bool(st.get("rec_start") or (rj(TNP_STATE, {}) or {}).get("rec_start"))


def open_prompter(script):
    """Studio has no 'open this script' command, but it reopens the script saved in its settings when it starts.
    So: quit Studio, point its settings at the script, start it again. Only the script changes, never text size,
    speed or margins. While a REC timer runs (HBB or TNP), Studio is only brought forward."""
    running = lambda: subprocess.run(["pgrep", "-x", "Elgato Studio"], capture_output=True).returncode == 0
    if not script or recording():
        subprocess.run(["open", "-a", "Elgato Studio"])
        return
    if running():
        subprocess.run(["osascript", "-e", 'tell application "Elgato Studio" to quit'], capture_output=True)
        for _ in range(40):
            if not running():
                break
            time.sleep(0.25)
        else:
            subprocess.run(["open", "-a", "Elgato Studio"])   # wouldn't quit (busy): leave it as it is
            return
    import plistlib, uuid
    ref = {"library": {"relativePath": os.path.relpath(script, PROMPTER_LIB)}}
    try:
        with open(STUDIO_PREFS + ".plist", "rb") as f:
            prefs = plistlib.load(f)
    except Exception:
        prefs = {}

    def load(k, default):
        try:
            return json.loads(prefs[k])
        except Exception:
            return default
    last = load("lastOpenedPrompterScript", {})
    for dev in last or {}:
        last[dev] = ref
    tabs = [t for t in load("prompterOpenTabs", []) if t.get("reference") != ref]
    tabs.insert(0, {"id": str(uuid.uuid4()).upper(), "scrollPosition": 0, "reference": ref, "cursorPosition": 0, "selection": 0})
    for k, v in (("lastOpenedPrompterScript", last), ("prompterOpenTabs", tabs)):
        if v:
            subprocess.run(["defaults", "write", STUDIO_PREFS, k, "-data", json.dumps(v, separators=(",", ":")).encode().hex()])
    subprocess.run(["open", "-a", "Elgato Studio"])


# ---------------------------------------------------------------- status

def status():
    state = rj(STATE, {}) or {}
    now = datetime.now(timezone.utc)
    tail = log_tail()
    out = {}

    review = [j for j in jobs("review") if "(FAILED)" not in j["_folder"] and j.get("stage") != "failed"]
    failed = glob.glob(os.path.join(stage_dir("review"), "*(FAILED)"))
    working = in_progress(tail)
    hint = ("%d FAILED" % len(failed)) if failed else ("re-editing" if working else ("waiting" if review else "all clear"))
    out["review"] = {"count": len(review), "failed": len(failed), "new": len(review) > state.get("review_seen", 0),
                     "lines": ["REVIEW", str(len(review)), hint]}

    inbox = [p for p in glob.glob(os.path.join(stage_dir("inbox"), "*")) if not os.path.basename(p).startswith(".")]
    ns = notices(tail)
    bad = ("Edit failed", "Upload failed", "Update failed", "Shorts failed", "Login needed", "4K failed", "Reschedule failed")
    last_bad = next((n for n in reversed(ns) if n[1] in bad), None)
    last_ok = next((n for n in reversed(ns) if n[1] in ("Ready for review", "Updated", "Scheduled", "Short scheduled")), None)
    lines, alert, work = ["PIPELINE", "IDLE", "all clear"], False, busy()
    if last_bad and time.time() - last_bad[0] < 6 * 3600 and (not last_ok or last_ok[0] < last_bad[0]):
        lines, alert = ["PIPELINE", "FAILED" if last_bad[1] != "Login needed" else "LOGIN", last_bad[1].upper()[:14]], True
    elif work:
        last = ns[-1] if ns else (0, "", "")
        ep = last[2].split(":")[0].strip()[:8]
        lines = {"New recording": ["EDITING", "NEW", "20-40 min"], "Changes": ["RE-EDIT", ep, "changes"],
                 "B-roll": ["RENDER", ep, "B-roll"], "Rendering 4K": ["4K", ep, "then upload"],
                 "Rendering Shorts": ["SHORTS", ep, "rendering"]}.get(last[1], ["PIPELINE", "BUSY", "working"])
    elif inbox:
        lines = ["INBOX", str(len(inbox)), "queued"]
    out["pipeline"] = {"lines": lines, "alert": alert, "busy": work}

    upcoming = sorted(((s, j) for j in jobs("approved") + jobs("scheduled") for s in [slot_of(j)] if s and s > now),
                      key=lambda x: x[0])
    if upcoming:
        out["runway"] = {"weeks": len(upcoming), "low": len(upcoming) == 1 and not review,
                         "lines": ["RUNWAY", "%d wk" % len(upcoming), "to " + upcoming[-1][0].astimezone(tz()).strftime("%d %b")]}
        s, j = upcoming[0]
        yid = (j.get("youtube") or {}).get("id")
        out["nextpost"] = {"episode": j.get("episode"), "folder": j["_folder"],
                           "url": "https://studio.youtube.com/video/%s/edit" % yid if yid else None,
                           "lines": ["NEXT", j.get("episode", "?"), when(s)]}
    else:
        out["runway"] = {"weeks": 0, "warn": True, "lines": ["RUNWAY", "0 wk", "record one!"]}
        out["nextpost"] = {"lines": ["NEXT", "none", "scheduled"]}

    yt = youtube_numbers()
    seen = state.get("comments_seen", "")
    new = [c for c in yt.get("comments", []) if c["at"] > seen and not c.get("own")] if seen else []
    out["comments"] = {"new": len(new), "lines": ["COMMENTS", str(len(new)) if seen else "-", "new" if seen else "press to start"]}
    last = yt.get("last") or {}
    out["pulse"] = {"lines": ["%s SUBS" % yt.get("subs", "?"), "%s" % (last.get("views") if last.get("views") is not None else "?"),
                              "views %s" % (last.get("episode") or "last")], "error": yt.get("error")}

    rec = state.get("rec_start")
    out["rec"] = {"on": bool(rec), "start": rec, "lines": ["HBB", "REC", "mode"] if not rec else ["REC", "", "tap to stop"]}

    sc = prompter_script()
    m = re.match(r"HBB (\d+) (.+)\.md$", os.path.basename(sc or ""))
    out["prompter"] = {"script": sc, "lines": ["PROMPTER", "EP.%s" % m.group(1), short_title(m.group(2))] if m
                       else ["PROMPTER", "HBB", "scripts"]}

    vids = sorted((v for j in review for v in [main_video(j["_folder"])] if v), key=os.path.getmtime)
    rv = next((j for j in review if vids and main_video(j["_folder"]) == vids[-1]), None)
    out["quick"] = {"lines": ["WATCH", rv.get("episode", "?") if rv else "-", "in review" if rv else "nothing new"]}

    lq = [x for x in rj(os.path.join(SYSTEM, "linkedin_queue.json"), []) or [] if x.get("status") != "done"]
    lq.sort(key=lambda x: x.get("post_at", ""))
    if lq:
        x = lq[0]
        at = datetime.fromisoformat(x["post_at"])
        late = now > at + timedelta(hours=2)
        ok = x.get("status") == "approved"
        out["linkedin"] = {"episode": x.get("episode"), "alert": late, "attention": not ok,
                           "lines": ["LINKEDIN", x.get("episode", "?"), "LATE" if late else (when(at) if ok else "needs OK")]}
    else:
        out["linkedin"] = {"lines": ["LINKEDIN", "-", "nothing queued"]}

    tq = [x for x in rj(os.path.join(SYSTEM, "thumbnail_queue.json"), []) or [] if x.get("status") == "todo"]
    if tq:
        out["thumbnail"] = {"folder": os.path.join(ROOT, tq[0]["folder"]), "attention": True,
                            "lines": ["THUMBNAIL", tq[0].get("episode", "?"), "%d to make" % len(tq) if len(tq) > 1 else "to make"]}
    elif review:
        j = sorted(review, key=ep_no)[0]
        has = any(os.path.exists(os.path.join(j["_folder"], fn)) for fn in ("THUMBNAIL.jpg", "THUMBNAIL.jpeg", "THUMBNAIL.png"))
        out["thumbnail"] = {"folder": j["_folder"], "attention": not has,
                            "lines": ["THUMBNAIL", j.get("episode", "?"), "ready" if has else "cover only"]}
    else:
        out["thumbnail"] = {"lines": ["THUMBNAIL", "-", "none waiting"]}

    sq = [x for x in rj(os.path.join(SYSTEM, "studio_queue.json"), []) or [] if x.get("status") == "todo"]
    out["studio"] = {"count": len(sq), "url": sq[0].get("studio_url") if sq else None, "attention": bool(sq),
                     "lines": ["STUDIO", str(len(sq)), ("links " + sq[0].get("episode", "")) if sq else "all linked"]}

    cands = review_candidates(tail)
    stamp = rj(os.path.join(LOGS, "review_deadline.json"), {}) or {}
    local = datetime.now(tz())
    week = local.strftime("%G-W%V")
    dl = {"approve": "fri 18:00", **acfg().get("review_deadline", {})}
    day, hm = dl["approve"].split()
    h, mi = map(int, hm.split(":"))
    due = (local.replace(hour=h, minute=mi, second=0, microsecond=0) + timedelta(days=(DAYS.index(day) - local.weekday()) % 7))
    if due < local:
        due += timedelta(days=7)
    slot_then = next_free_slot(due)
    if dl.get("on", True) is False:
        out["auto"] = {"lines": ["AUTO-OK", "OFF", "in config"]}
    elif stamp.get("approved") == week and local.weekday() >= DAYS.index(day):
        out["auto"] = {"lines": ["AUTO-OK", stamp.get("approved_episode", "done"), "done this week"]}
    elif slot_then and slot_then - due > timedelta(days=4):
        out["auto"] = {"lines": ["AUTO-OK", "SKIP", "Monday covered"]}
    elif not cands:
        out["auto"] = {"lines": ["AUTO-OK", "-", "nothing to take"]}
    else:
        soon = due - local < timedelta(hours=24)
        left = due - local
        out["auto"] = {"folder": cands[0]["_folder"], "attention": soon,
                       "lines": ["AUTO-OK", cands[0].get("episode", "?"),
                                 ("in %dh %02dm" % (left.seconds // 3600 + left.days * 24, left.seconds % 3600 // 60)) if soon
                                 else due.strftime("%a %H:%M")]}

    arm = state.get("approve_armed", 0)
    done = state.get("approve_done") or {}
    if time.time() - arm < 4:
        out["approve"] = {"armed": True, "lines": ["SURE?", "press", "again"]}
    elif time.time() - done.get("at", 0) < 15:
        out["approve"] = {"ready": True, "lines": ["APPROVED", str(done.get("n", 0)), "-> 3 Approved"]}
    else:
        big = cands[0].get("episode", "?") if len(cands) == 1 else "ALL %d" % len(cands)
        out["approve"] = {"lines": ["APPROVE", big, "press twice" if cands else "nothing ready"]}
    return out


# ---------------------------------------------------------------- presses

def press(key):
    state = rj(STATE, {}) or {}
    if key == "review":
        state["review_seen"] = len([j for j in jobs("review") if "(FAILED)" not in j["_folder"]])
        subprocess.run(["open", stage_dir("review")])
    elif key == "pipeline":
        subprocess.run(["open", "-a", "Console", os.path.join(LOGS, "hbb.log")])
    elif key == "runway":
        subprocess.run(["open", stage_dir("scheduled")])
    elif key == "nextpost":
        st = status()["nextpost"]
        subprocess.run(["open", st.get("url") or st.get("folder") or stage_dir("scheduled")])
    elif key == "comments":
        cache = rj(CACHE, {}) or {}
        state["comments_seen"] = max([c["at"] for c in cache.get("comments", [])] or
                                     [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")])
        subprocess.run(["open", "https://studio.youtube.com/channel/%s/comments/inbox" % CHANNEL])
    elif key == "pulse":
        subprocess.run(["open", "https://studio.youtube.com/channel/%s/analytics/tab-overview/period-default" % CHANNEL])
    elif key == "rec":
        if state.get("rec_start"):
            sessions = rj(os.path.join(LOGS, "rec_sessions.json"), []) or []
            sessions.append({"start": state["rec_start"], "end": time.time(),
                             "script": os.path.basename(state.get("rec_script") or "")})
            wj(os.path.join(LOGS, "rec_sessions.json"), sessions)
            state.pop("rec_start")
            state.pop("rec_script", None)
        else:
            state["rec_start"] = time.time()
            state["rec_script"] = prompter_script()
            subprocess.run(["open", "-a", "Elgato Studio"])   # Studio runs the camera and the prompter
    elif key == "prompter":
        open_prompter(prompter_script())
    elif key == "quick":
        vids = sorted((v for j in jobs("review") for v in [main_video(j["_folder"])] if v), key=os.path.getmtime)
        if vids:
            subprocess.run(["open", "-a", "QuickTime Player", vids[-1]])
    elif key == "linkedin":
        st = status()["linkedin"]
        folder = find_episode(st.get("episode")) if st.get("episode") else None
        if not folder:
            r = sorted(jobs("review"), key=ep_no)
            folder = r[0]["_folder"] if r else None
        f = os.path.join(folder, "LINKEDIN.txt") if folder else ""
        subprocess.run(["open", "-a", "TextEdit", f] if os.path.exists(f) else ["open", os.path.join(ROOT, "Guides", "LinkedIn Posting.md")])
    elif key == "thumbnail":
        st = status()["thumbnail"]
        folder = st.get("folder")
        if folder:
            p = os.path.join(folder, "THUMBNAIL_PROMPT.txt") if st.get("attention") else None
            if not p or not os.path.exists(p):
                p = next((os.path.join(folder, fn) for fn in ("THUMBNAIL.jpg", "THUMBNAIL.jpeg", "THUMBNAIL.png", "cover.jpg")
                          if os.path.exists(os.path.join(folder, fn))), folder)
            subprocess.run(["open", p])
    elif key == "studio":
        st = status()["studio"]
        subprocess.run(["open", st.get("url") or "https://studio.youtube.com/channel/%s/videos" % CHANNEL])
    elif key == "auto":
        st = status()["auto"]
        subprocess.run(["open", st.get("folder") or os.path.join(ROOT, "Guides", "Autopilot Guide.md")])
    elif key == "approve":
        if time.time() - state.get("approve_armed", 0) < 4:
            state["approve_armed"] = 0
            state["approve_done"] = {"at": time.time(), "n": approve_all()}
        else:
            state["approve_armed"] = time.time()
    wj(STATE, state)


def approve_all():
    """Everything ready in 2 Review -> 3 Approved, in episode order: the same as moving the folders in Finder. That
    approves each episode's video, thumbnail, LinkedIn post and Shorts; the autopilot gives them the next Mondays."""
    n, moved = 0, []
    for j in review_candidates():
        f = j["_folder"]
        dest = os.path.join(stage_dir("approved"), os.path.basename(f))
        if os.path.exists(dest):
            continue
        shutil.move(f, dest)
        moved.append(j.get("episode", os.path.basename(f)))
        n += 1
    with open(os.path.join(LOGS, "hbb.log"), "a") as lf:
        lf.write("%s  deck: approved %s from 2 Review\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                            ", ".join(moved) if moved else "nothing"))
    return n


if __name__ == "__main__":
    cmd = sys.argv[1:] or ["status"]
    if cmd[0] == "status":
        print(json.dumps(status(), ensure_ascii=False))
    elif cmd[0] == "press" and len(cmd) > 1:
        press(cmd[1])
    else:
        sys.exit(__doc__)
