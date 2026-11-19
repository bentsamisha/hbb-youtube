"""HBB video editor: raw talking-head footage + episode script -> finished landscape YouTube video.

  editor.py analyze <video or folder> [--episode N]   transcribe, match the script, write System/work/<job>/draft.json
  editor.py render <job>                              render System/work/<job>/edit.json (or draft.json) to final.mp4
  editor.py sample <job> <from> <to>                  render only a piece (output seconds) to check the look

Work folder System/work/<job>/:
  source.mp4        the raw footage (several camera clips are joined in name order)
  words.json        every spoken word with its time (Whisper, runs locally, nothing is uploaded)
  transcript.txt    readable transcript, one phrase per line with SOURCE times
  script.json       the parsed episode script (rows of the production table and their cues)
  draft.json        the automatic edit: the best take of every script row, pauses tightened, all cues placed
  edit.json         the final edit (the Claude editor writes it from draft.json; same format)
  post.json         YouTube text (title options, description parts, chapters, tags, thumbnail text...)
  final.mp4, captions.srt, thumbnail.jpg, chapters.txt   the outputs

All times in draft.json / edit.json are SOURCE seconds; the renderer maps them to the output timeline.
"""
import difflib
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave

PIPE = os.path.dirname(os.path.realpath(__file__))
SYSTEM = os.path.dirname(PIPE)
ROOT = os.path.dirname(SYSTEM)
WORK = os.path.join(SYSTEM, "work")
ASSETS = os.path.join(PIPE, "assets")
FONTS = os.path.join(ASSETS, "fonts")
VIDEO_EXT = (".mp4", ".mov", ".mts", ".m4v", ".mkv")
sys.path.insert(0, PIPE)
import scripts  # noqa: E402


def load_config():
    with open(os.path.join(PIPE, "config.json")) as f:
        return json.load(f)


def run(cmd, quiet=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("command failed: %s\n%s" % (" ".join(cmd[:6]), r.stderr[-2500:]))
    return r.stdout


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def probe(path):
    out = json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate:stream_tags=rotate",
                          "-of", "json", path]))
    v = next((s for s in out["streams"] if s["codec_type"] == "video"), {})
    num, den = (v.get("r_frame_rate") or "25/1").split("/")
    return {"duration": float(out["format"]["duration"]), "width": v.get("width"), "height": v.get("height"),
            "fps": round(float(num) / float(den), 3)}


def mmss(t):
    t = max(0, int(round(t)))
    return "%02d:%02d" % (t // 60, t % 60) if t < 3600 else "%d:%02d:%02d" % (t // 3600, t // 60 % 60, t % 60)


# ---------------------------------------------------------------- 1. analyze

def gather(path):
    """A video file, or a folder of camera clips (Sony: C0001.MP4, C0002.MP4 ...) in name order."""
    if os.path.isdir(path):
        vids = [p for p in sorted(glob.glob(os.path.join(path, "**", "*"), recursive=True))
                if p.lower().endswith(VIDEO_EXT) and "b-roll" not in p.lower() and not os.path.basename(p).startswith((".", "broll"))]
        return vids
    return [path]


def join_sources(files, dest):
    if len(files) == 1:
        if os.path.lexists(dest):
            os.remove(dest)
        os.symlink(os.path.abspath(files[0]), dest)
        return
    lst = dest + ".txt"
    with open(lst, "w") as f:
        for p in files:
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", "'\\''"))
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", dest])


def transcribe(wav, cfg):
    import numpy as np
    from faster_whisper import WhisperModel
    w = wave.open(wav)
    audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(os.environ.get("HBB_WHISPER_MODEL") or cfg.get("whisper_model_path") or cfg.get("whisper_model", "small"), device="cpu", compute_type="int8")
    segs, _ = model.transcribe(audio, language=cfg.get("language", "en"), word_timestamps=True,
                               initial_prompt=cfg.get("whisper_prompt"), vad_filter=False,
                               condition_on_previous_text=False)
    words = []
    for s in segs:
        for wd in s.words or []:
            words.append({"w": wd.word.strip(), "s": round(wd.start, 3), "e": round(wd.end, 3)})
    return words


def apply_fixes(words, fixes):
    """[heard, correct] pairs, matched word by word (case-insensitive, punctuation ignored)."""
    for heard, right in fixes:
        hw = [norm(x) for x in heard.split()]
        rw = right.split()
        i = 0
        while i + len(hw) <= len(words):
            if [norm(w["w"]) for w in words[i:i + len(hw)]] == hw:
                tail = re.sub(r"^.*?([.,!?;:]*)$", r"\1", words[i + len(hw) - 1]["w"])
                new = [{"w": rw[k] + (tail if k == len(rw) - 1 else ""), "s": words[i]["s"] if k == 0 else words[i + min(k, len(hw) - 1)]["s"],
                        "e": words[i + min(k, len(hw) - 1)]["e"]} for k in range(len(rw))]
                new[-1]["e"] = words[i + len(hw) - 1]["e"]
                words[i:i + len(hw)] = new
                i += len(rw)
            else:
                i += 1
    return words


def silences(media, cfg):
    sc = cfg["cut"]
    out = subprocess.run(["ffmpeg", "-i", media, "-vn", "-af", "highpass=f=100,silencedetect=noise=%ddB:d=%.2f" % (
        sc["noise_db"], sc["min_silence"]), "-f", "null", "-"], capture_output=True, text=True).stderr
    res, start = [], None
    for ln in out.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", ln)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", ln)
        if m and start is not None:
            res.append([round(start, 3), round(float(m.group(1)), 3)])
            start = None
    return res


