#!/bin/bash
# One-time setup on the Mac: Python environment for the HBB pipeline.
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "ffmpeg is missing: brew install ffmpeg"; exit 1; }
# the Mac's own Python 3.9 (as TNP): Homebrew's newest Python is often too new for faster-whisper's av package
PY=/usr/bin/python3
[ -x "$PY" ] || PY=python3
"$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p ../secrets ../logs ../work
chmod 700 ../secrets
echo "OK. Next: Guides/Setup Steps.md step 2."
