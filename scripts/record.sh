#!/usr/bin/env bash
# Record a 16 kHz mono WAV from the default mic via ffmpeg (avfoundation).
# Usage: scripts/record.sh [output.wav] [seconds]
#   defaults: recordings/test.wav, 45 seconds
# If ":default" fails, list devices with:
#   ffmpeg -f avfoundation -list_devices true -i ""
# then use the audio index, e.g. -i ":1"
set -euo pipefail
cd "$(dirname "$0")/.."
out="${1:-recordings/test.wav}"
secs="${2:-45}"
mkdir -p "$(dirname "$out")"
echo "Recording ${secs}s -> ${out}"
echo "Open script_to_read.md and START SPEAKING IMMEDIATELY."
echo
ffmpeg -hide_banner -loglevel warning -f avfoundation -i ":default" \
       -ar 16000 -ac 1 -t "$secs" -y "$out"
echo
echo "Saved ${out}. Now run:  .venv/bin/python bakeoff.py --wav ${out}"