def norm(w):
    w = w.lower().replace("\u2019", "'")
    w = re.sub(r"[^\w']", "", w)
    return {"organisation": "organization", "organisations": "organizations", "&": "and"}.get(w, w)


# contractions and filler words differ between script and speech ("I'm" vs "I am"): the matcher ignores them
SOFT = {"am", "is", "are", "was", "will", "would", "not", "have", "has", "had", "the", "a", "an", "uh", "um", "so",
        "and", "of", "that", "this", "it", "s", "re", "ll", "ve", "d", "m", "t", "number", "do", "does"}


def mtok(w):
    """Matching token: 'I'm' -> 'i', 'doesn't' -> 'does', soft words -> None."""
    w = norm(w).split("'")[0]
    return None if (not w or w in SOFT) else w


def tokens(text):
    return [t for t in (mtok(x) for x in re.split(r"[\s/\-]+", text)) if t]


def write_transcript(words, path, gap=0.6):
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if not nxt or nxt["s"] - w["e"] > gap or (w["w"].endswith((".", "?", "!")) and len(cur) > 12):
            lines.append("[%s | %.2f-%.2f] %s" % (mmss(cur[0]["s"]), cur[0]["s"], cur[-1]["e"], " ".join(x["w"] for x in cur)))
            if nxt and nxt["s"] - w["e"] > 1.5:
                lines.append("--- %.1f s no talking (%.2f-%.2f) ---" % (nxt["s"] - w["e"], w["e"], nxt["s"]))
            cur = []
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def episode_from_name(name):
    m = re.search(r"(?:^|[^a-z])(?:ep|episode|video|vid)[ ._#-]*(\d{1,3})(?!\d)", name, re.I) or re.match(r"^(\d{1,3})(?!\d)", name)
    return int(m.group(1)) if m else None


def guess_script(words):
    """No episode number given: compare the first ~2 minutes of talk with every script's opening."""
    head = [norm(w["w"]) for w in words[:350]]
    best = (0.0, None)
    for path, sc in scripts.all_scripts():
        st = tokens(" ".join(r["text"] for r in sc["rows"][:6]))
        score = difflib.SequenceMatcher(None, head, st, autojunk=False).ratio()
        if score > best[0]:
            best = (score, sc["number"])
    return best[1] if best[0] > 0.35 else None


def analyze(src, job=None, episode=None, log=print):
    cfg = load_config()
    files = gather(src)
    if not files:
        raise RuntimeError("no video files in %s" % src)
    job = job or os.path.splitext(os.path.basename(src.rstrip("/")))[0]
    jd = os.path.join(WORK, job)
    os.makedirs(jd, exist_ok=True)
    source = os.path.join(jd, "source.mp4")
    join_sources(files, source)
    info = probe(source)
    wav = os.path.join(jd, "audio16k.wav")
    if not os.path.exists(os.path.join(jd, "words.json")):
        log("transcribing %s (%.0f s of footage)" % (job, info["duration"]))
        run(["ffmpeg", "-v", "error", "-y", "-i", source, "-vn", "-ac", "1", "-ar", "16000", wav])
        words = transcribe(wav, cfg)
        write_json(os.path.join(jd, "words.json"), words)
    words = apply_fixes(read_json(os.path.join(jd, "words.json")), cfg.get("fixes", []))
    write_transcript(words, os.path.join(jd, "transcript.txt"))
    sil = silences(source, cfg)
    episode = episode or episode_from_name(os.path.basename(src.rstrip("/"))) or guess_script(words)
    sc, spath = scripts.load(episode) if episode else (None, None)
    if sc:
        write_json(os.path.join(jd, "script.json"), sc)
        shutil.copy(spath, os.path.join(jd, "script.md"))
    draft = auto_edit(words, sil, sc, info, cfg)
    draft.update(source=source, episode=episode, job=job, files=[os.path.abspath(f) for f in files], info=info)
    write_json(os.path.join(jd, "draft.json"), draft)
    log("draft: %s, %d rows matched, %.0f s -> %.0f s" % (
        job, sum(1 for r in draft["rows"] if r.get("start") is not None), info["duration"], Timeline(draft["keep"]).duration))
    return jd


# ---------------------------------------------------------------- 2. automatic edit

