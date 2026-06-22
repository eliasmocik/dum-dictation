#!/usr/bin/env python3
"""
SEAM 3 — correction-event hook.

Surfaces correction events so a CLOSED personalisation agent can observe them later
(it learns from what gets fixed). The free core only emits; it never depends on a
subscriber. Today: in-process callbacks + an optional append-only JSONL sink
(env HOVOR_EVENTS). Later: the same emit() also pushes over a Unix socket the
closed agent connects to — no change to call sites.

Event schema (one JSON object per line; `ts` = epoch seconds, added by emit()):
  {"ts": 1.7e9, "type": "correction.applied", "stage": "<phonetic|llm|external>",
   "before": "...", "after": "..."}
  {"ts": 1.7e9, "type": "commit", "raw": "...", "fixed": "...", "changed": true,
   "surface": "terminal", "app": "Code", "mode": "overlay", "llm": false,
   "n_words": 7}   # one per committed sentence — the end-to-end record for the
                   # future personalisation agent (every commit, corrected or not).
  {"ts": 1.7e9, "type": "user.refix", "before": "...", "after": "..."}  # DEFERRED:
        the user-correction signal. Seam stays defined; not emitted yet (see
        decisions/log.md 2026-06-14) — capture is premature with no consumer built.
"""
import json
import time
from pathlib import Path

EVENT_TYPES = ("correction.applied", "commit", "user.refix")

class EventBus:
    def __init__(self, sink_path=None):
        self.sink_path = sink_path
        self.subscribers = []          # in-process now; IPC (Unix socket) later

    def subscribe(self, fn):
        self.subscribers.append(fn)

    def emit(self, event):
        event.setdefault("ts", time.time())     # uniform timestamp; call sites stay simple
        for fn in self.subscribers:
            try:
                fn(event)
            except Exception:
                pass
        if self.sink_path:
            try:
                Path(self.sink_path).parent.mkdir(parents=True, exist_ok=True)
                with open(self.sink_path, "a") as f:
                    f.write(json.dumps(event) + "\n")
            except Exception:
                pass
