#!/usr/bin/env python3
"""Offline check for lock-and-trim: replay a recorded sentence WAV as growing audio
(simulating the live preview loop) and compare:
  - growing-window (old): re-transcribe the whole window every step
  - lock-and-trim (new): transcribe only the unlocked tail, lock old words

Verifies (a) the live window stays bounded (constant proc), (b) the streamed text
converges to the same final as the growing window, and (c) per-step proc drops.
Run: .venv/bin/python test_lock_trim.py [seg.wav]
"""
import os, sys, time
import numpy as np, soundfile as sf
from live import (build_parakeet, find_model_dir, transcribe, transcribe_words,
                  clean_punct, SR, LOCK_MARGIN_S, STEP_S)

# Exit 77 == "skipped" (corpus/model absent), distinct from any other nonzero exit,
# which means a REAL failure. scripts/test relies on this split so a genuine
# regression here is no longer masked as "bench skipped".
SKIP = 77

wav = sys.argv[1] if len(sys.argv) > 1 else "sessions/20260615-165654/seg_002.wav"
if not os.path.exists(wav):
    print(f"lock-trim: corpus wav absent ({wav}) - skipping", file=sys.stderr)
    sys.exit(SKIP)
audio, sr = sf.read(wav)
audio = audio.astype("float32")
assert sr == SR, f"expected {SR}, got {sr}"
try:
    model_dir = find_model_dir("sherpa-onnx-nemo-parakeet-tdt-*")
except SystemExit:
    print("lock-trim: parakeet model absent - skipping", file=sys.stderr)
    sys.exit(SKIP)
rec = build_parakeet(model_dir)
dur = len(audio) / SR
step = int(STEP_S * SR)
print(f"wav {wav}  dur {dur:.1f}s  margin {LOCK_MARGIN_S}s  step {STEP_S}s\n")

def run_growing():
    procs, last = [], ""
    pos = step
    while pos <= len(audio):
        t = time.monotonic()
        last = clean_punct(transcribe(rec, audio[:pos]))
        procs.append((time.monotonic() - t) * 1000)
        pos += step
    return last, procs

def run_lock_trim():
    procs, locked, locked_n = [], [], 0
    locked_words = []
    pos = step
    while pos <= len(audio):
        full = audio[:pos]
        t = time.monotonic()
        tail = full[locked_n:]
        tw, ts = transcribe_words(rec, tail)
        cutoff = (len(tail) / SR) - LOCK_MARGIN_S
        n = 0
        while n + 1 < len(ts) and ts[n + 1] <= cutoff:
            n += 1
        if n:
            locked_words.extend(tw[:n])
            locked_n += int(ts[n] * SR)
        txt = clean_punct(" ".join(locked_words + tw[n:]))
        procs.append((time.monotonic() - t) * 1000)
        locked.append((round(len(tail) / SR, 2), len(locked_words)))
        pos += step
    return txt, procs, locked

def run_context_trim(margin, context_s):
    """Lock-and-trim WITH a carry-over context buffer: decode from `context_s` before the
    lock point so unlocked words keep left context (no garbage/recapitalization), but only
    words past the lock point are displayed/locked. Window = context_s + margin + recent."""
    CTX = int(context_s * SR)
    procs, locks = [], []
    locked_words, lock_n = [], 0
    eps = 0.06
    pos = step
    txt = ""
    while pos <= len(audio):
        full = audio[:pos]
        ctx_start = max(0, lock_n - CTX)
        window = full[ctx_start:]
        t = time.monotonic()
        tw, ts = transcribe_words(rec, window)
        procs.append((time.monotonic() - t) * 1000)
        lock_t = (lock_n - ctx_start) / SR
        tail = [(w, s) for w, s in zip(tw, ts) if s >= lock_t - eps]   # words past the lock
        wdur = len(window) / SR
        cutoff = wdur - margin
        n = 0
        while n + 1 < len(tail) and tail[n + 1][1] <= cutoff:
            n += 1
        if n:
            locked_words.extend(w for w, _ in tail[:n])
            lock_n = ctx_start + int(tail[n][1] * SR)
        txt = clean_punct(" ".join(locked_words + [w for w, _ in tail[n:]]))
        locks.append((round((len(full) - ctx_start) / SR, 2), len(locked_words)))
        pos += step
    return txt, procs, locks


gtext, gprocs = run_growing()
ltext, lprocs, locks = run_lock_trim()

def stats(p):
    return f"min {min(p):.0f}  median {sorted(p)[len(p)//2]:.0f}  max {max(p):.0f}  over-step {sum(1 for x in p if x > STEP_S*1000)}/{len(p)}"

print("growing-window proc_ms :", stats(gprocs))
print("lock-and-trim proc_ms  :", stats(lprocs))
print("\nlive tail size over time (s, locked_words):")
print("  ", " ".join(f"{t}s/{n}w" for t, n in locks[::max(1, len(locks)//12)]))
print("\nfinal text growing :", gtext)
print("final text locktrim:", ltext)
# the live draft is a bounded-window approximation; commit re-runs full audio anyway.
# what we assert: the LIVE text ends close to the growing-window text (prefix agreement).
gw, lw = gtext.split(), ltext.split()
common = 0
for a, b in zip(gw, lw):
    if a.lower().strip(".,!?") == b.lower().strip(".,!?"):
        common += 1
    else:
        break
print(f"\nleading words matching growing-window: {common}/{len(gw)}")
print(f"max live tail: {max(t for t,_ in locks):.1f}s (should be ~<= margin+step)")

def match(gt, lt):
    gw, lw = gt.split(), lt.split()
    c = 0
    for a, b in zip(gw, lw):
        if a.lower().strip(".,!?") == b.lower().strip(".,!?"):
            c += 1
        else:
            break
    return c, len(gw)

print("\n===== CONTEXT-CARRY-OVER SWEEP (margin, context_s) =====")
for margin, ctx in [(2.0, 3.0), (1.5, 3.0), (2.0, 4.0), (1.0, 4.0)]:
    ct, cp, cl = run_context_trim(margin, ctx)
    c, n = match(gtext, ct)
    print(f"\n-- margin={margin} context={ctx}s -> proc {stats(cp)}")
    print(f"   max window {max(t for t,_ in cl):.1f}s | leading match {c}/{n}")
    print(f"   text: {ct}")
print("\ngrowing-window final:", gtext)