def find_takes(wn, row_tokens, min_score):
    """All places in the transcript where the row is spoken (complete or not), best first by position.
    wn: normalized transcript words. Returns [(score, i0, i1)] (i1 exclusive)."""
    n, L = len(wn), len(row_tokens)
    if not L:
        return []
    first = set(row_tokens[:5])
    cands = []
    for i in range(n):
        if wn[i] not in first:
            continue
        best = None
        for span in (int(L * 0.8), L, int(L * 1.15) + 2):
            j = min(n, i + max(1, span))
            sm = difflib.SequenceMatcher(None, wn[i:j], row_tokens, autojunk=False)
            blocks = sm.get_matching_blocks()
            matched = sum(b.size for b in blocks)
            score = matched / L
            if best is None or score > best[0]:
                last = max((b.a + b.size for b in blocks if b.size), default=1)
                best = (score, i, i + last)
        cands.append(best)
    # drop near-duplicates (same take found from neighbouring start words)
    cands.sort(key=lambda c: (-c[0], c[1]))
    picked = []
    for c in cands:
        if c[0] < min_score * 0.6:
            continue
        if all(c[2] <= p[1] or c[1] >= p[2] for p in picked):
            picked.append(c)
    return sorted(picked, key=lambda c: c[1])


def inner_retakes(seg_words, min_len=4):
    """Inside one take: he repeats a phrase ("this is the most... this is the most common question").
    Returns [i0, i1) word ranges to drop (the abandoned first attempt)."""
    toks = [norm(w["w"]) for w in seg_words]
    drops = []
    i = 0
    while i < len(toks) - min_len:
        gram = toks[i:i + min_len]
        hit = None
        for j in range(i + 1, min(len(toks) - min_len + 1, i + 25)):
            if toks[j:j + min_len] == gram:
                hit = j
                break
        if hit is not None:
            drops.append((i, hit))
            i = hit
        else:
            i += 1
    return drops


def auto_edit(words, sil, sc, info, cfg):
    cut = cfg["cut"]
    idx = [i for i, w in enumerate(words) if mtok(w["w"])]
    wn = [mtok(words[i]["w"]) for i in idx]
    rows = []
    spans = []  # (row_index, w0, w1)
    if sc:
        pos = 0
        for ri, row in enumerate(sc["rows"]):
            rt = tokens(row["text"])
            takes = [t for t in find_takes(wn, rt, cut["min_take_score"]) if t[1] >= pos]
            good = [t for t in takes if t[0] >= cut["min_take_score"]]
            rec = {"i": ri, "text": row["text"][:90], "cues": row["cues"], "takes": len(takes)}
            if good:
                # the last good take wins (he restarts until it's right); unless an earlier one is clearly better
                top = max(t[0] for t in good)
                take = [t for t in good if t[0] >= top - 0.08][-1]
                w0 = idx[take[1]]
                w1 = idx[take[2] - 1] + 1
                while w0 > 0 and not mtok(words[w0 - 1]["w"]) and words[w0]["s"] - words[w0 - 1]["e"] < 0.3:
                    w0 -= 1  # take back a leading "I", "The", "So" the matcher ignored
                rec.update(score=round(take[0], 2), start=words[w0]["s"], end=words[w1 - 1]["e"])
                spans.append((ri, w0, w1))
                pos = take[2]
            else:
                rec.update(score=round(takes[-1][0], 2) if takes else 0.0, start=None, end=None,
                           note="not found in the footage" if not takes else "only a partial take")
            rows.append(rec)
    else:
        # no script: keep all talk, the editor removes retakes by reading the transcript
        spans = [(None, 0, len(words))] if words else []
    keep = []
    for ri, w0, w1 in spans:
        seg = words[w0:w1]
        drop = set()
        for a, b in inner_retakes(seg):
            drop.update(range(a, b))
        run_start = None
        for k, w in enumerate(seg):
            if k in drop:
                continue
            prev = seg[k - 1] if k > 0 and (k - 1) not in drop else None
            if run_start is None:
                run_start = w["s"] - cut["pad_before"]
            elif prev is None or w["s"] - prev["e"] > cut["max_pause"]:
                # close the previous run after a natural breath, start a new one
                last = seg[max(i for i in range(k) if i not in drop)]
                keep.append([run_start, last["e"] + min(cut["pad_after"], cut["keep_pause"])])
                run_start = w["s"] - min(cut["pad_before"], cut["keep_pause"])
        if run_start is not None:
            last = seg[max(i for i in range(len(seg)) if i not in drop)]
            keep.append([run_start, last["e"] + cut["pad_after"]])
    keep = merge_ranges([[max(0.0, round(a, 3)), round(min(b, info["duration"]), 3)] for a, b in keep])
    zooms = default_zooms(keep, rows, cfg)
    return {"keep": keep, "rows": rows, "zoom": zooms, "overlays": cue_overlays(rows, cfg),
            "broll": cue_broll(rows), "cards": cue_cards(rows), "lower_thirds": cue_lower_thirds(rows, cfg)}


def merge_ranges(rs, gap=0.05):
    out = []
    for a, b in sorted(rs):
        if b - a < 0.15:
            continue
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def row_time(rows, ri):
    r = rows[ri]
    return (r["start"], r["end"]) if r.get("start") is not None else (None, None)


