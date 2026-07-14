#!/usr/bin/env python3
"""STT smoke test: prove the speech-to-text recognizer actually turns audio into
the right words. This is the CI gate that catches a broken model / model layout /
sherpa-onnx break - the thing a mic-less pipeline unit test can't see.

It decodes ONE small committed WAV (tests/assets/stt_en_16k.wav - a public sample
clip resampled to 16 kHz, no personal voice) straight through Parakeet and asserts
the word error rate against the known reference stays low. Pure recognizer decode:
no VAD, no correction pipeline, so a failure points squarely at STT.

The Parakeet model (~480 MB) is NOT in the repo - it's downloaded by ./setup and,
in CI, by a cached download step. If the model isn't present this test SKIPS (exit
0) so a fresh clone running `scripts/test --unit` without models still goes green.
Run: PYTHONPATH=src .venv/bin/python tests/test_stt_smoke.py
"""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from live import build_parakeet, find_model_dir, transcribe, clean_punct, SR
from model_utils import HERE

# WER tolerance: this exact clip decodes cleanly (0% today); allow a small margin so
# a benign model/library patch that nudges a token doesn't red the build, while a real
# regression (wrong/empty output) still trips it.
MAX_WER = 15.0

ASSET = HERE / "tests" / "assets" / "stt_en_16k.wav"
REF = HERE / "tests" / "assets" / "stt_en_16k.ref.txt"


def _wer(ref, hyp):
    """Word error rate (%) via jiwer if available, else a dependency-free
    Levenshtein-on-words fallback (keeps this test runnable without jiwer)."""
    def norm(t):
        return "".join(c for c in t.lower() if c.isalnum() or c.isspace()).split()
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0 if not h else 100.0
    try:
        import jiwer
        return jiwer.wer(reference=" ".join(r), hypothesis=" ".join(h)) * 100.0
    except Exception:
        pass
    # classic edit distance over word lists
    dp = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, len(h) + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return dp[len(h)] / len(r) * 100.0


def main():
    try:
        model_dir = find_model_dir("sherpa-onnx-nemo-parakeet-tdt-*")
    except SystemExit:
        model_dir = None
    if model_dir is None or not Path(model_dir).exists():
        print("[skip] STT smoke: Parakeet model not present "
              "(run ./setup, or the CI model-download step). Nothing to test.")
        return 0
    if not ASSET.exists() or not REF.exists():
        print(f"[skip] STT smoke: asset missing ({ASSET.name}/{REF.name}).")
        return 0

    audio, sr = sf.read(str(ASSET), dtype="float32")
    if audio.ndim > 1:
        audio = audio[:, 0]
    assert sr == SR, f"asset must be {SR} Hz mono; got {sr} Hz"

    rec = build_parakeet(model_dir)
    hyp = clean_punct(transcribe(rec, audio))
    ref = REF.read_text().strip()

    wer = _wer(ref, hyp)
    print(f"  ref: {ref}")
    print(f"  hyp: {hyp}")
    print(f"  WER: {wer:.1f}%  (max allowed {MAX_WER}%)")

    assert hyp.strip(), "STT produced EMPTY output - recognizer is broken"
    assert wer <= MAX_WER, f"STT WER {wer:.1f}% exceeds {MAX_WER}% - recognition regressed"
    print("\nSTT SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
