#!/usr/bin/env python3
"""
Latency tracer — the instrumentation behind `live.py --trace`.

Writes one JSON object per line with a high-resolution monotonic timestamp (`t`,
seconds since the tracer was created) so a recorded session can be decomposed into
where every millisecond goes: speech onset, each preview re-transcribe, each word
lock, and the per-commit breakdown (silence-gate -> transcribe -> LLM -> paste).

Designed to be a no-op when `path` is None, so the same call sites stay in the hot
loop with zero cost in normal runs. `wall` (epoch seconds) is included on every
event so the trace can be aligned against the ffmpeg screen/mic recording.
"""
import json
import time


class Tracer:
    def __init__(self, path=None):
        self.t0 = time.monotonic()
        self.wall0 = time.time()
        self._f = open(path, "a", buffering=1) if path else None

    def ev(self, kind, **kw):
        if not self._f:
            return
        kw["ev"] = kind
        kw["t"] = round(time.monotonic() - self.t0, 4)
        kw["wall"] = round(time.time(), 4)
        self._f.write(json.dumps(kw) + "\n")

    def close(self):
        if self._f:
            try:
                self.ev("end")
                self._f.close()
            except Exception:
                pass
            self._f = None