def default_zooms(keep, rows, cfg):
    """Camera feel on a single locked-off shot: script cues (slow zoom in/out), plus a punch-in that alternates
    at every retake cut so jump cuts read as deliberate. Output: [{"t0","t1","kind"}] in source seconds."""
    z = []
    for r in rows:
        k = r["cues"].get("zoom")
        if k and r.get("start") is not None:
            z.append({"t0": r["start"], "t1": r["end"], "kind": k})
    return z


def cue_overlays(rows, cfg):
    out = []
    for r in rows:
        on = r["cues"].get("onscreen")
        if not on or r.get("start") is None:
            continue
        dur = r["end"] - r["start"]
        out.append({"t": round(r["start"] + 0.4, 2), "dur": round(min(max(4.0, dur - 0.6), 9.0), 2), "lines": on,
                    "accent": r["cues"].get("accent", []), "row": r["i"]})
    return out


def cue_broll(rows):
    out, n = [], 0
    for r in rows:
        b = r["cues"].get("broll")
        if not b:
            continue
        n += 1
        out.append({"n": n, "t": round(r["start"] + 0.5, 2) if r.get("start") is not None else None,
                    "dur": b["seconds"], "prompt": b["prompt"], "row": r["i"], "file": ""})
    return out


def cue_cards(rows):
    return [{"t": r["start"], "what": r["cues"]["card"], "row": r["i"]} for r in rows
            if r["cues"].get("card") and r.get("start") is not None]


def cue_lower_thirds(rows, cfg):
    out = []
    for r in rows:
        lt = r["cues"].get("lower_third")
        if lt and r.get("start") is not None:
            out.append({"t": round(r["start"] + 0.8, 2), "dur": 5.0, "kind": "name" if lt == "name" else "company"})
    if not any(x["kind"] == "name" for x in out):
        # the intro line ("I'm Mykhailo Bentsa...") always gets the name card
        for r in rows:
            if r.get("start") is not None and re.search(r"\bI'm Mykhailo\b", r["text"]) and "Bentsa" in r["text"]:
                out.append({"t": round(r["start"] + 0.8, 2), "dur": 5.0, "kind": "name"})
                break
    return sorted(out, key=lambda x: x["t"])


# ---------------------------------------------------------------- timeline

class Timeline:
    def __init__(self, keep):
        self.keep = [list(k[:2]) for k in keep]
        self.starts = []
        t = 0.0
        for a, b in self.keep:
            self.starts.append(t)
            t += b - a
        self.duration = t

    def out(self, src):
        """Source second -> output second (a cut-out moment snaps to the next kept moment)."""
        for (a, b), o in zip(self.keep, self.starts):
            if src < a:
                return o
            if src <= b:
                return o + src - a
        return self.duration

    def segment_of(self, src):
        for i, (a, b) in enumerate(self.keep):
            if a <= src <= b:
                return i
        return None


# ---------------------------------------------------------------- 3. render

def encoder_args(cfg, final=False):
    enc = cfg["output"].get("encoder", "auto")
    if enc == "auto":
        encs = run(["ffmpeg", "-hide_banner", "-encoders"])
        enc = "h264_videotoolbox" if "h264_videotoolbox" in encs else "libx264"
    if enc == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", cfg["output"]["bitrate"] if final else "60M", "-allow_sw", "1"]
    return ["-c:v", "libx264", "-preset", cfg["output"].get("preset", "medium") if final else "veryfast",
            "-crf", str(cfg["output"]["crf"] if final else 14)]


def out_size(info, cfg):
    h = min(cfg["output"]["height"], info["height"])
    w = int(round(info["width"] * h / info["height"] / 2) * 2)
    return w, h


def zoom_filter(kind, seg_dur, w, h, cfg, z_static=1.0):
    """Scale+crop for one segment. kind: None (static at z_static), 'slow_in', 'slow_out'."""
    look = cfg["look"]
    fx, fy = look["face_x"], look["face_y"]
    amt = look["slow_zoom"]
    if kind in ("slow_in", "slow_out"):
        d = max(seg_dur, 0.5)
        z = "(%f+%f*t/%f)" % (z_static, amt, d) if kind == "slow_in" else "(%f-%f*t/%f)" % (z_static + amt, amt, d)
        return ("scale=w='trunc(%d*%s/2)*2':h='trunc(%d*%s/2)*2':eval=frame,"
                "crop=%d:%d:x='(in_w-%d)*%f':y='(in_h-%d)*%f'" % (w, z, h, z, w, h, w, fx, h, fy))
    if abs(z_static - 1.0) > 1e-3:
        sw, sh = int(w * z_static / 2) * 2, int(h * z_static / 2) * 2
        return "scale=%d:%d,crop=%d:%d:%d:%d" % (sw, sh, w, h, int((sw - w) * fx), int((sh - h) * fy))
    return "scale=%d:%d" % (w, h)


