"""Vertical Shorts (1080x1920) cut from an episode, in the HoneyBadger & Bees look.

Each episode gets 2 Shorts (talk around 5 min) or 3 (around 10 min). The editor picks them and writes them into
edit.json "shorts" (see EDITOR.md):

  {"n": 1, "title": "Fine-tuning made you an AI producer", "kicker": "ISO 42001",
   "ranges": [[31.2, 51.84], [143.9, 182.3]],            # SOURCE seconds, in play order, cut on word edges
   "fixes": [["sleep past", "slip past"]],              # optional caption fixes for this Short only
   "description": "...", "hashtags": ["#ISO42001", "#Shorts"]}

The look: his face cropped to 9:16 with a punch-in at every join and a slow push, a navy title card with a gold edge
at the top, big word-by-word captions (the word being said turns gold), a thin gold progress bar, and a short end
tag ("Full episode on the channel"). Voice gets the same clean-up and loudness as the episode, with the channel's own
music bed under it.
"""
import os
import subprocess
import sys
import time

import numpy as np
import cv2
from PIL import Image, ImageDraw

PIPE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, PIPE)
from gfx import NAVY, CREAM, GOLD, WHITE, font, text, rrect, blend, fill, logo, ease_out, ramp, to_np  # noqa: E402
import motion  # noqa: E402

OW, OH = 1080, 1920
TAIL = 2.4            # seconds of end tag after the last word
BASE = 1.0            # zoom of the plain clips (1 = full camera height)
PUNCH = 1.08          # zoom of every second clip
PUSH = 0.025          # slow push over a clip
TITLE_Y = 30          # title card top
HEAD_GAP = 16         # free space between the title card and his hair
LOW_GAP = 24          # free space between his chin and the captions
CAP_MAX = 1470        # lowest caption centre: YouTube's Shorts title, description and buttons cover the area below
KEYLINE_CARDS = False # cream key-line cards under his chin: off (Mykhailo, 27 Nov 2026: "delete these messages")
FACE_MODEL = os.path.join(PIPE, "assets", "models", "face_yunet.onnx")  # OpenCV Zoo YuNet (Apache-2.0)

# Layout rule (Mykhailo, 20 Nov 2026): nothing may cover his face. The face is found in every clip, the picture is
# placed so his hair starts under the title card, and the key-line cards and captions sit below his chin.
# Captions (27 Nov 2026): one fixed level per Short, as high as the chin allows, never in the bottom strip
# that YouTube covers. Every range must start at a sentence start and end at a sentence end (check_ranges).


def _tok(s):
    return motion._tok(s)


def short_words(words, ranges, fixes=()):
    """Words spoken inside the Short's ranges, in Short time (seconds from the Short's start)."""
    import editor
    words = editor.apply_fixes(words, list(fixes))
    out, t0 = [], 0.0
    for a, b in ranges:
        for w in words:
            mid = (w["s"] + w["e"]) / 2
            if a <= mid <= b:
                out.append({"w": editor.no_em_dash(w["w"]), "s": t0 + max(w["s"], a) - a, "e": t0 + min(w["e"], b) - a})
        t0 += b - a
    return out


def apply_all(words, cfg, edit, short):
    import editor
    return editor.apply_fixes([dict(w) for w in words], cfg.get("fixes", []) + edit.get("fixes", []) + short.get("fixes", []))


def chunks(ws, max_words=3, max_chars=16):
    """Caption groups of 1-3 words: a new group at a sentence end, a comma, or a pause."""
    out, cur = [], []
    for i, w in enumerate(ws):
        cur.append(w)
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        txt = " ".join(x["w"] for x in cur)
        if (not nxt or len(cur) >= max_words or len(txt) + 1 + len(nxt["w"]) > max_chars
                or w["w"].endswith((".", "?", "!", ",", ":")) or nxt["s"] - w["e"] > 0.35):
            out.append({"words": cur, "s": cur[0]["s"], "e": cur[-1]["e"]})
            cur = []
    for a, b in zip(out, out[1:]):
        a["e"] = b["s"] if b["s"] - a["e"] < 0.6 else a["e"] + 0.25
    if out:
        out[-1]["e"] += 0.4
    return out


_CAP = {}


