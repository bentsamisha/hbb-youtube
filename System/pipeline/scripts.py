"""Episode scripts: read the Video_N_Script docs and turn the production table into edit cues.

Scripts live in Google Drive (one numbered folder per episode, a Google Doc "Video_N_Script" inside).
`sync()` exports them to HBB/Scripts/Video_N_Script.md through the Drive API (read-only), so the
editor can work offline. You can also drop a .md / .txt export there by hand.

A script has two parts:
  ## Prompter Script     the spoken text, one paragraph per thought
  ## Production Script   a two-column table: spoken text | details (camera, on-screen text, B-roll, cards...)

parse() returns {"number", "title", "prompter": [paragraphs], "rows": [{"text", "details", "cues"}]}.
"""
import glob
import json
import os
import re

PIPE = os.path.dirname(os.path.realpath(__file__))
SYSTEM = os.path.dirname(PIPE)
ROOT = os.path.dirname(SYSTEM)
SCRIPTS_DIR = os.path.join(ROOT, "Scripts")


def clean(text):
    """Markdown export noise -> plain text (escapes, bold markers, smart quotes kept)."""
    text = re.sub(r"\\+([^\w\s])", r"\1", text)
    text = text.replace("**", "").replace("\\*", "*")
    text = text.replace(" ", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


# a quoted phrase: opens after a space/colon/slash, closes before space/punctuation, so "It's" stays inside
QUOTE = r"(?:(?<=^)|(?<=[\s:(/]))['\u2018](.+?)['\u2019](?=$|[\s.,;:)/])"


def parse_cues(details):
    """Production-table 'Details' cell -> structured cues the renderer understands."""
    d = clean(details)
    low = d.lower()
    cues = {}
    m = re.search(r"b-roll(?: duration)?:?\s*(\d+(?:\.\d+)?)\s*s", low)
    if m:
        prompt = re.search(r"AI prompt:\s*['‘](.+?)['’](?:\.|\s|$)", d)
        cues["broll"] = {"seconds": float(m.group(1)), "prompt": prompt.group(1) if prompt else ""}
    # on-screen text: everything quoted after ON-SCREEN / On-screen text / On-screen list
    m = re.search(r"on-screen(?: text| list)?(?: [a-z ,]+)?:\s*(.+?)(?=(?:\bCARD\b|\bLower third\b|$))", d, re.I)
    if m:
        seg = m.group(1)
        # "Underline 'develops' in honey gold" / "Highlight that 'producer' ..." are accents, not extra lines
        accents = re.findall(r"(?:Underline|Highlight)[^.']*['\u2018]([^'\u2018\u2019]+)['\u2019]", seg)
        seg = re.sub(r"(?:Underline|Highlight)[^.]*\.?", "", seg)
        quotes = re.findall(QUOTE, seg)
        if quotes:
            lines = []
            for q in quotes:
                parts = q.split(" / ") if " / " in q else re.split(r"\s+(?=\d\.\s)", q)
                lines += [x.strip() for x in parts]
            cues["onscreen"] = [x for x in lines if x]
            if accents:
                cues["accent"] = accents
        else:
            # unquoted description ("three badges stack up - PRODUCER, PROVIDER, CUSTOMER") -> keep as a note
            cues["onscreen_note"] = seg.strip().rstrip(".")
    m = re.search(r"lower third:\s*(.+?)(?:\.|$)", d, re.I)
    if m:
        txt = m.group(1).strip().strip("'‘’")
        cues["lower_third"] = "name" if "name" in txt.lower() and "credential" in txt.lower() else txt
    m = re.search(r"CARD:\s*(.+?)(?:\.\s|$)", d)
    if m:
        cues["card"] = m.group(1).strip()
    if "zoom in" in low:
        cues["zoom"] = "slow_in" if ("slow" in low or "gentle" in low or "subtle" in low) else "in"
    elif "zoom out" in low:
        cues["zoom"] = "slow_out"
    if low.startswith("b-roll") or "b-roll" in low[:20]:
        cues["shot"] = "broll"
    else:
        cues["shot"] = "talking"
    return cues


def parse(md):
    lines = md.splitlines()
    title = ""
    for ln in lines:
        if ln.startswith("# "):
            title = clean(ln[2:])
            break
    num = re.search(r"Video\s*#?\s*(\d+)", title, re.I)
    # prompter part
    text = md
    p_start = re.search(r"^#+\s*\**Prompter Script", text, re.M | re.I)
    t_start = re.search(r"^#+\s*\**Production Script", text, re.M | re.I)
    prompter = []
    if p_start:
        body = text[p_start.end(): t_start.start() if t_start else len(text)]
        for para in body.split("\n"):
            para = clean(para)
            if para and not para.startswith("#"):
                prompter.append(para)
    rows = []
    if t_start:
        for ln in text[t_start.end():].splitlines():
            if not ln.strip().startswith("|"):
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) < 2 or set("".join(cells)) <= set(":- "):
                continue
            spoken, details = clean(cells[0]), clean(" | ".join(cells[1:]))
            if not spoken or "spoken text" in spoken.lower():
                continue
            rows.append({"text": spoken, "details": details, "cues": parse_cues(details)})
    if not rows:  # no table: every prompter paragraph becomes a row without cues
        rows = [{"text": p, "details": "", "cues": {"shot": "talking"}} for p in prompter]
    return {"number": int(num.group(1)) if num else None, "title": title, "prompter": prompter, "rows": rows}