def segment_plan(edit, cfg):
    """Each kept range -> (start, end, zoom kind, static zoom). Punch-in alternates where a cut skips a retake
    or a long pause, so the jump cut looks intentional; slow zooms follow the script cues."""
    punch = cfg["look"]["zoom_punch"]
    plan, level = [], 1.0
    prev_end = None
    fps = cfg["output"].get("fps") or edit["info"]["fps"]
    for a, b in edit["keep"]:
        # whole frames only, so video and audio of every piece are exactly as long (no drift over 100+ cuts)
        a, b = round(a * fps) / fps, round(b * fps) / fps
        kind = None
        for zc in edit.get("zoom", []):
            if zc["t0"] - 0.3 <= a <= zc["t1"] and zc["kind"] in ("slow_in", "slow_out"):
                kind = zc["kind"]
            if zc["t0"] - 0.3 <= a <= zc["t1"] and zc["kind"] == "in":
                level = punch
        if prev_end is not None and a - prev_end > cfg["look"]["punch_after_gap"]:
            level = punch if level == 1.0 else 1.0
        plan.append((a, b, kind, level))
        prev_end = b
    return plan


def cut_segments(jd, edit, cfg, w, h, fps):
    seg_dir = os.path.join(jd, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    files = []
    src = edit["source"]
    for i, (a, b, kind, level) in enumerate(segment_plan(edit, cfg)):
        vf = zoom_filter(kind, b - a, w, h, cfg, level)
        key = hashlib.md5(json.dumps([src, a, b, vf, w, h, fps, cfg["output"].get("encoder")]).encode()).hexdigest()[:12]
        out = os.path.join(seg_dir, "%04d_%s.mov" % (i, key))
        if not os.path.exists(out):
            fade = min(0.012, (b - a) / 4)
            run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % a, "-to", "%.3f" % b, "-i", src,
                 "-vf", "%s,fps=%s,setsar=1,format=yuv420p" % (vf, fps),
                 "-af", "afade=t=in:d=%.3f,afade=t=out:st=%.3f:d=%.3f,aresample=48000" % (fade, b - a - fade, fade),
                 "-ac", "2"] + encoder_args(cfg) + ["-c:a", "pcm_s16le", out])
        files.append(out)
    # drop stale segments from earlier edits
    for f in glob.glob(os.path.join(seg_dir, "*.mov")):
        if f not in files:
            os.remove(f)
    lst = os.path.join(jd, "segments.txt")
    with open(lst, "w") as f:
        for p in files:
            f.write("file '%s'\n" % p)
    body = os.path.join(jd, "body.mov")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", body])
    return body


# --------------------------------------------- graphics (ASS subtitles: captions, on-screen text, lower thirds)

