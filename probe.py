#!/usr/bin/env python3
"""
G0 mechanism probe.

Replays a WAV through the REAL live.py pipeline — current HARDCODED aliases, NO LLM,
NO vocab pack — and prints, per committed sentence, the RAW transcript vs the FIXED
(post-phonetic-correction) output. The point: read with your own eyes whether phrase
aliases (engine x->nginx, cube control->kubectl, local host->localhost) fire on real
mic audio, and whether prose / near-miss controls survive untouched.

This is test scaffolding, not product code — it ships nothing into the tool.

Usage:
    .venv/bin/python probe.py <wav>
    # to also test loading from a pack instead of hardcoded (G1a re-prove):
    DUM_VOCAB_DIR=/path/to/pack .venv/bin/python probe.py <wav>
"""
import os, sys, json
os.environ.setdefault("DUM_OVERLAY_QUIET", "1")   # silence dry-overlay keystroke logs
from pathlib import Path

from live import (build_parakeet, find_model_dir, build_pipeline, load_terms,
                  LiveDictation, Tracer, EventBus, HERE)


def main():
    if len(sys.argv) < 2:
        print("usage: .venv/bin/python probe.py <wav>")
        return 1
    wav = sys.argv[1]
    if not Path(wav).exists():
        print(f"no such wav: {wav}")
        return 1

    ev_path = Path("/tmp/dum_probe.events.jsonl")
    ev_path.unlink(missing_ok=True)

    # picks up DUM_VOCAB_DIR if set (None -> falls through to the env var in load_terms)
    terms = load_terms([HERE / "terms.txt"], None)
    print(f"loaded {len(terms)} IT terms"
          + (f"  (+pack {os.environ['DUM_VOCAB_DIR']})" if os.environ.get("DUM_VOCAB_DIR") else "  (no pack)"))
    print("building model (once)...")
    rec = build_parakeet(find_model_dir("sherpa-onnx-nemo-parakeet-tdt-*"))
    pipe = build_pipeline(terms)
    bus = EventBus(str(ev_path))
    app = LiveDictation(rec, pipe, bus, do_paste=False, use_llm=False,
                        terms=terms, overlay=True, tracer=Tracer(None))
    app.replay(wav, realtime=False)

    rows = []
    for line in ev_path.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("type") == "commit":
            rows.append((e.get("raw", ""), e.get("fixed", "")))

    print("\n" + "=" * 78)
    print("G0 PROBE — raw (recognizer) vs fixed (after hardcoded phonetic aliases)")
    print("=" * 78)
    changed = 0
    for i, (raw, fixed) in enumerate(rows, 1):
        mark = "  CHANGED" if fixed.strip() != raw.strip() else ""
        if mark:
            changed += 1
        print(f"\n[{i}]{mark}")
        print(f"  raw  : {raw.strip()}")
        print(f"  fixed: {fixed.strip()}")
    print("\n" + "-" * 78)
    print(f"{len(rows)} sentences, {changed} changed by the correction layer.")
    print("Read the FIXED column: did the aliases land, and are the controls untouched?")
    print("If an alias did NOT fire, the RAW column shows the true spoken form to alias on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
