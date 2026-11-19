"""The HoneyBadger & Bees motion-graphics renderer (the look of the published EP.11 v3).

The edit decides WHAT is said (keep ranges in source seconds). This module decides how it LOOKS:
  - one locked-off talking-head shot, punch-ins at retake joins, slow push-ins, gentle zoom-out at the sign-off
  - navy lower cards with a gold edge (title, name, key lines, related/next video)
  - chapter stings with a numbered hex and a small 1-2-3 hex tracker in the top corner
  - split screen: he slides right, a cream panel with a gold edge opens on the left (lists, checklists, role cards)
  - full-screen explainers on cream "paper" with a faint honeycomb (clause quote, two-column compare, citation stack)
  - statement slam, LinkedIn card, subscribe click, brand lower third, cream logo end card

Graphics live in edit.json "graphics": a list of {"type": ..., "at": ..., "until": ...}. Every time ("at", "until",
item "at") is either a SOURCE second (number) or a short phrase he says ("clause 4.1"), which is found in the kept
words, so cues land on the word even after the cut changes. See EDITOR.md for every type and its fields.
"""
import json
import math
import os
import subprocess
import sys
import time

import numpy as np
import cv2

PIPE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, PIPE)
from gfx import (W, H, NAVY, CREAM, PAPER, GOLD, GREY, RED, WHITE, LINKEDIN, clamp, ease_out, ease_in_out, ramp,  # noqa: E402
                 font, text, runs, ink_right, rrect, hexbadge, arrow, stamp, honeycomb, blend, fill, logo, cursor, to_np)

PW = 840          # split panel width
MX = 96           # panel margin
SHIFT = 240       # how far the talking head slides right in split mode
SOFT = (214, 220, 230)
HILITE = (246, 214, 140)
ICONS = ("provider", "producer", "customer", "partner", "subject", "authority", "doc", "shield", "lock", "clock",
         "people", "money", "check", "cross", "warn")


def _tok(s):
    import re
    s = s.lower().replace("-", " ").replace(".", " ").replace("/", " ").replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9 ]+", " ", s).split()


def no_em(s):
    return (s or "").replace("—", " - ").replace("–", "-").replace("  ", " ")


# ---------------------------------------------------------------- anchors: phrase or source second -> output second

class Anchors:
    def __init__(self, words, tl):
        self.tl = tl
        self.toks = []
        for w in words:
            seg = tl.segment_of(w["s"] + 0.01)
            if seg is None:
                continue
            for t in _tok(w["w"]):
                self.toks.append((t, tl.out(w["s"]), tl.out(w["e"])))
        self.misses = []

    def at(self, spec, after=0.0, end=False, default=None):
        if spec is None or spec == "":
            return default
        if isinstance(spec, (int, float)):
            return self.tl.out(float(spec))
        if isinstance(spec, dict):
            base = self.at(spec.get("at"), after, end=spec.get("end", False), default=default)
            return None if base is None else base + float(spec.get("plus", 0))
        p = _tok(str(spec))
        if not p:
            return default
        for lo in (after - 0.01, -1):
            hit = self._find(p, lo, end)
            if hit is not None:
                return hit
        # fuzzy: the longest run of the phrase's words that he actually said
        for n in range(len(p) - 1, 0, -1):
            for k in range(0, len(p) - n + 1):
                if n == 1 and len(p[k]) < 5:
                    continue  # a single short word ("the", "ai") is too common to trust
                hit = self._find(p[k:k + n], after - 0.01, end) or self._find(p[k:k + n], -1, end)
                if hit is not None:
                    return hit
        self.misses.append(str(spec))
        return default

    def _find(self, p, lo, end):
        T = self.toks
        for i in range(len(T) - len(p) + 1):
            if T[i][1] < lo:
                continue
            if all(T[i + k][0] == p[k] for k in range(len(p))):
                return T[i + len(p) - 1][2] if end else T[i][1]
        return None


# ---------------------------------------------------------------- scenes

class Scene:
    def __init__(self, show, kind, t0, t1, draw=None, tail=None):
        self.kind, self.t0, self.t1, self.draw = kind, t0, t1, draw
        self.tail = tail if tail is not None else (0.35 if kind == "F" else 0.45)
        show.scenes.append(self)

    def env(self, t, din=0.55):
        return ease_in_out(ramp(t, self.t0, din)) * (1 - ease_in_out(ramp(t, self.t1 - self.tail, self.tail)))

    def fade(self, t):
        if self.kind == "L":
            return 1 - ramp(t, self.t1 - 0.3, 0.3)
        return 1 - ramp(t, self.t1 - self.tail - 0.25, 0.25)


def pop(t, t_in, dur=0.45):
    p = ease_out(ramp(t, t_in, dur))
    return p, (1 - p) * 26


def put(frame, spr, x, y, t, t_in, sc, a=1.0, **kw):
    al, dy = pop(t, t_in)
    blend(frame, spr, x, y + dy, al * sc.fade(t) * a, **kw)
    return al


def label(s, col=GOLD, size=22):
    return text(no_em(s).upper(), "sans", size, 600, col, track=3)


_RICH = {}


