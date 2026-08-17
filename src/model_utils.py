#!/usr/bin/env python3
"""
Shared model + scoring helpers used by the live engine and the bench.

Locates the downloaded model directory under models/ and picks the encoder/decoder/joiner
ONNX files inside it, plus small WAV/normalization/term-scoring utilities reused by the
regression bench.
"""
import glob
import os
import re
import string
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

# --- the two roots -----------------------------------------------------------------
# There are TWO kinds of path, and conflating them breaks a bundled .app:
#
#   HERE      SHIPPED, READ-ONLY resources (packs/ (incl. terms.txt), tests/). From a checkout that's
#             the repo root, one level above src/. Frozen into a .app, PyInstaller unpacks them
#             beside the bundled modules and points sys._MEIPASS at that directory - so honour
#             _MEIPASS first, or every lookup lands one level too high (Contents/ instead of
#             Contents/Frameworks/).
#
#   USER_DATA WRITABLE state (models, and later logs/telemetry). This may NEVER live inside a
#             signed .app: writing into the bundle invalidates its code signature, which costs
#             both Gatekeeper approval and - because macOS keys TCC grants to the signature -
#             the user's Microphone/Accessibility/Input Monitoring permissions. Frozen, it's
#             ~/.dum (already this app's state dir: config.json, dum.lock live there, so there
#             is nothing to migrate and one `rm -rf ~/.dum` uninstalls cleanly). From a checkout
#             it stays the repo root, so ./setup, scripts/test, the bench and --replay are
#             completely unaffected.
#
# Overrides, in both modes: DUM_DATA_DIR (all writable state) and DUM_MODELS_DIR (models only).
# .resolve() on the roots is deliberate: it canonicalises away the `../` a caller introduces by
# putting src/ on the path relatively (the tests do exactly that), which keeps "is this path
# inside the bundle?" answerable by a prefix comparison - the check that stops writable state
# landing in a signed .app. autostart_base.py resolves its REPO_ROOT for the same reason.
FROZEN = bool(getattr(sys, "frozen", False))
HERE = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).parent.parent).resolve()
USER_DATA = Path(os.environ.get("DUM_DATA_DIR")
                 or (Path.home() / ".dum" if FROZEN else HERE)).resolve()
MODELS = Path(os.environ.get("DUM_MODELS_DIR") or (USER_DATA / "models")).resolve()


def find_model_dir(pattern):
    hits = sorted(glob.glob(str(MODELS / pattern)))
    hits = [h for h in hits if os.path.isdir(h)]
    if not hits:
        sys.exit(f"[!] model dir not found: {pattern} (under {MODELS}). Did ./setup finish?")
    return Path(hits[0])


def pick(d, *prefixes, prefer_int8=False):
    """Find encoder/decoder/joiner onnx in a model dir, preferring float (or int8)."""
    for pre in prefixes:
        cands = sorted(glob.glob(str(d / f"{pre}*.onnx")))
        if not cands:
            continue
        int8 = [c for c in cands if ".int8." in c]
        flt = [c for c in cands if ".int8." not in c]
        chosen = (int8 or flt) if prefer_int8 else (flt or int8)
        return chosen[0]
    sys.exit(f"[!] no onnx matching {prefixes} in {d}")


def load_wav(path):
    audio, sr = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        # simple linear resample to 16k
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio), n, endpoint=False),
                          np.arange(len(audio)), audio).astype("float32")
        sr = 16000
    return audio, sr


PUNC = str.maketrans("", "", string.punctuation)


def norm(t):
    return " ".join(t.lower().translate(PUNC).split())


def score_terms(hyp, terms):
    h = norm(hyp)
    found, missed = [], []
    for term in terms:
        if re.search(rf"\b{re.escape(norm(term))}\b", h):
            found.append(term)
        else:
            missed.append(term)
    return found, missed
