#!/usr/bin/env python3
"""
SAFETY GATE for constrained fuzzy symbol recovery (HOVOR_FUZZY_SYMBOLS feature).

Locks the recovery rule's safety properties so a future broadening can't silently start
over-correcting prose. The benchmark (fuzzy_recovery_spike.py) measures yield on real audio;
THIS asserts the invariants deterministically + the FuzzySymbolStage flag gating.
Run: .venv/bin/python test_fuzzy_recover.py
"""
import os
from fuzzy_recover import recover, build_index
from pipeline import FuzzySymbolStage

PAIRS = [
    (["find", "model", "dir"], "find_model_dir"),
    (["min", "edit", "script"], "min_edit_script"),
    (["clean", "punct"], "clean_punct"),
    (["web", "socket"], "WebSocket"),
    (["engine", "x"], "nginx"),
    (["data", "model"], "DataModel"),
]
IDX = build_index(PAIRS)
passed = 0


def first(text):
    return recover(text, PAIRS, index=IDX)[0]


# POSITIVE: near-misses that resolve to a known multi-word symbol -> recovered
POS = [
    ("Find model deer returns the path.", "find_model_dir returns the path."),
    ("the overlay applies the min edited script.", "the overlay applies the min_edit_script."),
    ("clean punked runs here", "clean_punct runs here"),
]
# NEGATIVE: must stay UNTOUCHED — the safety invariants
NEG = [
    "I saw a deer in the forest",              # standalone 'deer' — no anchor, never single-word
    "open a web socket now",                   # exact match -> that's the regular alias's job
    "the engine is down so restart",           # 'engine' alone ('engine x' needs the 'x' anchor)
    "move the needle on this project",         # ordinary prose
    "what are the most important things",      # ordinary prose
    "the data model is complex",               # exact 2-common-words alias present but EXACT -> skip
    "i need to model the deer anatomy",        # 'model'+'deer' but not the 'find model dir' window
]
for src, want in POS:
    got = first(src)
    assert got == want, f"FAIL pos\n  in : {src!r}\n got: {got!r}\n want: {want!r}"
    passed += 1
    print(f"ok  recover {src!r} -> {got!r}")
for src in NEG:
    got = first(src)
    assert got == src, f"FAIL neg (fired on prose/standalone!)\n  in : {src!r}\n got: {got!r}"
    passed += 1
    print(f"ok  untouched {src!r}")

# FuzzySymbolStage: OFF by default, ON only with HOVOR_FUZZY_SYMBOLS=1
stage = FuzzySymbolStage(PAIRS)
os.environ.pop("HOVOR_FUZZY_SYMBOLS", None)
out, _ = stage.run("Find model deer returns the path.", {})
assert out == "Find model deer returns the path.", f"stage must be OFF by default, got {out!r}"
passed += 1
print("ok  FuzzySymbolStage OFF by default (no recovery)")
os.environ["HOVOR_FUZZY_SYMBOLS"] = "1"
out, _ = stage.run("Find model deer returns the path.", {})
assert out == "find_model_dir returns the path.", f"stage must be ON with flag, got {out!r}"
passed += 1
print("ok  FuzzySymbolStage ON with HOVOR_FUZZY_SYMBOLS=1")
os.environ.pop("HOVOR_FUZZY_SYMBOLS", None)

print(f"\nALL {passed} CHECKS PASSED")