def rich(s, accent=(), family="serif", size=46, wght=600, col=CREAM, acc=GOLD, max_w=None, lh=1.12):
    """Wrapped text where the accent words are honey gold."""
    s = no_em(s)
    key = (s, tuple(accent or ()), family, size, wght, col, acc, max_w, lh)
    if key in _RICH:
        return _RICH[key]
    from PIL import Image, ImageDraw
    f = font(family, size, wght)
    acc_t = set(t for a in (accent or ()) for t in _tok(a))
    lines, cur = [], []
    for word in s.split(" "):
        trial = " ".join(cur + [word])
        if max_w and cur and f.getlength(trial) > max_w:
            lines.append(cur)
            cur = [word]
        else:
            cur.append(word)
    lines.append(cur)
    asc, desc = f.getmetrics()
    line_h = int(size * lh)
    wmax = int(max(f.getlength(" ".join(l)) for l in lines)) + 16
    img = Image.new("RGBA", (wmax, line_h * (len(lines) - 1) + asc + desc + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        x = 4
        for word in l:
            c = acc if acc_t and set(_tok(word)) & acc_t else col
            d.text((x, 4 + i * line_h), word, font=f, fill=c + (255,))
            x += f.getlength(word + " ")
    out = to_np(img)
    _RICH[key] = out
    return out


def pill_runs(text_, accent, col, acc, size=40):
    """'It doesn't hand you a role. You determine one.' with the accent part in gold, as one line."""
    text_ = no_em(text_)
    if accent and accent in text_:
        i = text_.index(accent)
        parts = tuple(p for p in ((text_[:i], col), (accent, acc), (text_[i + len(accent):], col)) if p[0])
    else:
        parts = ((text_, col),)
    return runs(parts, "serif", size, 560)


class Show:
    """All graphics of one episode, resolved to output time."""

    def __init__(self, graphics, anchors, voice_end, cfg):
        self.scenes, self.chapters = [], []
        self.A = anchors
        self.voice_end = voice_end
        self.cfg = cfg
        self.track_end = None
        self.bg = None
        gs = []
        for g in graphics or []:
            t0 = anchors.at(g.get("at"), default=None)
            if t0 is None:
                continue
            gs.append((t0, g))
        gs.sort(key=lambda x: x[0])
        # split/full scenes may not overlap: each ends before the next one starts
        panel_starts = [t for t, g in gs if g.get("type") in SPLIT + FULL]
        for t0, g in gs:
            kind = g.get("type")
            fn = getattr(self, "g_" + str(kind), None)
            if not fn:
                anchors.misses.append("unknown graphic type %r" % kind)
                continue
            dflt = DEFAULT_DUR.get(kind, 6.0)
            t1 = anchors.at(g.get("until"), after=t0, default=None)
            if t1 is None or t1 <= t0 + 0.8:
                t1 = t0 + float(g.get("dur", dflt))
            if kind in SPLIT + FULL:
                nxt = [p for p in panel_starts if p > t0 + 0.5]
                if nxt:
                    t1 = min(t1, nxt[0] - 0.3)
            t1 = min(t1, voice_end - 0.1)
            if t1 - t0 < 1.0:
                continue
            try:
                fn(g, t0, t1)
            except Exception as e:  # one bad cue must never stop the render
                anchors.misses.append("%s at %.1f s: %s" % (kind, t0, e))
        if self.chapters and self.track_end is None:
            ends = [t for t, g in gs if g.get("type") in ("linkedin", "subscribe", "brand", "next")]
            self.track_end = min([e for e in ends if e > self.chapters[-1]] or [voice_end])

    def items_at(self, items, t0, step=0.6):
        out, last = [], t0
        for i, it in enumerate(items):
            spec = it.get("at") if isinstance(it, dict) else None
            tin = self.A.at(spec, after=t0 - 1, default=None) if spec is not None else None
            if tin is None:
                tin = last + (0.4 if i == 0 else step)
            out.append(tin)
            last = tin
        return out

    # ---------------------------------------------------------- lower-third family (kind L)
    def lower(self, t0, t1, title, sub=None, kicker=None, accent=(), title_size=46, maxw=1100):
        def draw(frame, t, sc):
            ts = rich(title, accent, "serif", title_size, 560, CREAM, GOLD, max_w=maxw)
            ss = text(no_em(sub), "sans", 28, 400, SOFT, max_w=maxw) if sub else None
            ks = label(kicker) if kicker else None
            w = max(ts.shape[1], ss.shape[1] if ss is not None else 0, ks.shape[1] if ks is not None else 0) + 64
            h = ts.shape[0] + (ss.shape[0] + 6 if ss is not None else 0) + (ks.shape[0] + 4 if ks is not None else 0) + 44
            e = ease_out(ramp(t, t0, 0.5)) * sc.fade(t)
            if e <= 0:
                return
            x, top = MX, H - 110 - h
            blend(frame, rrect(w, h, 16, NAVY, 236, shadow=10), x - 20 - (1 - e) * 40, top - 20, e)
            fill(frame, x - (1 - e) * 40, top + 18, 6, h - 36, GOLD, e)
            cy = top + 20
            if ks is not None:
                put(frame, ks, x + 26, cy, t, t0 + 0.15, sc)
                cy += ks.shape[0] + 4
            put(frame, ts, x + 24, cy, t, t0 + 0.2, sc)
            cy += ts.shape[0] + 6
            if ss is not None:
                put(frame, ss, x + 26, cy, t, t0 + 0.35, sc)
        Scene(self, "L", t0, t1, draw)

    def g_lower(self, g, t0, t1):
        size = 46 if len(g.get("title", "")) > 34 else 52
        self.lower(t0, t1, g.get("title", ""), g.get("sub"), g.get("kicker"), g.get("accent", ()), title_size=size)

    def g_title(self, g, t0, t1):
        self.lower(t0, t1, g.get("title", ""), g.get("sub"), g.get("kicker"), g.get("accent", ()), title_size=58)

    def g_name(self, g, t0, t1):
        b = self.cfg["brand"]
        self.lower(t0, t1, g.get("title", b.get("name", "Mykhailo Bentsa")),
                   g.get("sub", b.get("role_line", "Lead Auditor · ISO 27001 & ISO 42001")),
                   g.get("kicker", b.get("company", "HoneyBadger & Bees")))

    def g_related(self, g, t0, t1):
        self.lower(t0, t1, g.get("title", ""), g.get("sub", "Watch the full breakdown · link below"),
                   g.get("kicker", "Related video"))

    def g_next(self, g, t0, t1):
        self.lower(t0, t1, g.get("title", ""), g.get("sub", "Watch it next · link below"), g.get("kicker", "Next video"),
                   title_size=52)

    def g_warning(self, g, t0, t1):
        def draw(frame, t, sc):
            e = ease_out(ramp(t, t0, 0.45)) * sc.fade(t)
            if e <= 0:
                return
            ts = rich(g.get("title", ""), g.get("accent", ()), "serif", 44, 600, CREAM, GOLD, max_w=1000)
            w = ts.shape[1] + 180
            h = max(130, ts.shape[0] + 70)
            blend(frame, rrect(w, h, 18, NAVY, 236, shadow=10), MX - 20, H - 130 - h, e)
            blend(frame, hexbadge(40, GOLD, icon=g.get("icon", "warn"), icon_col=NAVY), MX + 50, H - 130 - h / 2 + 10, e,
                  anchor="c")
            blend(frame, label(g.get("kicker", "One warning"), GOLD, 22), MX + 120, H - 112 - h, e)
            blend(frame, ts, MX + 116, H - 80 - h, e)
        Scene(self, "L", t0, t1, draw)

    def g_statement(self, g, t0, t1):
        lines = g.get("lines") or [{"text": g.get("title", ""), "accent": g.get("accent", [])}]
        tins = self.items_at(lines, t0, 0.8)
        tins[0] = max(tins[0], t0 + 0.2)

        def draw(frame, t, sc):
            e = ease_out(ramp(t, t0, 0.4)) * sc.fade(t)
            if e <= 0:
                return
            sprites = [rich(l.get("text", ""), l.get("accent", ()), "serif", 64, 600, CREAM, GOLD, max_w=1500) for l in lines]
            w = max(s.shape[1] for s in sprites) + 90
            h = sum(s.shape[0] for s in sprites) + 50
            y = H - 250
            blend(frame, rrect(w, h, 18, NAVY, 236, shadow=12), W / 2, y + h / 2, e, anchor="c")
            cy = y + 25
            for tin, s in zip(tins, sprites):
                put(frame, s, W / 2, cy, t, tin, sc, anchor="tc")
                cy += s.shape[0]
        Scene(self, "L", t0, t1, draw)

    def g_chapter(self, g, t0, t1):
        num = int(g.get("num", len(self.chapters) + 1))
        title = no_em(g.get("title", ""))
        self.chapters.append(t0)
        self.chapter_nums = getattr(self, "chapter_nums", []) + [num]
        self.chapter_total = max(getattr(self, "chapter_total", 0), int(g.get("of", 0) or 0))

        def draw(frame, t, sc):
            e = ease_out(ramp(t, t0, 0.5)) * sc.fade(t)
            if e <= 0:
                return
            blend(frame, hexbadge(58, GOLD, label="%02d" % num, label_col=NAVY, label_size=40), MX + 54, H - 170, e,
                  scale=0.6 + 0.4 * e, anchor="c")
            ks = label(g.get("kicker", "Group %d" % num), CREAM, 24)
            ts = text(title, "serif", 60, 600, CREAM)
            blend(frame, rrect(ts.shape[1] + 70, 140, 16, NAVY, 236, shadow=10), MX + 90, H - 260, e, reveal=e)
            put(frame, ks, MX + 150, H - 222, t, t0 + 0.2, sc)
            put(frame, ts, MX + 146, H - 190, t, t0 + 0.3, sc)
        Scene(self, "L", t0, t0 + 3.6, draw)

    def draw_tracker(self, frame, t):
        n = max(len(self.chapters), getattr(self, "chapter_total", 0))
        if not self.chapters or t < self.chapters[0] or t > self.track_end + 0.6:
            return
        a = ease_out(ramp(t, self.chapters[0], 0.6)) * (1 - ramp(t, self.track_end, 0.5))
        cur = max(i for i, c in enumerate(self.chapters) if t >= c) + 1
        cur = int(self.chapter_nums[cur - 1]) if cur - 1 < len(self.chapter_nums) else cur
        for i in range(n):
            on = (i + 1) == cur
            x = W - 70 - (n - 1 - i) * 62
            blend(frame, hexbadge(26, GOLD if on else NAVY, label=str(i + 1), label_col=NAVY if on else CREAM,
                                  label_size=22, alpha=255 if on else 215), x, 66, a, anchor="c", scale=1.15 if on else 1.0)

    def g_linkedin(self, g, t0, t1):
        def draw(frame, t, sc):
            e = ease_out(ramp(t, t0, 0.45)) * sc.fade(t)
            if e <= 0:
                return
            ts = text(no_em(g.get("title", "DM me on LinkedIn")), "serif", 50, 600, CREAM)
            ch = 124
            cw = 40 + 84 + 28 + ts.shape[1] + 36
            x0, y0 = MX - 20 - (1 - e) * 40, H - 120 - ch
            blend(frame, rrect(cw, ch, 18, NAVY, 236, shadow=10), x0 - 20, y0 - 20, e)
            blend(frame, rrect(84, 84, 16, LINKEDIN), x0 + 40, y0 + (ch - 84) / 2, e)
            blend(frame, text("in", "sans", 58, 700, WHITE), x0 + 82, y0 + ch / 2 + 2, e, anchor="c")
            blend(frame, ts, x0 + 152, y0 + ch / 2, e, anchor="lc")
        Scene(self, "L", t0, t1, draw)

    def g_brand(self, g, t0, t1):
        b = self.cfg["brand"]

        def draw(frame, t, sc):
            e = ease_out(ramp(t, t0 + 0.1, 0.5)) * sc.fade(t)
            if e <= 0:
                return
            blend(frame, rrect(1080, 200, 18, CREAM, 245, shadow=12), MX - 20, H - 330, e, reveal=e)
            blend(frame, logo("icon", 120), MX + 6, H - 290, e)
            blend(frame, text("HoneyBadger & Bees", "serif", 54, 700, NAVY), MX + 240, H - 300, e)
            blend(frame, label("Technologies", GREY, 20), MX + 244, H - 232, e)
            blend(frame, runs(((b.get("tagline_a", "Enterprise compliance, "), NAVY),
                               (b.get("tagline_b", "without the enterprise headache."), GOLD)), "sans", 28, 500),
                  MX + 240, H - 196, e)
        Scene(self, "L", t0, t1, draw)

    def g_subscribe(self, g, t0, t1):
        sub = no_em(g.get("text", "for practical, no-headache compliance"))

        def draw(frame, t, sc):
            e = ease_out(ramp(t, t0, 0.4)) * sc.fade(t)
            if e <= 0:
                return
            ts = text(sub, "sans", 32, 500, CREAM)
            bw, bh, ch = 300, 80, 128
            cw = 30 + bw + 30 + ts.shape[1] + 40
            x0, y0 = MX - 20, H - 120 - ch
            cyc = y0 + ch / 2
            blend(frame, rrect(cw, ch, 18, NAVY, 236, shadow=10), x0 - 20, y0 - 20, e)
            t_click = t0 + 1.3
            press = 1 - 0.08 * math.sin(math.pi * clamp((t - t_click) / 0.25))
            pulse = 1 + 0.04 * math.sin(max(0.0, t - t_click - 0.4) * 5.0) * (t > t_click + 0.4)
            bs = (0.7 + 0.3 * e) * press * pulse
            bx = x0 + 30 + bw / 2
            clicked = t >= t_click + 0.12
            blend(frame, rrect(bw, bh, bh // 2, (150, 40, 32) if clicked else RED, shadow=8), bx, cyc, e, scale=bs, anchor="c")
            blend(frame, text("Subscribed" if clicked else "Subscribe", "sans", 38, 700, WHITE), bx, cyc + 1, e, scale=bs,
                  anchor="c")
            rp = clamp((t - t_click) / 0.6)
            if 0 < rp < 1:
                blend(frame, rrect(bw, bh, bh // 2, None, stroke=WHITE, sw=4), bx, cyc, (1 - rp) * e, scale=1 + 0.35 * rp,
                      anchor="c")
            blend(frame, ts, x0 + 30 + bw + 30, cyc, e, anchor="lc")
            mv = ease_in_out(ramp(t, t0 + 0.4, 0.8))
            ca = e * (1 - ramp(t, t_click + 1.4, 0.4))
            blend(frame, cursor(46), bx + 260 * (1 - mv) + 8, cyc + 140 * (1 - mv) - 10, ca,
                  scale=0.9 if abs(t - t_click - 0.1) < 0.12 else 1.0)
        Scene(self, "L", t0, t1, draw)

    # ---------------------------------------------------------- split panel (kind S)
    def panel_title(self, frame, t, sc, t_in, kicker, title, y=130, size=60):
        if kicker:
            put(frame, label(kicker), MX, y, t, t_in, sc)
        ts = rich(title, (), "serif", size, 600, NAVY, GOLD, max_w=PW - 2 * MX, lh=1.08)
        put(frame, ts, MX - 4, y + 40, t, t_in + 0.1, sc)
        return y + 40 + ts.shape[0]

    def extras(self, frame, t, sc, y, g, t0):
        """note (gold-edged serif line), tag (gold pill), stamp (red stamp) under a panel's content."""
        for key in ("note", "tag", "stamp"):
            x = g.get(key)
            if not x:
                continue
            x = x if isinstance(x, dict) else {"text": x}
            tin = self.A.at(x.get("at"), after=t0, default=t0 + 1.2)
            if key == "note":
                ns = text(no_em(x["text"]), "serif", 40, 500, NAVY, max_w=PW - 2 * MX - 40)
                al, dy = pop(t, tin)
                fill(frame, MX, y + 6 + dy, 5, ns.shape[0] - 8, GOLD, al * sc.fade(t))
                put(frame, ns, MX + 22, y, t, tin, sc)
                y += ns.shape[0] + 18
            elif key == "tag":
                tg = text(no_em(x["text"]), "sans", 30, 600, NAVY, max_w=PW - 2 * MX - 40, align="center")
                inner = int((tg.shape[1] - 16) / 1.06)  # real ink width: text() pads its sprite on the right
                al, dy = pop(t, tin)
                pw, ph = inner + 56, tg.shape[0] + 16
                blend(frame, rrect(pw, ph, 26, GOLD), MX, y + dy, al * sc.fade(t))
                blend(frame, tg, MX + pw / 2, y + ph / 2 + dy, al * sc.fade(t), anchor="c")
                y += tg.shape[0] + 40
            else:
                p = ease_out(ramp(t, tin, 0.4))
                if p > 0:
                    blend(frame, stamp(no_em(x["text"]), RED, 40, angle=-5), PW / 2, min(y + 50, H - 110),
                          p * sc.fade(t), scale=1.5 - 0.5 * p, anchor="c")
                y += 110
        return y

    def g_list(self, g, t0, t1):
        """Agenda, checklist, role list, decision rows. style: number | check | icon | dot | verdict."""
        items = [it if isinstance(it, dict) else {"text": it} for it in g.get("items", [])]
        style = g.get("style", "dot")
        tins = self.items_at(items, t0)
        dims = [self.A.at(it.get("dim_at"), after=t0, default=None) if it.get("dim_at") else None for it in items]
        has_sub = any(it.get("sub") for it in items) or style == "verdict"
        n = max(1, len(items))
        room = 930 - 330 - (110 if any(g.get(k) for k in ("note", "tag", "stamp")) else 0)
        base = {"verdict": 170, "number": 150, "check": 92, "dot": 80}.get(style, 130 if has_sub else 98)
        # short lists spread out to use the panel's height (then the whole panel is centred, see panel_offset)
        step = min((base + (32 if has_sub and style in ("number", "check", "dot") else 0)) * 1.7, room / n)
        big = 46 if style == "verdict" or n <= 3 else 42 if n <= 5 else 36

        def draw(frame, t, sc):
            yt = self.panel_title(frame, t, sc, t0 + 0.2, g.get("kicker"), g.get("title", ""))
            y0 = max(yt + 50, 330)
            for i, it in enumerate(items):
                y = y0 + i * step
                al, dy = pop(t, tins[i] - 0.1)
                dk = ease_out(ramp(t, dims[i], 0.5)) if dims[i] else 0
                a = al * sc.fade(t) * (1 - 0.6 * dk)
                if a <= 0:
                    continue
                r = 30 if style == "check" else 26 if style == "dot" else 44
                ic = it.get("icon") if it.get("icon") in ICONS else None
                if style == "number":
                    hb = hexbadge(44, NAVY, label=str(i + 1), label_col=GOLD, label_size=38)
                elif style == "check":
                    hb = hexbadge(30, GOLD, icon="check", icon_col=NAVY)
                elif style == "dot":
                    hb = hexbadge(18, GOLD)
                else:
                    hb = hexbadge(44, GREY if dk else NAVY, icon=ic or "check", icon_col=CREAM if dk else GOLD)
                cx = MX + (r if style != "dot" else 18)
                blend(frame, hb, cx, y + 30 + dy, a, anchor="c", scale=0.6 + 0.4 * al)
                tx = MX + (2 * r + 30 if style != "dot" else 54)
                ts = rich(it.get("text", ""), it.get("accent", ()), "serif", big, 560, NAVY, GOLD, max_w=PW - MX - tx)
                blend(frame, ts, tx, y + dy - (8 if style in ("number", "icon", "verdict") else 0), a)
                if it.get("sub") and style != "verdict":
                    blend(frame, text(no_em(it["sub"]), "sans", 26, 400, GREY, max_w=PW - MX - tx), tx + 2,
                          y + dy - 8 + ts.shape[0], a)
                if style == "verdict" and it.get("verdict"):
                    pv = ease_out(ramp(t, tins[i] + 0.6, 0.5))
                    gold = it.get("tone", "gold") != "grey"
                    blend(frame, arrow(46, NAVY, 4, 12), tx + 2, y + 66 + dy, a * pv)
                    vs = text(no_em(it["verdict"]), "sans", 34, 600, NAVY if gold else CREAM)
                    blend(frame, rrect(vs.shape[1] + 40, 60, 30, GOLD if gold else GREY), tx + 68, y + 50 + dy, a * pv)
                    blend(frame, vs, tx + 88, y + 54 + dy, a * pv)
                if dk and it.get("dim_note"):
                    put(frame, text(no_em(it["dim_note"]), "sans", 24, 500, GREY), tx + 2, y + 48, t, dims[i] + 0.2, sc)
            self.extras(frame, t, sc, y0 + n * step + 10, g, t0)
        Scene(self, "S", t0, t1, draw)

    def g_card(self, g, t0, t1):
        """Definition card: hex icon, small grey source line, serif name, definition, optional gold underline."""
        ic = g.get("icon") if g.get("icon") in ICONS else "doc"
        definition = no_em(g.get("body", ""))
        ul = g.get("underline")
        t_ul = self.A.at(ul, after=t0, default=None) if ul else None

        def draw(frame, t, sc):
            al, _ = pop(t, t0 + 0.2)
            blend(frame, hexbadge(70, NAVY, icon=ic, icon_col=GOLD), MX + 70, 210, al * sc.fade(t),
                  scale=0.7 + 0.3 * ease_out(ramp(t, t0 + 0.2, 0.5)), anchor="c")
            if g.get("kicker"):
                put(frame, label(g["kicker"], GREY, 20), MX + 170, 160, t, t0 + 0.3, sc)
            put(frame, rich(g.get("title", ""), (), "serif", 64, 650, NAVY, GOLD, max_w=PW - MX - 170), MX + 166, 182, t,
                t0 + 0.35, sc)
            ds = text(definition, "sans", 40, 400, NAVY, max_w=PW - 2 * MX, lh=1.3)
            put(frame, ds, MX, 320, t, t0 + 0.6, sc)
            if ul and t_ul is not None:
                f = font("sans", 40, 400)
                line_h = int(40 * 1.3)
                cur_line, x = 0, 0
                for token in definition.split(" "):
                    tw = f.getlength(token)
                    if x and x + f.getlength(" ") + tw > PW - 2 * MX:
                        cur_line += 1
                        x = 0
                    elif x:
                        x += f.getlength(" ")
                    if _tok(token) and _tok(token) == _tok(ul if isinstance(ul, str) else "")[:1]:
                        p = ease_out(ramp(t, t_ul, 0.5))
                        fill(frame, MX + 4 + x, 320 + 4 + cur_line * line_h + 50, tw * p, 6, GOLD, sc.fade(t))
                        break
                    x += tw
            y = 320 + ds.shape[0] + 34
            subs = g.get("items") or []
            stins = self.items_at(subs, t0 + 1.0)
            for i, s in enumerate(subs):
                s = s if isinstance(s, dict) else {"text": s}
                al, dy = pop(t, stins[i])
                blend(frame, hexbadge(18, GOLD), MX + 18, y + 27 + dy, al * sc.fade(t), anchor="c")
                put(frame, text(no_em(s["text"]), "sans", 40, 500, NAVY), MX + 54, y, t, stins[i], sc)
                y += 70
            self.extras(frame, t, sc, y, g, t0)
        Scene(self, "S", t0, t1, draw)

    # ---------------------------------------------------------- full-screen explainers (kind F)
    def g_quote(self, g, t0, t1):
        """A clause on a paper sheet: types out, the key words get a highlighter swipe, a navy caption pill below."""
        quote = no_em(g.get("text", ""))
        hl = no_em(g.get("highlight", ""))
        t_hl = self.A.at(g.get("highlight_at") or hl or None, after=t0, default=t0 + 4.0)
        cap = g.get("caption")
        cap = cap if isinstance(cap, dict) else ({"text": cap} if cap else None)
        t_cap = self.A.at(cap.get("at"), after=t0, default=t0 + 6.5) if cap else None
        tw_dur = clamp(len(quote) / 22.0, 2.0, 7.0)
        qsize = 44 if len(quote) < 190 else 38

        def draw(frame, t, sc):
            put(frame, label(g.get("kicker", "What the standard actually says")), 220, 120, t, t0 + 0.2, sc)
            al, dy = pop(t, t0 + 0.25, 0.6)
            blend(frame, rrect(1480, 560, 14, PAPER, shadow=18), 184, 154 + dy, al * sc.fade(t))
            if g.get("source"):
                put(frame, text(no_em(g["source"]), "sans", 28, 600, GREY), 290, 240, t, t0 + 0.4, sc)
            if g.get("heading"):
                put(frame, text(no_em(g["heading"]), "serif", 40, 600, NAVY, max_w=1340), 286, 284, t, t0 + 0.5, sc)
            n = int(clamp((t - t0 - 0.9) / tw_dur) * len(quote))
            if n > 0:
                f = font("serif", qsize, 420)
                p = ease_out(ramp(t, t_hl, 0.6))
                if p > 0 and hl and hl in quote:
                    lines, cur = [], ""
                    for wd in quote.split(" "):
                        trial = (cur + " " + wd).strip()
                        if f.getlength(trial) <= 1340 or not cur:
                            cur = trial
                        else:
                            lines.append(cur)
                            cur = wd
                    lines.append(cur)
                    for li, ln in enumerate(lines):
                        if hl in ln:
                            pre = ln[:ln.index(hl)].rstrip()
                            x0 = ink_right(pre, "serif", qsize, 420) + 8 if pre else 0
                            x1 = ink_right(ln[:ln.index(hl) + len(hl)], "serif", qsize, 420)
                            fill(frame, 294 + x0, 374 + li * int(qsize * 1.32) + 6, (x1 - x0 + 10) * p, qsize + 6, HILITE,
                                 sc.fade(t))
                blend(frame, text(quote[:n], "serif", qsize, 420, NAVY, max_w=1340, lh=1.32), 290, 370, sc.fade(t))
            if g.get("paraphrased", True):
                put(frame, text("Paraphrased", "sans", 20, 400, GREY), 1680, 676, t, t0 + 1, sc, anchor="tr")
            if cap:
                al, dy = pop(t, t_cap)
                ps = pill_runs(cap["text"], cap.get("accent", ""), CREAM, GOLD)
                blend(frame, rrect(ps.shape[1] + 60, 92, 46, NAVY), 220, 800 + dy, al * sc.fade(t))
                blend(frame, ps, 250, 816 + dy, al * sc.fade(t))
        Scene(self, "F", t0 - 0.3, t1, draw)

    def g_compare(self, g, t0, t1):
        """Two paper columns side by side (ISO vs EU AI Act, before vs after, applies vs market)."""
        cols = [g.get("left") or {}, g.get("right") or {}]
        col_t = [self.A.at(c.get("at"), after=t0, default=t0 + 0.4 + 0.9 * k) for k, c in enumerate(cols)]
        item_t = [self.items_at([i if isinstance(i, dict) else {"text": i} for i in c.get("items", [])], col_t[k], 0.25)
                  for k, c in enumerate(cols)]
        cap = g.get("caption")
        cap = cap if isinstance(cap, dict) else ({"text": cap} if cap else None)
        t_cap = self.A.at(cap.get("at"), after=t0, default=t1 - 3.0) if cap else None

        def draw(frame, t, sc):
            put(frame, label(g.get("kicker", "")), 200, 110, t, t0, sc)
            if g.get("title"):
                put(frame, rich(g["title"], g.get("accent", ()), "serif", 54, 620, NAVY, GOLD, max_w=1500), 196, 140, t,
                    t0 + 0.1, sc)
            top = 250 if g.get("title") else 170
            for k, (x, c) in enumerate(zip((200, 1000), cols)):
                al, dy = pop(t, col_t[k])
                a = al * sc.fade(t)
                hgt = 880 - top - (110 if cap else 0)
                blend(frame, rrect(720, hgt, 20, PAPER, shadow=14), x, top + dy, a)
                blend(frame, text(no_em(c.get("head", "")), "serif", 46, 650, NAVY), x + 50, top + 28 + dy, a)
                fill(frame, x + 50, top + 96 + dy, 620, 3, GOLD, a)
                items = c.get("items", [])
                stepy = min(92, (hgt - 140) / max(1, len(items)))
                for i, it in enumerate(items):
                    it = it if isinstance(it, dict) else {"text": it}
                    al2, dy2 = pop(t, item_t[k][i])
                    y = top + 124 + i * stepy
                    if it.get("hl"):
                        p = ease_out(ramp(t, self.A.at(it["hl"], after=t0, default=item_t[k][i] + 1.0), 0.5))
                        if p > 0:
                            fill(frame, x + 36, y + dy2 - 6, 650 * p, 70, HILITE, al2 * sc.fade(t))
                    if it.get("empty"):
                        blend(frame, rrect(620, 66, 14, None, stroke=GOLD, sw=3), x + 50, y - 4 + dy2, al2 * sc.fade(t))
                        blend(frame, text(no_em(it.get("text", "")), "sans", 34, 600, GOLD), x + 76, y + 8 + dy2,
                              al2 * sc.fade(t))
                    else:
                        blend(frame, text(no_em(it.get("text", "")), "sans", 38, 500, NAVY, max_w=620), x + 56, y + dy2,
                              al2 * sc.fade(t))
            if cap:
                al, dy = pop(t, t_cap)
                ps = pill_runs(cap["text"], cap.get("accent", ""), CREAM, GOLD, 42)
                blend(frame, rrect(1520, 90, 45, NAVY), 200, 905 + dy, al * sc.fade(t))
                blend(frame, ps, W / 2, 920 + dy, al * sc.fade(t), anchor="tc")
        Scene(self, "F", t0 - 0.3, t1, draw)

    def g_cards(self, g, t0, t1):
        """Headline plus a stack of source cards (citations, examples) with an optional hex cluster on the left."""
        items = [it if isinstance(it, dict) else {"text": it} for it in g.get("items", [])][:4]
        tins = self.items_at(items, t0 + 0.6, 0.9)
        icons = [i for i in g.get("icons", []) if i in ICONS][:3]
        n = max(1, len(items))
        ch = min(190, 560 / n)

        def draw(frame, t, sc):
            put(frame, label(g.get("kicker", "")), 160, 130, t, t0, sc)
            put(frame, rich(g.get("title", ""), g.get("accent", ()), "serif", 64, 620, NAVY, GOLD, max_w=1600), 156, 170, t,
                t0 + 0.1, sc)
            if icons:
                gg = ease_in_out(ramp(t, t0 + 0.6, 2.4))
                spread = 230 - 120 * gg
                for k, (ic, ang, col) in enumerate(zip(icons, (-90, 30, 150), (NAVY, GOLD, GREY))):
                    x = 520 + spread * math.cos(math.radians(ang))
                    y = 610 + spread * math.sin(math.radians(ang))
                    al, _ = pop(t, t0 + 0.2 + k * 0.15)
                    blend(frame, hexbadge(130, col, icon=ic, icon_col=CREAM if col != GOLD else NAVY, alpha=200), x, y,
                          al * sc.fade(t) * 0.92, anchor="c")
            x0, cw = (960, 860) if icons else (160, 1600)
            for k, it in enumerate(items):
                y = 330 + k * ch
                al, dy = pop(t, tins[k])
                a = al * sc.fade(t)
                blend(frame, rrect(cw, ch - 30, 16, PAPER, shadow=10), x0, y + dy, a)
                fill(frame, x0 + 10, y + 30 + dy, 6, ch - 90, GOLD, a)
                if it.get("label"):
                    blend(frame, label(it["label"], GREY, 22), x0 + 40, y + 28 + dy, a)
                blend(frame, rich(it.get("text", ""), it.get("accent", ()), "serif", 38, 520, NAVY, GOLD, max_w=cw - 90),
                      x0 + 38, y + (64 if it.get("label") else 40) + dy, a)
        Scene(self, "F", t0 - 0.3, t1, draw)

    # ---------------------------------------------------------- per frame
    def background(self):
        if self.bg is None:
            bg = np.empty((H, W, 3), np.uint8)
            bg[:] = CREAM
            blend(bg, honeycomb(), 0, 0, 1.0)
            self.bg = bg
        return self.bg

    def camera_state(self, t):
        s = max([sc.env(t) for sc in self.scenes if sc.kind == "S" and sc.t0 - 0.1 <= t <= sc.t1 + 0.1] + [0])
        f = max([sc.env(t, 0.3) for sc in self.scenes if sc.kind == "F" and sc.t0 - 0.1 <= t <= sc.t1 + 0.1] + [0])
        return s, f

    def panel_offset(self, sc):
        """Split panels are laid out from the top; measure the fully built panel once and centre it vertically,
        so a short list or card doesn't leave the lower half of the panel empty."""
        if getattr(sc, "dy", None) is None:
            canvas = np.empty((H, PW, 3), np.uint8)
            canvas[:] = CREAM
            sc.draw(canvas, max(sc.t0 + 0.6, sc.t1 - sc.tail - 0.4), sc)
            rows = np.where(np.any(np.abs(canvas.astype(np.int16) - np.array(CREAM, np.int16)) > 12, axis=(1, 2)))[0]
            if len(rows):
                top, bot = int(rows[0]), int(rows[-1])
                sc.dy = int(max(0, min((H - (bot - top)) / 2 - top, H - 90 - bot)))
            else:
                sc.dy = 0
        return sc.dy

    def draw_panel(self, frame, t, sc):
        dy = self.panel_offset(sc)
        if dy <= 0:
            sc.draw(frame, t, sc)
            return
        canvas = np.empty((H, PW, 3), np.uint8)
        canvas[:] = CREAM
        sc.draw(canvas, t, sc)
        region = frame[dy:, :PW]
        src = canvas[:H - dy]
        mask = np.any(src != np.array(CREAM, np.uint8), axis=2)
        region[mask] = src[mask]

    def draw(self, frame, t):
        s, f = self.camera_state(t)
        if s > 0:
            fill(frame, 0, 0, PW * s, H, CREAM)
            fill(frame, PW * s - 6, 0, 6, H, GOLD)
        if f > 0:
            wipe = int(W * f)
            frame[:, :wipe] = self.background()[:, :wipe]
            if f < 0.999:
                fill(frame, wipe - 8, 0, 8, H, GOLD)
        for sc in self.scenes:
            if sc.t0 - 0.05 <= t <= sc.t1 + 0.05 and sc.draw:
                if sc.kind == "F" and sc.env(t, 0.3) < 0.95 and t < sc.t0 + 0.3:
                    continue
                if sc.kind == "S" and sc.env(t) < 0.6 and t < sc.t0 + 0.5:
                    continue
                if sc.kind == "S":
                    self.draw_panel(frame, t, sc)
                else:
                    sc.draw(frame, t, sc)
        if f < 0.5:
            self.draw_tracker(frame, t)

    def end_card(self, tl_):
        f = self.background().copy()
        a = ease_out(ramp(tl_, 0.3, 0.6))
        b = self.cfg["brand"]
        blend(f, logo("full", 300), W / 2, 60, a, anchor="tc", scale=0.94 + 0.06 * a)
        blend(f, runs(((b.get("tagline_a", "Enterprise compliance, "), NAVY),
                       (b.get("tagline_b", "without the enterprise headache."), GOLD)), "sans", 34, 500),
              W / 2, 390, ease_out(ramp(tl_, 0.6, 0.6)), anchor="tc")
        # two 16:9 slots where YouTube's end-screen videos go, and the site underneath
        e = ease_out(ramp(tl_, 0.9, 0.6))
        vw, vh = 560, 315
        for x, lab in ((W / 2 - vw - 40, "Watch next"), (W / 2 + 40, "Start with this one")):
            blend(f, rrect(vw, vh, 16, None, stroke=GOLD, sw=4), x, 500, e)
            blend(f, label(lab, GREY, 22), x + vw / 2, 500 + vh + 18, e, anchor="tc")
        blend(f, text(b.get("site", "honeybadgerandbees.com"), "sans", 30, 600, NAVY), W / 2, 1000,
              ease_out(ramp(tl_, 1.1, 0.6)), anchor="tc")
        return f


SPLIT = ["list", "card"]
FULL = ["quote", "compare", "cards"]
DEFAULT_DUR = {"title": 7.0, "name": 6.0, "lower": 6.0, "related": 6.0, "next": 6.0, "warning": 3.0, "statement": 5.0,
               "chapter": 3.6, "linkedin": 5.0, "brand": 5.0, "subscribe": 5.0, "list": 10.0, "card": 10.0,
               "quote": 12.0, "compare": 12.0, "cards": 12.0}


# ---------------------------------------------------------------- camera

def quantise(keep, fps):
    """Whole frames per kept range, so picture and sound stay in sync over 100+ cuts."""
    clips, t = [], 0
    for a, b in keep:
        fi = round(a * fps)
        n = round((b - a) * fps)
        if n <= 0:
            continue
        clips.append({"src_in": fi / fps, "src_out": (fi + n) / fps, "frames": n, "out_start": t / fps})
        t += n
    return clips, t


def camera_plan(clips, edit, cfg):
    """Zoom level per clip: a punch-in that alternates where a cut skips a retake or a long pause, the script's
    zoom cues, a slow zoom in at the opening and a gentle zoom out at the sign-off."""
    look = cfg["look"]
    punch = look.get("zoom_punch", 1.2)
    level, prev = 1.0, None
    for i, c in enumerate(clips):
        kind, hold = None, None
        for zc in edit.get("zoom", []):
            if zc["t0"] - 0.3 <= c["src_in"] <= zc["t1"]:
                kind = zc["kind"]
                if kind == "in":
                    hold = float(zc.get("z", punch))  # held only inside the cue, e.g. to crop something out
        if prev is not None and c["src_in"] - prev > look.get("punch_after_gap", 2.0):
            level = punch if level == 1.0 else 1.0
        c["level"], c["kind"] = (hold if hold else level), (None if kind == "in" else kind)
        prev = c["src_out"]
    if clips:
        clips[0]["kind"] = clips[0]["kind"] or "slow_in"
        clips[-1]["kind"] = "slow_out"
    return clips


def zoom_at(c, local, cfg):
    n = max(1, c["frames"] - 1)
    p = local / n
    z = c["level"] * (1.0 + 0.025 * p)
    amt = cfg["look"].get("slow_zoom", 0.08)
    if c["kind"] == "slow_in":
        z = c["level"] * (1.0 + amt * ease_in_out(p))
    elif c["kind"] == "slow_out":
        z = c["level"] * (1.0 + amt - amt * ease_in_out(p))
    return z


def warp(src, z, shift, cfg):
    SH, SW = src.shape[:2]
    cx, cy = cfg["look"]["face_x"], cfg["look"]["face_y"]
    cw = 1.0 / z
    cx = min(max(cx, cw / 2), 1 - cw / 2)
    cy = min(max(cy, cw / 2), 1 - cw / 2)
    a = SW / (W * z)
    bx = (cx - cw / 2) * SW
    by = (cy - cw / 2) * SH
    M = np.float32([[a, 0, bx - a * shift], [0, a, by]])
    return cv2.warpAffine(src, M, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


def decode(source, c, size, hw):
    SW, SH = size
    cmd = ["ffmpeg", "-v", "error"] + (["-hwaccel", "videotoolbox"] if hw else []) + [
        "-ss", "%.4f" % c["src_in"], "-i", source, "-frames:v", str(c["frames"]),
        "-vf", "scale=%d:%d:flags=bicubic" % (SW, SH), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=SW * SH * 3 * 2)
    n = SW * SH * 3
    last = None
    for _ in range(c["frames"]):
        buf = p.stdout.read(n)
        if len(buf) == n:
            last = np.frombuffer(buf, np.uint8).reshape(SH, SW, 3)
        yield last if last is not None else np.zeros((SH, SW, 3), np.uint8)
    p.stdout.close()
    p.wait()


def build_voice(source, clips, fps, out, cfg, tail):
    """Per-clip trims with 12 ms fades, cleaned, two-pass loudness to -14 LUFS, silence under the end card."""
    a = cfg["audio"]
    parts, labels = [], []
    for i, c in enumerate(clips):
        d = c["frames"] / fps
        parts.append("[0:a]atrim=start=%.4f:duration=%.4f,asetpts=PTS-STARTPTS,afade=t=in:d=0.012,"
                     "afade=t=out:st=%.4f:d=0.012[a%d]" % (c["src_in"], d, max(0, d - 0.012), i))
        labels.append("[a%d]" % i)
    chain = "highpass=f=%d" % a.get("highpass", 80)
    if a.get("denoise"):
        chain += ",afftdn=nr=%d:nf=-40" % a.get("denoise_db", 10)
    chain += ",deesser=i=0.35,acompressor=threshold=-20dB:ratio=2.5:attack=8:release=120:makeup=2"
    graph = ";".join(parts) + ";" + "".join(labels) + "concat=n=%d:v=0:a=1,pan=mono|c0=c0,%s[pre]" % (len(clips), chain)
    gpath = out + ".graph.txt"
    with open(gpath, "w") as f:
        f.write(graph)
    pre = out + ".pre.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-filter_complex_script", gpath, "-map", "[pre]",
                    "-ar", "48000", "-c:a", "pcm_s24le", pre], check=True)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", pre, "-af", "loudnorm=I=%d:TP=-1.5:LRA=9:print_format=json"
                        % a["lufs"], "-f", "null", "-"], capture_output=True, text=True)
    js = r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1]
    m = json.loads(js)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", pre, "-af",
                    "loudnorm=I=%d:TP=-1.5:LRA=9:measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:"
                    "offset=%s:linear=true,apad=pad_dur=%.2f" % (a["lufs"], m["input_i"], m["input_tp"], m["input_lra"],
                                                                 m["input_thresh"], m["target_offset"], tail),
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", out], check=True)
    os.remove(pre)
    os.remove(gpath)


def encoder(cfg):
    enc = cfg["output"].get("encoder", "auto")
    if enc == "auto":
        encs = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        enc = "h264_videotoolbox" if "h264_videotoolbox" in encs else "libx264"
    if enc == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", cfg["output"].get("bitrate", "16M"), "-allow_sw", "1"]
    return ["-c:v", "libx264", "-preset", cfg["output"].get("preset", "medium"), "-crf", str(cfg["output"].get("crf", 16))]


def render_video(edit, words, tl_cls, cfg, out, part=None, stills=None, log=print):
    """Cut + camera + graphics + end card -> out (mp4). stills: list of output seconds -> jpgs instead of a video.
    Returns (clips quantised to frames, voice_end, Show)."""
    fps = int(round(cfg["output"].get("fps") or edit["info"]["fps"]))
    clips, total = quantise(edit["keep"], fps)
    clips = camera_plan(clips, edit, cfg)
    voice_end = total / fps
    tl = tl_cls([[c["src_in"], c["src_out"]] for c in clips])
    show = Show(edit.get("graphics", []), Anchors(words, tl), voice_end, cfg)
    endsec = float(cfg.get("end_screen_seconds", 12))
    full = voice_end + endsec
    t_from, t_to = (part or (0, full))
    t_to = min(t_to, full)
    tight = cfg["look"].get("zoom_punch", 1.2) * (1 + cfg["look"].get("slow_zoom", 0.08))
    size = (int(W * min(tight, edit["info"]["width"] / W) / 2) * 2, int(H * min(tight, edit["info"]["height"] / H) / 2) * 2)
    hw = sys.platform == "darwin"
    still_k = {round(s * fps) for s in (stills or [])}
    enc = None
    voice = os.path.join(os.path.dirname(out), "voice.wav")
    if not stills:
        build_voice(edit["source"], clips, fps, voice, cfg, endsec + 1)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                                "-r", str(fps), "-i", "-", "-ss", "%.3f" % t_from, "-t", "%.3f" % (t_to - t_from),
                                "-i", voice, "-vf", "scale=out_color_matrix=bt709:out_range=tv"] + encoder(cfg) +
                               ["-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc",
                                "bt709", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", out],
                               stdin=subprocess.PIPE)
    started, done = time.time(), 0
    for c in clips:
        k0 = round(c["out_start"] * fps)
        c_t1 = c["out_start"] + c["frames"] / fps
        if c_t1 <= t_from or c["out_start"] >= t_to:
            continue
        if stills and not any(k0 <= k < k0 + c["frames"] for k in still_k):
            continue
        for i, src in enumerate(decode(edit["source"], c, size, hw)):
            t = c["out_start"] + i / fps
            if t < t_from - 1e-6 or t >= t_to:
                continue
            if stills and (k0 + i) not in still_k:
                continue
            s, f = show.camera_state(t)
            if f >= 0.999:
                frame = np.empty((H, W, 3), np.uint8)
            else:
                z = zoom_at(c, i, cfg)
                z = z + (1.0 - z) * s
                frame = warp(src, z, SHIFT * s, cfg)
            show.draw(frame, t)
            if stills:
                cv2.imwrite(os.path.join(out, "still_%07.2f.jpg" % t), frame[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 88])
            else:
                enc.stdin.write(frame.tobytes())
            done += 1
            if done % 500 == 0:
                log("  %6.1f s rendered, %.1f fps" % (t, done / (time.time() - started)))
    if t_to > voice_end:
        n = round((t_to - max(t_from, voice_end)) * fps)
        for i in range(n):
            tl_ = max(t_from, voice_end) - voice_end + i / fps
            t = voice_end + tl_
            if stills:
                if round(t * fps) in still_k:
                    cv2.imwrite(os.path.join(out, "still_%07.2f.jpg" % t), show.end_card(tl_)[..., ::-1],
                                [cv2.IMWRITE_JPEG_QUALITY, 88])
                continue
            enc.stdin.write(show.end_card(tl_).tobytes())
    if enc:
        enc.stdin.close()
        enc.wait()
        if enc.returncode:
            raise RuntimeError("ffmpeg encoder failed")
    log("  %d frames in %d s" % (done, time.time() - started))
    return clips, voice_end, show, tl
