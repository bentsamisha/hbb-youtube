"""Drawing primitives and sprite cache for the HoneyBadger & Bees motion-graphics layer (the published EP.11 v3 look)."""
import math, os
from functools import lru_cache
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
FONTS = os.path.join(ASSETS, "fonts")

NAVY = (10, 25, 47)
CREAM = (245, 240, 232)
PAPER = (252, 250, 246)
GOLD = (232, 163, 23)
GREY = (139, 149, 165)
RED = (196, 64, 52)
WHITE = (255, 255, 255)
LINKEDIN = (10, 102, 194)

W, H = 1920, 1080
SS = 3  # supersampling for shapes


# ---------------------------------------------------------------- easing
def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x

def ease_out(p):
    p = clamp(p)
    return 1 - (1 - p) ** 3

def ease_in_out(p):
    p = clamp(p)
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2

def ramp(t, t0, dur):
    return clamp((t - t0) / dur) if dur > 0 else float(t >= t0)


# ---------------------------------------------------------------- fonts
@lru_cache(maxsize=None)
def font(family, size, wght=400, opsz=None):
    path = os.path.join(FONTS, "Fraunces.ttf" if family == "serif" else "Outfit.ttf")
    f = ImageFont.truetype(path, size)
    axes = f.get_variation_axes()
    vals = []
    for ax in axes:
        name = ax.get("name", b"")
        name = name.decode() if isinstance(name, bytes) else str(name)
        n = name.lower()
        if "weight" in n:
            v = wght
        elif "optical" in n:
            v = opsz or min(144, max(9, size))
        elif "soft" in n:
            v = 50
        elif "wonk" in n:
            v = 0
        else:
            v = ax.get("default", ax["minimum"])
        vals.append(clamp(v, ax["minimum"], ax["maximum"]))
    f.set_variation_by_axes(vals)
    return f


def to_np(img):
    return np.asarray(img.convert("RGBA"), dtype=np.uint8)


# ---------------------------------------------------------------- text sprites
@lru_cache(maxsize=4096)
def text(s, family="sans", size=40, wght=400, color=NAVY, max_w=None, lh=1.18, track=0, align="left"):
    """Return RGBA numpy sprite of (wrapped) text."""
    f = font(family, size, wght)
    lines = []
    for para in s.split("\n"):
        if max_w is None:
            lines.append(para)
            continue
        cur = ""
        for word in para.split(" "):
            trial = (cur + " " + word).strip()
            if f.getlength(trial) + track * len(trial) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    asc, desc = f.getmetrics()
    line_h = int(size * lh)
    widths = [int(f.getlength(l) + track * max(0, len(l) - 1)) for l in lines]
    w = int(max(widths + [1]) * 1.06) + 16
    h = line_h * (len(lines) - 1) + asc + desc + 8
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        x = 4 if align == "left" else (w - widths[i]) // 2 if align == "center" else w - widths[i] - 4
        y = 4 + i * line_h
        if track:
            for ch in l:
                d.text((x, y), ch, font=f, fill=color + (255,))
                x += f.getlength(ch) + track
        else:
            d.text((x, y), l, font=f, fill=color + (255,))
    return to_np(img)


