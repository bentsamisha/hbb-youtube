#!/bin/bash
# One-time setup on the Mac: Python environment for the HBB pipeline.
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "ffmpeg is missing: brew install ffmpeg"; exit 1; }
ffmpeg -hide_banner -filters 2>/dev/null | grep -q " ass " || echo "WARNING: this ffmpeg has no libass (captions/graphics). Install with: brew install ffmpeg"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p ../secrets ../logs ../work
chmod 700 ../secrets
echo "OK. Next: Guides/Setup Steps.md step 2."