def ass_color(hexrgb, alpha=0):
    r, g, b = hexrgb[0:2], hexrgb[2:4], hexrgb[4:6]
    return "&H%02X%s%s%s" % (alpha, b, g, r)


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return "%d:%02d:%02d.%02d" % (cs // 360000, cs // 6000 % 60, cs // 100 % 60, cs % 100)


def ass_escape(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def no_em_dash(s):
    return s.replace(" — ", " - ").replace("—", " - ").replace("–", "-")


def caption_chunks(words, tl, cfg):
    """Spoken words (only the kept ones) -> short caption lines in output time."""
    cc = cfg["captions"]
    kept = []
    for w in words:
        mid = (w["s"] + w["e"]) / 2
        if tl.segment_of(mid) is not None:
            kept.append({"w": w["w"], "s": tl.out(max(w["s"], tl.keep[tl.segment_of(mid)][0])), "e": tl.out(min(w["e"], tl.keep[tl.segment_of(mid)][1]))})
    chunks, cur = [], []
    for i, w in enumerate(kept):
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        nxt = kept[i + 1] if i + 1 < len(kept) else None
        if (not nxt or len(cur) >= cc["max_words"] or len(text) >= cc["max_chars"] or w["w"].endswith((".", "?", "!"))
                or (w["w"].endswith(",") and len(cur) >= 3) or nxt["s"] - w["e"] > 0.5):
            chunks.append({"s": cur[0]["s"], "e": max(cur[-1]["e"], cur[0]["s"] + 0.6), "text": text})
            cur = []
    for a, b in zip(chunks, chunks[1:]):
        if b["s"] - a["e"] < 0.3:
            a["e"] = b["s"]
    return chunks


def write_srt(chunks, path):
    def ts(t):
        ms = int(round(t * 1000))
        return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
    with open(path, "w") as f:
        for i, c in enumerate(chunks, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (i, ts(c["s"]), ts(c["e"]), c["text"]))


def build_ass(path, edit, words, tl, w, h, cfg, end_start):
    b = cfg["brand"]
    s = h / 1080.0
    cream, char, gold, grey = b["cream"], b["charcoal"], b["gold"], b["grey"]
    styles = [
        # name, font, size, primary, outline colour, back colour, bold, border style, outline, shadow, align, marginV
        ("Caption", "Inter SemiBold", 46, "FFFFFF", char, char, 0, 3, 10, 0, 2, 70, 0x40),
        ("Callout", "DM Serif Display", 58, char, cream, cream, 0, 3, 22, 0, 7, 0, 0x08),
        ("CalloutSmall", "Inter SemiBold", 40, char, cream, cream, 0, 3, 18, 0, 7, 0, 0x08),
        ("LTName", "DM Serif Display", 62, char, cream, cream, 0, 3, 18, 0, 1, 0, 0x00),
        ("LTSub", "Inter Medium", 34, grey, cream, cream, 0, 3, 14, 0, 1, 0, 0x00),
        ("Bar", "Inter", 20, gold, gold, gold, 0, 1, 0, 0, 7, 0, 0x00),
    ]
    head = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % w, "PlayResY: %d" % h, "WrapStyle: 0",
            "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    for name, font, size, prim, outl, back, bold, bs, ol, sh, al, mv, alpha in styles:
        head.append("Style: %s,%s,%d,%s,%s,%s,%s,%d,0,0,0,100,100,0,0,%d,%d,%d,%d,%d,%d,%d,1" % (
            name, font, round(size * s), ass_color(prim), ass_color(prim), ass_color(outl, alpha), ass_color(back, alpha),
            -1 if bold else 0, bs, round(ol * s), sh, al, round(60 * s), round(60 * s), round(mv * s)))
    ev = ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def add(layer, t0, t1, style, text):
        t1 = min(t1, end_start - 0.05)
        if t1 - t0 > 0.2:
            ev.append("Dialogue: %d,%s,%s,%s,,0,0,0,,%s" % (layer, ass_time(t0), ass_time(t1), style, text))

    if cfg["captions"].get("burn", True):
        for c in caption_chunks(words, tl, cfg):
            add(0, c["s"], c["e"], "Caption", ass_escape(no_em_dash(c["text"])))
    x0, y0 = round(90 * s), round(90 * s)
    gold_tag = "{\\c%s}" % ass_color(gold)
    for o in edit.get("overlays", []):
        t0 = tl.out(o["t"])
        t1 = t0 + o.get("dur", 5)
        lines = [no_em_dash(x) for x in o["lines"]]
        first, rest = lines[0], lines[1:]
        txt = ass_escape(first)
        for acc in o.get("accent", []):
            txt = re.sub(r"(?i)\b(%s)\b" % re.escape(acc), lambda m: gold_tag + m.group(1) + "{\\r}", txt, count=1)
        big = len(first) <= 46 and not rest
        style = "Callout" if big else "CalloutSmall"
        body = txt + ("\\N" + "\\N".join(ass_escape(x) for x in rest) if rest else "")
        add(2, t0, t1, style, "{\\fad(250,250)\\pos(%d,%d)}%s" % (x0 + round(14 * s), y0, body))
        # honey gold accent bar to the left of the card
        bar_h = round((70 if big else 56) * s * (1 + len(rest) * 0.95))
        add(3, t0, t1, "Bar", "{\\fad(250,250)\\pos(%d,%d)\\p1}m 0 0 l %d 0 l %d %d l 0 %d{\\p0}" % (
            x0 - round(26 * s), y0 - round(22 * s), round(8 * s), round(8 * s), bar_h, bar_h))
    for lt in edit.get("lower_thirds", []):
        t0 = tl.out(lt["t"])
        t1 = t0 + lt.get("dur", 5)
        name, sub = (b["name"], b["credentials"]) if lt.get("kind") == "name" else (b["company"], b["tagline"])
        yb = h - round(250 * s)
        add(2, t0, t1, "LTName", "{\\fad(300,300)\\pos(%d,%d)}%s" % (round(110 * s), yb - round(62 * s), ass_escape(name)))
        add(2, t0, t1, "LTSub", "{\\fad(300,300)\\pos(%d,%d)}%s" % (round(110 * s), yb + round(10 * s), ass_escape(no_em_dash(sub))))
        add(3, t0, t1, "Bar", "{\\fad(300,300)\\pos(%d,%d)\\p1}m 0 0 l %d 0 l %d %d l 0 %d{\\p0}" % (
            round(78 * s), yb - round(150 * s), round(8 * s), round(8 * s), round(172 * s), round(172 * s)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(head + ev) + "\n")


def font(name, size):
    from PIL import ImageFont
    files = {"serif": "DMSerifDisplay_400Regular.ttf", "sans": "Inter_600SemiBold.ttf", "sans_medium": "Inter_500Medium.ttf",
             "sans_bold": "Inter_700Bold.ttf"}
    return ImageFont.truetype(os.path.join(FONTS, files[name]), size)


def end_card(path, w, h, cfg, next_title=""):
    """Last 20 s: cream end screen with the logo, sign-off and two empty frames for YouTube's end-screen videos."""
    from PIL import Image, ImageDraw
    b = cfg["brand"]
    im = Image.new("RGB", (w, h), "#" + b["cream"])
    d = ImageDraw.Draw(im)
    s = h / 1080.0
    logo = Image.open(os.path.join(ASSETS, "logo.png")).convert("RGBA")
    bbox = logo.getchannel("A").point(lambda v: 255 if v > 200 else 0).getbbox()
    logo = logo.crop(bbox) if bbox else logo
    lh = int(300 * s)
    logo = logo.resize((int(logo.width * lh / logo.height), lh))
    im.paste(logo, ((w - logo.width) // 2, int(90 * s)), logo)
    f1, f2 = font("serif", int(64 * s)), font("sans_medium", int(30 * s))
    line1 = "Thanks for watching"
    d.text(((w - d.textlength(line1, font=f1)) / 2, int(420 * s)), line1, font=f1, fill="#" + b["charcoal"])
    line2 = no_em_dash(b["signoff"])
    d.text(((w - d.textlength(line2, font=f2)) / 2, int(510 * s)), line2, font=f2, fill="#" + b["grey"])
    # two video slots (16:9) and the subscribe circle where YouTube places end-screen elements
    vw, vh = int(560 * s), int(315 * s)
    y = int(610 * s)
    for x in (int(w / 2 - vw - 40 * s), int(w / 2 + 40 * s)):
        d.rounded_rectangle([x, y, x + vw, y + vh], radius=int(16 * s), outline="#" + b["gold"], width=max(2, int(4 * s)))
    lbl = font("sans", int(26 * s))
    for x, t in ((int(w / 2 - vw - 40 * s), "Watch next"), (int(w / 2 + 40 * s), "Start with this one")):
        d.text((x + (vw - d.textlength(t, font=lbl)) / 2, y + vh + int(14 * s)), t, font=lbl, fill="#" + b["grey"])
    im.save(path)


def end_card_clip(jd, w, h, fps, cfg):
    png = os.path.join(jd, "end_card.png")
    end_card(png, w, h, cfg)
    out = os.path.join(jd, "end_card.mov")
    secs = cfg["end_screen_seconds"]
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-t", str(secs), "-i", png,
         "-f", "lavfi", "-t", str(secs), "-i", "anullsrc=r=48000:cl=stereo",
         "-vf", "fade=t=in:d=0.6,format=yuv420p,setsar=1", "-shortest"] + encoder_args(cfg) + ["-c:a", "pcm_s16le", out])
    return out


def find_broll(jd, edit):
    """B-roll clips: edit.json "file", else broll_NN.* in the work folder or the episode's B-roll folder."""
    dirs = [jd, os.path.join(jd, "B-roll")] + edit.get("broll_dirs", [])
    for b in edit.get("broll", []):
        if b.get("file") and os.path.exists(b["file"]):
            continue
        b["file"] = ""
        for d in dirs:
            hits = sorted(glob.glob(os.path.join(d, "broll_%02d.*" % b["n"])) + glob.glob(os.path.join(d, "broll_%d.*" % b["n"])))
            hits = [x for x in hits if x.lower().endswith(VIDEO_EXT + (".jpg", ".png"))]
            if hits:
                b["file"] = hits[0]
                break
    return [b for b in edit.get("broll", []) if b.get("file") and b.get("t") is not None]


def render(job, part=None, log=print):
    cfg = load_config()
    jd = os.path.join(WORK, job)
    edit = read_json(os.path.join(jd, "edit.json")) or read_json(os.path.join(jd, "draft.json"))
    info = edit["info"]
    fps = cfg["output"].get("fps") or info["fps"]
    w, h = out_size(info, cfg)
    words = apply_fixes(read_json(os.path.join(jd, "words.json")), cfg.get("fixes", []) + edit.get("fixes", []))
    tl = Timeline(edit["keep"])
    log("render %s: %d cuts, %.1f s + %d s end screen at %dx%d" % (job, len(edit["keep"]), tl.duration, cfg["end_screen_seconds"], w, h))
    body = cut_segments(jd, edit, cfg, w, h, fps)
    endc = end_card_clip(jd, w, h, fps, cfg)
    end_start = tl.duration
    ass = os.path.join(jd, "graphics.ass")
    build_ass(ass, edit, words, tl, w, h, cfg, end_start)
    brolls = find_broll(jd, edit)
    inputs = ["-i", body, "-i", endc]
    graph = ["[0:v]setpts=PTS-STARTPTS[v0]"]
    last = "v0"
    for k, b in enumerate(brolls):
        t0 = tl.out(b["t"])
        dur = min(b["dur"], max(0.5, end_start - t0 - 0.2))
        if b["file"].lower().endswith((".jpg", ".png")):
            inputs += ["-loop", "1", "-t", "%.2f" % dur, "-i", b["file"]]
        else:
            inputs += ["-stream_loop", "-1", "-t", "%.2f" % dur, "-i", b["file"]]
        idx = 2 + k
        graph.append("[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,fps=%s,format=yuv420p,"
                     "fade=t=in:d=0.35:alpha=1,fade=t=out:st=%.2f:d=0.35:alpha=1,setpts=PTS-STARTPTS+%.3f/TB[b%d]"
                     % (idx, w, h, w, h, fps, max(0.1, dur - 0.35), t0, k))
        graph.append("[%s][b%d]overlay=eof_action=pass:enable='between(t,%.3f,%.3f)'[v%d]" % (last, k, t0, t0 + dur, k + 1))
        last = "v%d" % (k + 1)
    fonts_dir = FONTS.replace(":", "\\:").replace("'", "\\'")
    graph.append("[%s]fade=t=out:st=%.3f:d=0.4[vb]" % (last, max(0, end_start - 0.4)))
    graph.append("[vb][0:a][1:v][1:a]concat=n=2:v=1:a=1[vc][ac]")
    graph.append("[vc]ass='%s':fontsdir='%s',format=yuv420p[vout]" % (ass.replace("'", "\\'"), fonts_dir))
    a = cfg["audio"]
    chain = ["highpass=f=%d" % a["highpass"]]
    if a.get("denoise"):
        chain.append("afftdn=nr=%d:nf=-40" % a.get("denoise_db", 10))
    chain += ["acompressor=threshold=-20dB:ratio=2.5:attack=8:release=150",
              "loudnorm=I=%d:TP=-1.5:LRA=9" % a["lufs"], "aresample=48000"]
    graph.append("[ac]%s[aout]" % ",".join(chain))
    out = os.path.join(jd, "final.mp4" if part is None else "sample.mp4")
    trim = []
    if part:
        trim = ["-ss", "%.2f" % part[0], "-to", "%.2f" % part[1]]
    run(["ffmpeg", "-v", "error", "-y"] + inputs + ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]"]
        + trim + ["-r", str(fps)] + encoder_args(cfg, final=True) + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
                                                                    "-movflags", "+faststart", out])
    if part is None:
        write_srt(caption_chunks(words, tl, cfg), os.path.join(jd, "captions.srt"))
        post = read_json(os.path.join(jd, "post.json"), {}) or {}
        thumb = post.get("thumbnail") or {}
        thumbnail(edit["source"], thumb.get("t", edit["keep"][0][0] + 3), thumb.get("text") or [], thumb.get("accent", ""),
                  os.path.join(jd, "thumbnail.jpg"), cfg)
        write_json(os.path.join(jd, "render.json"), {"duration": end_start + cfg["end_screen_seconds"], "body": end_start,
                                                     "broll_used": [b["n"] for b in brolls], "size": [w, h]})
    log("rendered", out)
    return out


# ---------------------------------------------------------------- thumbnail

def thumbnail(source, t, text_lines, accent, path, cfg):
    """Brand formula: warm cream panel, 2-4 words in serif charcoal, ONE word in honey gold, face on the right
    third, tiny logo badge. 1280x720."""
    from PIL import Image, ImageDraw
    b = cfg["brand"]
    frame = path + ".frame.jpg"
    run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % t, "-i", source, "-frames:v", "1", "-vf", "scale=-2:720", frame])
    W, H = 1280, 720
    fr = Image.open(frame).convert("RGB")
    fx = cfg["look"]["face_x"]
    cw = int(W * 0.58)
    left = int(min(max(0, fr.width * fx - cw * 0.5), fr.width - cw))
    face = fr.crop((left, 0, left + cw, H))
    im = Image.new("RGB", (W, H), "#" + b["cream"])
    im.paste(face, (W - cw, 0))
    # soft blend from the cream panel into the photo
    grad = Image.linear_gradient("L").rotate(-90).resize((140, H))  # cream on the left, photo on the right
    im.paste(Image.new("RGB", (140, H), "#" + b["cream"]), (W - cw, 0), grad)
    d = ImageDraw.Draw(im)
    lines = [no_em_dash(x) for x in (text_lines or ["Watch", "this"])][:3]
    size = 132 if max(len(x) for x in lines) <= 8 else 104 if max(len(x) for x in lines) <= 11 else 84
    f = font("serif", size)
    y = (H - len(lines) * size * 1.08) / 2 - 10
    for ln in lines:
        x = 70
        for word in ln.split(" "):
            col = "#" + (b["gold"] if accent and norm(word) in [norm(a) for a in accent.split()] else b["charcoal"])
            d.text((x, y), word, font=f, fill=col)
            x += d.textlength(word + " ", font=f)
        y += size * 1.08
    logo = Image.open(os.path.join(ASSETS, "logo.png")).convert("RGBA")
    bb = logo.getchannel("A").point(lambda v: 255 if v > 200 else 0).getbbox()
    logo = logo.crop(bb) if bb else logo
    logo = logo.resize((int(logo.width * 96 / logo.height), 96))
    im.paste(logo, (64, H - 96 - 40), logo)
    im.save(path, quality=92)
    os.remove(frame)


def chapters_text(post, tl):
    """Exact chapter times from the edit (never estimated): first at 00:00, MM:SS, hyphens only."""
    ch = sorted(post.get("chapters", []), key=lambda c: c["t"])
    out, last = [], -99
    for i, c in enumerate(ch):
        t = 0.0 if i == 0 else tl.out(c["t"])
        if t - last < 10 and i:
            continue  # YouTube needs chapters at least 10 s apart
        out.append("%s %s" % (mmss(t), no_em_dash(c["title"])))
        last = t
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["analyze"] and len(a) >= 2:
        ep = int(a[a.index("--episode") + 1]) if "--episode" in a else None
        analyze(a[1], episode=ep)
    elif a[:1] == ["render"] and len(a) == 2:
        render(a[1])
    elif a[:1] == ["sample"] and len(a) == 4:
        render(a[1], part=(float(a[2]), float(a[3])))
    else:
        print(__doc__)
