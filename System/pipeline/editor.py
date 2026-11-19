"""HBB video editor: raw talking-head footage + episode script -> finished landscape YouTube video.

  editor.py analyze <video or folder> [--episode N]   transcribe, match the script, write System/work/<job>/draft.json
  editor.py render <job>                              render System/work/<job>/edit.json (or draft.json) to final.mp4
  editor.py sample <job> <from> <to>                  render only a piece (output seconds) to check the look
  editor.py stills <job> <src sec,src sec,...>        render single frames to System/work/<job>/stills/ to check graphics

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
        # word times trimmed to the real pauses, saved, so the cut, the captions and the graphics all agree
        write_json(os.path.join(jd, "words.json"), snap_words(words, silences(source, cfg)))
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


FILLERS = {"and", "so", "um", "uh", "okay", "ok", "again", "sorry"}


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
            # he usually pauses, then restarts a little BEFORE the repeated words ("...27001. [pause] I'm Mykhailo
            # Bentsa, certified..."): drop only up to that pause, so the restart's opening words stay
            cut = hit
            gaps = [(seg_words[k]["s"] - seg_words[k - 1]["e"], k) for k in range(i + 1, hit)]
            if gaps and max(gaps)[0] >= 0.5:
                cut = max(gaps)[1]
                while cut < hit and toks[cut] in FILLERS:
                    cut += 1
            drops.append((i, cut))
            i = hit
        else:
            i += 1
    return drops


def snap_words(words, sil):
    """Whisper often stretches a word over the pause after it ("27001" lasting 1.9 s). Trim word ends (and starts)
    to the detected silences, so pauses can be cut and retake restarts are found."""
    out = []
    for w in words:
        s, e = w["s"], w["e"]
        for a, b in sil:
            if s + 0.12 < a < e:
                e = min(e, a + 0.05)
            if s < b < e - 0.12 and a <= s + 0.05:
                s = max(s, b - 0.05)
        out.append(dict(w, s=round(s, 3), e=round(max(e, s + 0.05), 3)))
    return out


def auto_edit(words, sil, sc, info, cfg):
    cut = cfg["cut"]
    words = snap_words(words, sil or [])
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
    # padding must never reach into a dropped word (the "I-" of an abandoned take)
    starts = sorted(w["s"] for w in words)
    ends = sorted(w["e"] for w in words)
    for r in keep:
        clip = [s0 for s0 in starts if r[1] - cut["pad_after"] - 0.001 < s0 < r[1]]
        if clip:
            r[1] = max(r[1] - cut["pad_after"], clip[0] - 0.02)
        clip = [e0 for e0 in ends if r[0] < e0 < r[0] + cut["pad_before"] + 0.001]
        if clip:
            r[0] = min(r[0] + cut["pad_before"], clip[-1] + 0.02)
    keep = merge_ranges([[max(0.0, round(a, 3)), round(min(b, info["duration"]), 3)] for a, b in keep])
    zooms = default_zooms(keep, rows, cfg)
    return {"keep": keep, "rows": rows, "zoom": zooms, "graphics": cue_graphics(rows, cfg),
            "broll": cue_broll(rows), "cards": cue_cards(rows)}


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


NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6}


def cue_graphics(rows, cfg):
    """A first, mechanical graphics pass from the script's Details column (the Claude editor rewrites it with phrase
    anchors and full-screen explainers; see EDITOR.md). Times are source seconds."""
    out, first = [], True
    for r in rows:
        if r.get("start") is None:
            continue
        cues, t0, t1 = r["cues"], r["start"], r["end"]
        m = re.match(r"\s*Group (?:number )?(one|two|three|four|five|six|[1-6])\b[.,:]?\s*(.*?)[.?!]", r["text"], re.I)
        if m:
            out.append({"type": "chapter", "at": t0, "num": NUM[m.group(1).lower()], "title": m.group(2).strip()[:48]})
        if re.search(r"\bI'm Mykhailo\b", r["text"]) or cues.get("lower_third") == "name":
            out.append({"type": "name", "at": round(t0 + 0.6, 2), "dur": 6.0})
        on = cues.get("onscreen") or []
        if len(on) >= 2:
            items = [{"text": re.sub(r"^\d+[.)]\s*", "", x)} for x in on]
            numbered = all(re.match(r"^\d+[.)]", x) for x in on)
            out.append({"type": "list", "at": round(t0 + 0.3, 2), "until": t1, "style": "number" if numbered else "dot",
                        "kicker": "In this video" if numbered else "", "title": "", "items": items})
        elif len(on) == 1:
            kind = "title" if first else "lower"
            out.append({"type": kind, "at": round(t0 + 0.4, 2), "dur": 7.0 if first else 6.0, "title": on[0],
                        "accent": cues.get("accent", [])})
        if re.search(r"linkedin", r["text"], re.I):
            out.append({"type": "linkedin", "at": round(t0 + 0.3, 2), "dur": 5.0})
        if re.search(r"subscribe", r["text"], re.I):
            out.append({"type": "subscribe", "at": round(t0 + 0.3, 2), "dur": 5.0})
        first = False
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

# --------------------------------------------- captions (uploaded to YouTube as a subtitle file, not burned in)

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


def broll_pass(body, brolls, tl, voice_end, fps, out, cfg):
    """Real B-roll clips (if any were dropped in) go full frame over the finished picture, voice continues."""
    import motion
    inputs = ["-i", body]
    graph, last = [], "0:v"
    for k, b in enumerate(brolls):
        t0 = tl.out(b["t"])
        dur = min(b["dur"], max(0.5, voice_end - t0 - 0.2))
        loop = ["-loop", "1"] if b["file"].lower().endswith((".jpg", ".png")) else ["-stream_loop", "-1"]
        inputs += loop + ["-t", "%.2f" % dur, "-i", b["file"]]
        graph.append("[%d:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=%s,format=yuva420p,"
                     "fade=t=in:d=0.35:alpha=1,fade=t=out:st=%.2f:d=0.35:alpha=1,setpts=PTS-STARTPTS+%.3f/TB[b%d]"
                     % (k + 1, fps, max(0.1, dur - 0.35), t0, k))
        graph.append("[%s][b%d]overlay=eof_action=pass:enable='between(t,%.3f,%.3f)'[v%d]" % (last, k, t0, t0 + dur, k))
        last = "v%d" % k
    run(["ffmpeg", "-v", "error", "-y"] + inputs + ["-filter_complex", ";".join(graph), "-map", "[%s]" % last, "-map", "0:a",
                                                    "-pix_fmt", "yuv420p"] + motion.encoder(cfg) +
        ["-c:a", "copy", "-movflags", "+faststart", out])


def render(job, part=None, log=print, stills=None):
    """edit.json (or draft.json) -> final.mp4 in the published EP.11 v3 look (see motion.py).
    part=(from, to) in output seconds renders a piece to sample.mp4; stills=[source seconds] writes stills/*.jpg."""
    import motion
    cfg = load_config()
    jd = os.path.join(WORK, job)
    edit = read_json(os.path.join(jd, "edit.json")) or read_json(os.path.join(jd, "draft.json"))
    words = apply_fixes(read_json(os.path.join(jd, "words.json")), cfg.get("fixes", []) + edit.get("fixes", []))
    if stills is not None:
        sd = os.path.join(jd, "stills")
        shutil.rmtree(sd, ignore_errors=True)
        os.makedirs(sd)
        tl0 = Timeline(edit["keep"])
        outs = [tl0.out(s) if s <= edit["keep"][-1][1] else tl0.duration + (s - edit["keep"][-1][1]) for s in stills]
        motion.render_video(edit, words, Timeline, cfg, sd, stills=outs, log=log)
        log("stills in", sd)
        return sd
    out = os.path.join(jd, "final.mp4" if part is None else "sample.mp4")
    brolls = find_broll(jd, edit)
    target = out + ".body.mp4" if brolls else out
    log("render %s: %d cuts, %d graphics, %d B-roll clips" % (job, len(edit["keep"]), len(edit.get("graphics", [])), len(brolls)))
    clips, voice_end, show, tl = motion.render_video(edit, words, Timeline, cfg, target, part=part, log=log)
    if brolls:
        fps = int(round(cfg["output"].get("fps") or edit["info"]["fps"]))
        broll_pass(target, brolls, tl, voice_end, fps, out, cfg)
        os.remove(target)
    if show.A.misses:
        log("graphics not placed: " + "; ".join(show.A.misses))
    if part is None:
        write_srt(caption_chunks(words, tl, cfg), os.path.join(jd, "captions.srt"))
        post = read_json(os.path.join(jd, "post.json"), {}) or {}
        thumb = post.get("thumbnail") or {}
        thumbnail(edit["source"], thumb.get("t", edit["keep"][0][0] + 3), thumb.get("text") or [], thumb.get("accent", ""),
                  os.path.join(jd, "thumbnail.jpg"), cfg)
        write_json(os.path.join(jd, "render.json"), {"duration": voice_end + cfg["end_screen_seconds"], "body": voice_end,
                                                     "broll_used": [b["n"] for b in brolls], "size": [1920, 1080],
                                                     "graphics_not_placed": show.A.misses})
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
    import gfx
    f = gfx.font("serif", size, 620)
    y = (H - len(lines) * size * 1.08) / 2 - 10
    for ln in lines:
        x = 70
        for word in ln.split(" "):
            col = "#" + (b["gold"] if accent and norm(word) in [norm(a) for a in accent.split()] else b["navy"])
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
    elif a[:1] == ["stills"] and len(a) == 3:
        render(a[1], stills=[float(x) for x in a[2].split(",") if x])
    else:
        print(__doc__)