def caption_sprite(words, hot, size=88):
    """One caption group, white with a dark outline; word index `hot` in gold. Wraps to two lines if needed."""
    key = (tuple(w["w"] for w in words), hot, size)
    if key in _CAP:
        return _CAP[key]
    f = font("sans", size, 800)
    toks = [w["w"].upper().strip() for w in words]
    space = f.getlength(" ")
    lines, cur, wid = [], [], 0
    for i, t in enumerate(toks):
        tw = f.getlength(t)
        if cur and wid + space + tw > OW - 140:
            lines.append(cur)
            cur, wid = [], 0
        wid += (space if cur else 0) + tw
        cur.append(i)
    lines.append(cur)
    asc, desc = f.getmetrics()
    lh = int(size * 1.08)
    widths = [sum(f.getlength(toks[i]) for i in ln) + space * (len(ln) - 1) for ln in lines]
    pad = 18
    img = Image.new("RGBA", (int(max(widths)) + pad * 2, lh * (len(lines) - 1) + asc + desc + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for li, ln in enumerate(lines):
        x = pad + (max(widths) - widths[li]) / 2
        y = pad + li * lh
        for i in ln:
            col = GOLD if i == hot else WHITE
            d.text((x, y), toks[i], font=f, fill=col + (255,), stroke_width=7, stroke_fill=(8, 14, 26, 255))
            x += f.getlength(toks[i]) + space
    spr = to_np(img)
    _CAP[key] = spr
    return spr


_TITLE = {}


def title_card(title, kicker):
    key = (title, kicker)
    if key in _TITLE:
        return _TITLE[key]
    size, max_w = 50, 900
    n_lines = text(title, "serif", size, 600, CREAM, max_w=max_w).shape[0]
    while max_w > 400 and text(title, "serif", size, 600, CREAM, max_w=max_w - 20).shape[0] == n_lines:
        max_w -= 20  # balanced lines: the narrowest width that keeps the same number of lines
    t = text(title, "serif", size, 600, CREAM, max_w=max_w, lh=1.1, align="center")
    k = text(kicker.upper(), "sans", 30, 700, GOLD, track=3) if kicker else None
    w = max(t.shape[1], k.shape[1] if k is not None else 0) + 90
    h = t.shape[0] + (k.shape[0] + 8 if k is not None else 0) + 44
    card = rrect(w, h, 28, NAVY, 236, stroke=GOLD, sw=4, shadow=14).copy()
    pad = (card.shape[1] - w) // 2
    y = pad + 22
    if k is not None:
        blend_rgba(card, k, (card.shape[1] - k.shape[1]) // 2, y)
        y += k.shape[0] + 8
    blend_rgba(card, t, (card.shape[1] - t.shape[1]) // 2, y)
    _TITLE[key] = card
    return card


def blend_rgba(dst, src, x, y):
    """Paste RGBA sprite onto an RGBA sprite (in place)."""
    h, w = src.shape[:2]
    roi = dst[y:y + h, x:x + w].astype(np.float32)
    a = src[..., 3:4].astype(np.float32) / 255.0
    roi[..., :3] = src[..., :3] * a + roi[..., :3] * (1 - a)
    roi[..., 3:4] = np.maximum(roi[..., 3:4], src[..., 3:4])
    dst[y:y + h, x:x + w] = roi.astype(np.uint8)


_FD = []


def face_detector():
    if not _FD:
        d = None
        try:
            if os.path.exists(FACE_MODEL) and hasattr(cv2, "FaceDetectorYN"):
                d = cv2.FaceDetectorYN.create(FACE_MODEL, "", (320, 320), 0.6)
        except Exception:
            d = None
        _FD.append(d)
    return _FD[0]


def grab(source, t, w=960):
    """One frame (RGB, w wide) at source second t."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % max(0.0, t), "-i", source, "-frames:v", "1",
                        "-vf", "scale=%d:-2" % w, "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    if not p.stdout:
        return None
    return cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)


def head_box(source, ts, SW, SH):
    """His head (hair to chin) in source pixels over the moments ts: {cx, top, bottom}, or None if not found."""
    d = face_detector()
    if d is None:
        return None
    xs, tops, bots = [], [], []
    for t in ts:
        img = grab(source, t)
        if img is None:
            continue
        d.setInputSize((img.shape[1], img.shape[0]))
        _, faces = d.detect(img)
        if faces is None or not len(faces):
            continue
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])[:4]
        k = SW / img.shape[1]
        xs.append((x + w / 2) * k)
        tops.append((y - 0.22 * h) * k)   # the box starts at the hairline: hair adds about a fifth
        bots.append((y + 1.04 * h) * k)   # and ends at the chin
    if not xs:
        return None
    return {"cx": float(np.median(xs)), "top": float(min(tops)), "bottom": float(max(bots))}


def plan_layout(source, clips, fps, cfg, SW, SH, title_h, pop_h=0, log=print):
    """Per clip: where his head is. For the Short: where the cards and captions go (below the lowest chin)."""
    boxes = []
    for c in clips:
        d = c["frames"] / fps
        boxes.append(head_box(source, [c["src_in"] + d * f for f in (0.15, 0.5, 0.85)], SW, SH))
    found = [b for b in boxes if b]
    if found:
        fb = {k: float(np.median([b[k] for b in found])) for k in ("cx", "top", "bottom")}
    else:
        log("  shorts: face not found, using look.face_x/face_y")
        cy = cfg["look"].get("face_y", 0.4) * SH
        fb = {"cx": cfg["look"].get("face_x", 0.6) * SW, "top": cy - 0.22 * SH, "bottom": cy + 0.24 * SH}
    head_top = TITLE_Y + title_h + HEAD_GAP
    low = 0
    for c, b in zip(clips, boxes):
        c["head"] = b or fb
        zmax = c["level"] * (1 + PUSH)
        low = max(low, head_top + (c["head"]["bottom"] - c["head"]["top"]) * OH * zmax / SH)
    pop_y = int(low + LOW_GAP)
    if pop_h:
        cap_y = int(min(pop_y + max(pop_h - 14, 0) + 125, OH - 150))
    else:
        cap_y = int(min(pop_y + 72, CAP_MAX))
    return {"head_top": head_top, "pop_y": pop_y, "cap_y": cap_y}


def crop_vertical(src, z, head, head_top):
    """9:16 window from a 16:9 frame, z = zoom (1 = full height), his hair placed at output row head_top.
    Above the camera picture (when his head sits high) the top row is stretched: it stays under the title card."""
    SH, SW = src.shape[:2]
    ch = SH / z
    cw = ch * OW / OH
    a = ch / OH
    x0 = min(max(head["cx"] - cw / 2, 0), SW - cw)
    y0 = min(head["top"] - head_top * a, SH - ch)
    M = np.float32([[a, 0, x0], [0, a, y0]])
    return cv2.warpAffine(src, M, (OW, OH), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


_POP = {}


def pop_sprite(txt, accent=()):
    key = (txt, tuple(accent))
    if key not in _POP:
        f = font("serif", 54, 600)
        lines, cur = [], ""
        for w in txt.split():
            if cur and f.getlength(cur + " " + w) > 860:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
        if len(lines) == 2 and f.getlength(lines[1]) < f.getlength(lines[0]) * 0.5:  # balance two lines
            ws_ = txt.split()
            best = min(range(1, len(ws_)), key=lambda k: abs(f.getlength(" ".join(ws_[:k])) - f.getlength(" ".join(ws_[k:]))))
            lines = [" ".join(ws_[:best]), " ".join(ws_[best:])]
        sprs = [motion.rich(l, accent=tuple(accent), family="serif", size=54, wght=600, col=NAVY, acc=GOLD) for l in lines]
        tw = max(x.shape[1] for x in sprs)
        th = sum(x.shape[0] for x in sprs) - 6 * (len(sprs) - 1)
        card = rrect(tw + 80, th + 50, 26, CREAM, 248, stroke=GOLD, sw=4, shadow=14).copy()
        pad = (card.shape[1] - (tw + 80)) // 2
        y = pad + 25
        for x in sprs:
            blend_rgba(card, x, (card.shape[1] - x.shape[1]) // 2, y)
            y += x.shape[0] - 6
        _POP[key] = card
    return _POP[key]


END = (".", "?", "!")


def check_ranges(words, ranges):
    """Cuts in the middle of a sentence look bad (Mykhailo, 27 Nov 2026). Each range must start where a sentence
    starts (the word before it ends a sentence) and end on a word that ends one. Returns the problems found."""
    out = []
    for a, b in ranges:
        inside = [w for w in words if w["s"] >= a - 0.05 and w["e"] <= b + 0.05]
        before = [w for w in words if w["e"] <= a - 0.05]
        if not inside:
            out.append("range %.2f-%.2f has no words" % (a, b))
            continue
        if before and not before[-1]["w"].rstrip('"\')').endswith(END) and not inside[0]["w"][:1].isupper():
            out.append("range %.2f-%.2f starts mid-sentence at '%s'" % (a, b, inside[0]["w"]))
        if not inside[-1]["w"].rstrip('"\')').endswith(END):
            out.append("range %.2f-%.2f ends mid-sentence at '%s'" % (a, b, inside[-1]["w"]))
    return out


def short_time(ranges, src):
    """Source second -> Short second (None if that moment isn't in the Short)."""
    t0 = 0.0
    for a, b in ranges:
        if a - 0.05 <= src <= b + 0.05:
            return t0 + min(max(src, a), b) - a
        t0 += b - a
    return None


def short_pops(short, words, ranges):
    """short["pops"]: [{"at": source second or phrase, "until": ..., "text": "...", "accent": [...]}] -> Short times."""
    out = []
    ws = short_words(words, ranges)
    for p in short.get("pops", []):
        def when(spec, default, end=False):
            if spec is None:
                return default
            if isinstance(spec, (int, float)):
                return short_time(ranges, float(spec))
            toks = _tok(spec)
            for i in range(len(ws) - len(toks) + 1):
                if [_tok(ws[i + k]["w"])[0] if _tok(ws[i + k]["w"]) else "" for k in range(len(toks))] == toks:
                    return ws[i + len(toks) - 1]["e"] if end else ws[i]["s"]
            return default
        s0 = when(p.get("at"), None)
        if s0 is None:
            continue
        s1 = when(p.get("until"), s0 + 3.0, end=True) or s0 + 3.0
        out.append({"s": s0, "e": max(s1, s0 + 1.5), "text": p["text"], "accent": p.get("accent", [])})
    return out


def draw(frame, t, dur, voice_end, title, kicker, groups, pops=(), lay=None):
    lay = lay or {"pop_y": 1290, "cap_y": 1560}
    # title card: drops in, stays
    tc = title_card(title, kicker)
    p = ease_out(ramp(t, 0.1, 0.5))
    blend(frame, tc, OW / 2, TITLE_Y - 40 * (1 - p), p, anchor="tc")
    # key-line cards under his chin, captions under them: never over his face
    for p in pops:
        if p["s"] <= t < p["e"]:
            a = ease_out(ramp(t, p["s"], 0.35)) * (1 - ramp(t, p["e"] - 0.25, 0.25))
            blend(frame, pop_sprite(p["text"], p["accent"]), OW / 2, lay["pop_y"] + 30 * (1 - a), a,
                  scale=0.92 + 0.08 * a, anchor="tc")
    # captions
    for g in groups:
        if g["s"] - 0.02 <= t < g["e"] and t < voice_end:
            hot = 0
            for i, w in enumerate(g["words"]):
                if t >= w["s"] - 0.03:
                    hot = i
            sc = 0.86 + 0.14 * ease_out(ramp(t, g["s"] - 0.02, 0.12))
            blend(frame, caption_sprite(g["words"], hot), OW / 2, lay["cap_y"], 1.0, scale=sc, anchor="c")
            break
    # progress bar
    fill(frame, 0, OH - 14, OW, 14, NAVY, 0.55)
    fill(frame, 0, OH - 14, OW * min(1.0, t / dur), 14, GOLD, 1.0)
    # end tag
    if t >= voice_end - 0.2:
        e = ease_out(ramp(t, voice_end - 0.2, 0.45))
        fill(frame, 0, 0, OW, OH, NAVY, 0.55 * e)
        card = rrect(520, 420, 36, CREAM, 255, stroke=GOLD, sw=5, shadow=16)
        blend(frame, card, OW / 2, 560 + 30 * (1 - e), e, anchor="tc")
        blend(frame, logo("full", 330), OW / 2, 600 + 30 * (1 - e), e, anchor="tc")
        tg = text("Full episode on the channel", "sans", 54, 700, CREAM)
        blend(frame, tg, OW / 2, 1010, e, anchor="tc")
        sub = text("Enterprise compliance, without the enterprise headache.", "sans", 34, 500, GOLD, max_w=900,
                   align="center")
        blend(frame, sub, OW / 2, 1100, e, anchor="tc")


def render_short(edit, words, cfg, short, out, log=print, stills=None):
    """One Short -> out (mp4, 1080x1920). stills = list of Short seconds -> jpgs in out (a folder) instead."""
    fps = int(round(cfg["output"].get("fps") or edit["info"]["fps"]))
    clips, total = motion.quantise(short["ranges"], fps)
    for i, c in enumerate(clips):
        c["level"] = BASE if i % 2 == 0 else PUNCH
    voice_end = total / fps
    dur = voice_end + TAIL
    ws = short_words(words, [[c["src_in"], c["src_out"]] for c in clips],
                     cfg.get("fixes", []) + edit.get("fixes", []) + short.get("fixes", []))
    groups = chunks(ws)
    pops = short_pops(short, apply_all(words, cfg, edit, short), [[c["src_in"], c["src_out"]] for c in clips]) \
        if KEYLINE_CARDS else []
    for warn in check_ranges(words, short["ranges"]):
        log("  WARNING short %s: %s" % (short.get("n"), warn))
    title, kicker = short.get("title", ""), short.get("kicker", "")
    SW, SH = edit["info"]["width"], edit["info"]["height"]
    pop_h = max([pop_sprite(p["text"], p["accent"]).shape[0] for p in pops] or [0])
    lay = plan_layout(edit["source"], clips, fps, cfg, SW, SH, title_card(title, kicker).shape[0] - 28, pop_h, log)
    log("  layout %s" % lay)
    hw = sys.platform == "darwin"
    still_k = {round(s * fps) for s in (stills or [])}
    enc = None
    if not stills:
        voice = out + ".voice.wav"
        motion.build_voice(edit["source"], clips, fps, voice, cfg, TAIL + 0.5)
        motion.mix_music(voice, voice_end, TAIL, cfg)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                "-s", "%dx%d" % (OW, OH), "-r", str(fps), "-i", "-", "-i", voice, "-t", "%.3f" % dur,
                                "-vf", "scale=out_color_matrix=bt709:out_range=tv"] + motion.encoder(cfg) +
                               ["-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
                                "-color_trc", "bt709", "-c:a", "aac", "-b:a", "256k", "-shortest",
                                "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    started, k, last = time.time(), 0, None
    for c in clips:
        n = c["frames"]
        frames = range(k, k + n)
        if stills and not any(f in still_k for f in frames):
            k += n
            continue
        for i, src in enumerate(motion.decode(edit["source"], c, (SW, SH), hw)):
            if stills and (k + i) not in still_k:
                continue
            t = (k + i) / fps
            z = c["level"] * (1.0 + PUSH * i / max(1, n - 1))
            frame = crop_vertical(src, z, c["head"], lay["head_top"])
            last = (src, z, c["head"])
            draw(frame, t, dur, voice_end, title, kicker, groups, pops, lay)
            if stills:
                cv2.imwrite(os.path.join(out, "short_%05.2f.jpg" % t), frame[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 88])
            else:
                enc.stdin.write(frame.tobytes())
        k += n
    # end tag over the last picture, held
    for i in range(int(round(TAIL * fps))):
        t = voice_end + i / fps
        if stills and round(t * fps) not in still_k:
            continue
        if last is None:
            continue
        frame = crop_vertical(last[0], last[1], last[2], lay["head_top"])
        draw(frame, t, dur, voice_end, title, kicker, groups, pops, lay)
        if stills:
            cv2.imwrite(os.path.join(out, "short_%05.2f.jpg" % t), frame[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 88])
        else:
            enc.stdin.write(frame.tobytes())
    if enc:
        enc.stdin.close()
        enc.wait()
        os.remove(out + ".voice.wav")
        if enc.returncode:
            raise RuntimeError("ffmpeg encoder failed")
    log("  short %s: %.1f s in %d s" % (short.get("n", "?"), dur, time.time() - started))
    return dur


def render_all(job, log=print, only=None):
    """All Shorts of a job -> System/work/<job>/shorts/Short N.mp4. Returns the list of files."""
    import editor
    cfg = editor.load_config()
    jd = os.path.join(editor.WORK, job)
    edit = editor.read_json(os.path.join(jd, "edit.json"))
    words = editor.read_json(os.path.join(jd, "words.json"))
    od = os.path.join(jd, "shorts")
    os.makedirs(od, exist_ok=True)
    files = []
    for s in edit.get("shorts", []):
        if only and s.get("n") not in only:
            continue
        out = os.path.join(od, "Short %s.mp4" % s.get("n", len(files) + 1))
        log("render short %s: %s" % (s.get("n"), s.get("title")))
        render_short(edit, words, cfg, s, out, log=log)
        files.append(out)
    return files