@lru_cache(maxsize=1024)
def runs(parts, family="sans", size=40, wght=400):
    """Single line of mixed-colour text. parts = ((text, color), ...)"""
    f = font(family, size, wght)
    asc, desc = f.getmetrics()
    w = int(sum(f.getlength(t) for t, _ in parts)) + 8
    img = Image.new("RGBA", (w, asc + desc + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 4
    for t, c in parts:
        d.text((x, 4), t, font=f, fill=c + (255,))
        x += f.getlength(t)
    return to_np(img)


def ink_right(s, family="sans", size=40, wght=400):
    """Right edge of the actually rendered glyphs (variable-font safe)."""
    spr = text(s, family, size, wght, NAVY)
    cols = np.where(spr[..., 3].max(0) > 40)[0]
    return (cols[-1] - 4) if len(cols) else 0


def text_width(s, family="sans", size=40, wght=400):
    return font(family, size, wght).getlength(s)


# ---------------------------------------------------------------- shape sprites
def _ss(w, h):
    return Image.new("RGBA", (int(w * SS), int(h * SS)), (0, 0, 0, 0))

def _down(img, w, h):
    return to_np(img.resize((int(w), int(h)), Image.LANCZOS))


@lru_cache(maxsize=256)
def rrect(w, h, r=18, fill=NAVY, alpha=255, stroke=None, sw=0, shadow=0):
    pad = shadow * 2
    img = _ss(w + pad * 2, h + pad * 2)
    d = ImageDraw.Draw(img)
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [pad * SS, (pad + shadow * 0.6) * SS, (pad + w) * SS, (pad + h + shadow * 0.6) * SS],
            r * SS, fill=(10, 25, 47, 70))
        sh = sh.filter(ImageFilter.GaussianBlur(shadow * SS * 0.6))
        img = Image.alpha_composite(img, sh)
        d = ImageDraw.Draw(img)
    box = [pad * SS, pad * SS, (pad + w) * SS - 1, (pad + h) * SS - 1]
    d.rounded_rectangle(box, r * SS, fill=(fill + (alpha,)) if fill else None,
                        outline=(stroke + (255,)) if stroke else None, width=int(sw * SS))
    return _down(img, w + pad * 2, h + pad * 2)


def hex_points(cx, cy, r, rot=30):
    return [(cx + r * math.cos(math.radians(rot + 60 * i)), cy + r * math.sin(math.radians(rot + 60 * i)))
            for i in range(6)]


def _icon(d, kind, cx, cy, s, col, lw):
    """Simple geometric icons drawn at supersampled scale. s = icon half-size."""
    c = col + (255,)
    if kind == "provider":   # box with arrow out
        d.rounded_rectangle([cx - s, cy - s * 0.3, cx + s * 0.55, cy + s], s * 0.12, outline=c, width=lw)
        d.line([cx - s * 0.1, cy + s * 0.15, cx + s * 0.95, cy - s * 0.9], fill=c, width=lw)
        d.line([cx + s * 0.3, cy - s * 0.92, cx + s * 0.97, cy - s * 0.92], fill=c, width=lw)
        d.line([cx + s * 0.95, cy - s * 0.95, cx + s * 0.95, cy - s * 0.28], fill=c, width=lw)
    elif kind == "producer":  # gear
        for i in range(8):
            a = math.radians(i * 45)
            d.line([cx + math.cos(a) * s * 0.55, cy + math.sin(a) * s * 0.55,
                    cx + math.cos(a) * s * 1.0, cy + math.sin(a) * s * 1.0], fill=c, width=int(lw * 1.6))
        d.ellipse([cx - s * 0.68, cy - s * 0.68, cx + s * 0.68, cy + s * 0.68], outline=c, width=lw)
        d.ellipse([cx - s * 0.25, cy - s * 0.25, cx + s * 0.25, cy + s * 0.25], outline=c, width=lw)
    elif kind == "customer":  # person
        d.ellipse([cx - s * 0.38, cy - s * 0.95, cx + s * 0.38, cy - s * 0.19], outline=c, width=lw)
        d.arc([cx - s * 0.85, cy - s * 0.02, cx + s * 0.85, cy + s * 1.6], 180, 360, fill=c, width=lw)
        d.line([cx - s * 0.85, cy + s * 0.79, cx + s * 0.85, cy + s * 0.79], fill=c, width=lw)
    elif kind == "partner":   # two linked rings
        d.ellipse([cx - s * 1.0, cy - s * 0.55, cx + s * 0.15, cy + s * 0.55], outline=c, width=lw)
        d.ellipse([cx - s * 0.15, cy - s * 0.55, cx + s * 1.0, cy + s * 0.55], outline=c, width=lw)
    elif kind == "subject":   # small person outline, dotted feel
        d.ellipse([cx - s * 0.3, cy - s * 0.8, cx + s * 0.3, cy - s * 0.2], outline=c, width=lw)
        d.arc([cx - s * 0.7, cy - s * 0.05, cx + s * 0.7, cy + s * 1.35], 180, 360, fill=c, width=lw)
    elif kind == "authority":  # building with columns
        d.polygon([(cx - s, cy - s * 0.35), (cx, cy - s), (cx + s, cy - s * 0.35)], outline=c, width=lw)
        for i in range(4):
            x = cx - s * 0.7 + i * s * 0.466
            d.line([x, cy - s * 0.2, x, cy + s * 0.65], fill=c, width=lw)
        d.line([cx - s, cy + s * 0.85, cx + s, cy + s * 0.85], fill=c, width=lw)
    elif kind == "doc":       # page with lines
        d.rounded_rectangle([cx - s * 0.7, cy - s, cx + s * 0.7, cy + s], s * 0.12, outline=c, width=lw)
        for k in range(3):
            y = cy - s * 0.45 + k * s * 0.42
            d.line([cx - s * 0.4, y, cx + s * (0.4 if k < 2 else 0.1), y], fill=c, width=lw)
    elif kind == "shield":
        d.line([cx, cy - s, cx + s * 0.85, cy - s * 0.6, cx + s * 0.75, cy + s * 0.25, cx, cy + s,
                cx - s * 0.75, cy + s * 0.25, cx - s * 0.85, cy - s * 0.6, cx, cy - s], fill=c, width=lw, joint="curve")
    elif kind == "lock":
        d.rounded_rectangle([cx - s * 0.75, cy - s * 0.15, cx + s * 0.75, cy + s * 0.95], s * 0.12, outline=c, width=lw)
        d.arc([cx - s * 0.5, cy - s * 0.95, cx + s * 0.5, cy + s * 0.15], 180, 360, fill=c, width=lw)
    elif kind == "clock":
        d.ellipse([cx - s * 0.9, cy - s * 0.9, cx + s * 0.9, cy + s * 0.9], outline=c, width=lw)
        d.line([cx, cy, cx, cy - s * 0.55], fill=c, width=lw)
        d.line([cx, cy, cx + s * 0.4, cy + s * 0.2], fill=c, width=lw)
    elif kind == "people":
        for ox in (-0.5, 0.5):
            d.ellipse([cx + s * ox - s * 0.28, cy - s * 0.85, cx + s * ox + s * 0.28, cy - s * 0.29], outline=c, width=lw)
            d.arc([cx + s * ox - s * 0.55, cy - s * 0.1, cx + s * ox + s * 0.55, cy + s * 1.1], 180, 360, fill=c, width=lw)
    elif kind == "money":
        d.ellipse([cx - s * 0.9, cy - s * 0.9, cx + s * 0.9, cy + s * 0.9], outline=c, width=lw)
        f = ImageFont.truetype(os.path.join(FONTS, "Outfit.ttf"), int(s * 1.2))
        d.text((cx, cy), "$", font=f, fill=c, anchor="mm")
    elif kind == "check":
        d.line([cx - s * 0.6, cy, cx - s * 0.15, cy + s * 0.5, cx + s * 0.7, cy - s * 0.55], fill=c, width=int(lw * 1.3), joint="curve")
    elif kind == "cross":
        d.line([cx - s * 0.5, cy - s * 0.5, cx + s * 0.5, cy + s * 0.5], fill=c, width=lw)
        d.line([cx - s * 0.5, cy + s * 0.5, cx + s * 0.5, cy - s * 0.5], fill=c, width=lw)
    elif kind == "warn":
        d.polygon([(cx, cy - s), (cx + s * 1.05, cy + s * 0.8), (cx - s * 1.05, cy + s * 0.8)], outline=c, width=lw)
        d.line([cx, cy - s * 0.35, cx, cy + s * 0.25], fill=c, width=lw)
        d.ellipse([cx - lw * 0.6, cy + s * 0.45, cx + lw * 0.6, cy + s * 0.45 + lw * 1.2], fill=c)


@lru_cache(maxsize=256)
def hexbadge(r, fill=NAVY, stroke=None, sw=0, icon=None, icon_col=CREAM, label=None, label_col=CREAM,
             label_size=None, alpha=255):
    pad = 4
    size = int(r * 2 + pad * 2)
    img = _ss(size, size)
    d = ImageDraw.Draw(img)
    cx = cy = size * SS / 2
    pts = hex_points(cx, cy, r * SS)
    d.polygon(pts, fill=(fill + (alpha,)) if fill else None)
    if stroke:
        d.line(pts + [pts[0]], fill=stroke + (255,), width=int(sw * SS), joint="curve")
    if icon:
        _icon(d, icon, cx, cy, r * SS * 0.42, icon_col, max(2, int(r * SS * 0.07)))
    out = img.resize((size, size), Image.LANCZOS)
    if label:
        f = font("sans", label_size or int(r * 0.8), 600)
        dd = ImageDraw.Draw(out)
        dd.text((size / 2, size / 2), label, font=f, fill=label_col + (255,), anchor="mm")
    return to_np(out)


@lru_cache(maxsize=64)
def arrow(length, col=NAVY, lw=4, head=16, dashed=False):
    w, h = length + head, head * 2 + 4
    img = _ss(w, h)
    d = ImageDraw.Draw(img)
    y = h * SS / 2
    if dashed:
        x = 0
        while x < (length - 6) * SS:
            d.line([x, y, min(x + 14 * SS, (length - 2) * SS), y], fill=col + (255,), width=lw * SS)
            x += 24 * SS
    else:
        d.line([0, y, (length - 2) * SS, y], fill=col + (255,), width=lw * SS)
    d.polygon([((length + head) * SS - 2, y), ((length - 4) * SS, y - head * SS * 0.65),
               ((length - 4) * SS, y + head * SS * 0.65)], fill=col + (255,))
    return _down(img, w, h)


@lru_cache(maxsize=64)
def stamp(label, col=RED, size=44, angle=-7):
    f = font("sans", size, 700)
    tw = f.getlength(label)
    w, h = int(tw + size * 1.2), int(size * 1.7)
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([SS * 3, SS * 3, w * SS - SS * 3, h * SS - SS * 3], 10 * SS, outline=col + (255,), width=5 * SS)
    img = img.resize((w, h), Image.LANCZOS)
    ImageDraw.Draw(img).text((w / 2, h / 2 + 1), label, font=f, fill=col + (255,), anchor="mm")
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    return to_np(img)


@lru_cache(maxsize=4)
def honeycomb(w=W, h=H, r=46, col=NAVY, alpha=10):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    dx, dy = r * math.sqrt(3), r * 1.5
    for row in range(int(h / dy) + 2):
        for colm in range(int(w / dx) + 2):
            cx = colm * dx + (dx / 2 if row % 2 else 0)
            cy = row * dy
            pts = hex_points(cx, cy, r - 2, rot=30)
            d.line(pts + [pts[0]], fill=col + (alpha,), width=2)
    return to_np(img)


# ---------------------------------------------------------------- compositor
def blend(frame, spr, x, y, a=1.0, scale=1.0, anchor="tl", reveal=1.0, reveal_dir="x"):
    """Alpha-blend RGBA sprite onto RGB frame (in place)."""
    if a <= 0.003 or reveal <= 0:
        return
    if scale != 1.0:
        if scale <= 0.02:
            return
        spr = cv2.resize(spr, (max(1, int(spr.shape[1] * scale)), max(1, int(spr.shape[0] * scale))),
                         interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    h, w = spr.shape[:2]
    if anchor == "c":
        x, y = x - w / 2, y - h / 2
    elif anchor == "tc":
        x = x - w / 2
    elif anchor == "tr":
        x = x - w
    elif anchor == "bl":
        y = y - h
    elif anchor == "lc":
        y = y - h / 2
    elif anchor == "rc":
        x, y = x - w, y - h / 2
    x, y = int(round(x)), int(round(y))
    if reveal < 1.0:
        if reveal_dir == "x":
            w = max(1, int(w * reveal))
        else:
            h = max(1, int(h * reveal))
        spr = spr[:h, :w]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, frame.shape[1]), min(y + h, frame.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    s = spr[y0 - y:y1 - y, x0 - x:x1 - x]
    al = s[..., 3:4].astype(np.float32) * (a / 255.0)
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (s[..., :3] * al + roi * (1 - al)).astype(np.uint8)


def fill(frame, x, y, w, h, col, a=1.0):
    x0, y0 = max(int(x), 0), max(int(y), 0)
    x1, y1 = min(int(x + w), frame.shape[1]), min(int(y + h), frame.shape[0])
    if x1 <= x0 or y1 <= y0 or a <= 0:
        return
    if a >= 0.999:
        frame[y0:y1, x0:x1] = col
    else:
        roi = frame[y0:y1, x0:x1].astype(np.float32)
        frame[y0:y1, x0:x1] = (np.array(col, np.float32) * a + roi * (1 - a)).astype(np.uint8)


@lru_cache(maxsize=16)
def logo(kind="full", height=200):
    """Brand logo sprite (kind: 'full' with lettering, 'icon' badger + bees)."""
    img = Image.open(os.path.join(ASSETS, "logo_%s.png" % kind)).convert("RGBA")
    w = int(img.width * height / img.height)
    return to_np(img.resize((w, height), Image.LANCZOS))


@lru_cache(maxsize=4)
def cursor(size=46):
    img = _ss(size, size * 1.5)
    d = ImageDraw.Draw(img)
    s = size * SS
    pts = [(0.1 * s, 0.05 * s), (0.1 * s, 1.15 * s), (0.36 * s, 0.9 * s), (0.55 * s, 1.36 * s),
           (0.72 * s, 1.28 * s), (0.53 * s, 0.84 * s), (0.88 * s, 0.84 * s)]
    d.polygon(pts, fill=WHITE + (255,))
    d.line(pts + [pts[0]], fill=NAVY + (255,), width=int(2.5 * SS), joint="curve")
    return _down(img, size, size * 1.5)