def find(number):
    """Local script file for an episode number (Scripts/Video_12_Script.md, .txt, or anything with 'Video_12')."""
    pats = ["Video_%d_Script.*" % number, "Video_%d_*" % number, "*Video %d*" % number, "*Video_%d.*" % number]
    for p in pats:
        hits = [h for h in glob.glob(os.path.join(SCRIPTS_DIR, p)) if h.lower().endswith((".md", ".txt"))]
        if hits:
            return sorted(hits)[0]
    return None


def load(number_or_path):
    path = number_or_path if isinstance(number_or_path, str) and os.path.exists(number_or_path) else find(int(number_or_path))
    if not path:
        return None, None
    with open(path, encoding="utf-8") as f:
        sc = parse(f.read())
    if sc["number"] is None:
        m = re.search(r"Video_(\d+)", os.path.basename(path))
        sc["number"] = int(m.group(1)) if m else None
    return sc, path


def all_scripts():
    out = []
    for p in sorted(glob.glob(os.path.join(SCRIPTS_DIR, "*.md")) + glob.glob(os.path.join(SCRIPTS_DIR, "*.txt"))):
        sc, _ = load(p)
        if sc and sc["rows"]:
            out.append((p, sc))
    return out


# ---------------------------------------------------------------- Google Drive export

def sync(folder_id, log=print):
    """Export every Video_N_Script Google Doc under the Drive folder to Scripts/*.md (needs `hbb.py auth drive`)."""
    import publish
    drive = publish.drive()
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    folders, docs, seen = [folder_id], [], set()
    while folders:
        fid = folders.pop()
        if fid in seen:
            continue
        seen.add(fid)
        token = None
        while True:
            r = drive.files().list(q="'%s' in parents and trashed = false" % fid, pageToken=token,
                                   fields="nextPageToken, files(id, name, mimeType, modifiedTime)").execute()
            for f in r.get("files", []):
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    if not f["name"].lower().startswith(("_archive", "archive")):
                        folders.append(f["id"])
                elif f["mimeType"] == "application/vnd.google-apps.document" and re.match(r"Video_\d+_Script$", f["name"]):
                    docs.append(f)
            token = r.get("nextPageToken")
            if not token:
                break
    state_path = os.path.join(SCRIPTS_DIR, ".sync.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    changed = []
    for f in docs:
        dest = os.path.join(SCRIPTS_DIR, f["name"] + ".md")
        if state.get(f["id"]) == f["modifiedTime"] and os.path.exists(dest):
            continue
        data = drive.files().export(fileId=f["id"], mimeType="text/markdown").execute()
        with open(dest, "wb") as out:
            out.write(data)
        state[f["id"]] = f["modifiedTime"]
        changed.append(f["name"])
    with open(state_path, "w") as out:
        json.dump(state, out, indent=1)
    if changed:
        log("scripts updated from Drive:", ", ".join(changed))
    return changed


if __name__ == "__main__":
    import sys
    sc, path = load(sys.argv[1])
    print(path)
    print(json.dumps(sc, indent=1, ensure_ascii=False)[:6000])
