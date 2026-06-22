#!/usr/bin/env python3
"""
Session-level activity monitor — the Layer-2 backbone for the question Step 4 answers:
**after Hovor inserts text, did the user FIX the output, or just CONTINUE working?**

Two cheap, session-wide collectors feed timestamped logs; each commit's observer is a pure reader
that slices them by [t0, t1]. No per-commit OS taps.

  * app poller       — one thread polling frontmost_app() ~1/s, logging app SWITCHES with timestamps.
                       Robust, works in EVERY app, near-zero cost. The reliable signal for
                       "did they stay and edit, or move on to another task?".
  * keystroke proxy  — CONTENT-FREE keystroke categories (backspace/delete/nav/other), NEVER which
                       character, fed in via record_key() from the app's SINGLE keyboard listener.
                       App-attributed via the poller's current app, so edits are scoped to the
                       commit's app. (The monitor deliberately does NOT start its own keyboard
                       listener — two concurrent pynput listeners both query macOS TIS/TSM from
                       different threads and the OS aborts the process. One listener, shared.)

Local-only, fully guarded (never raises), and dependency-injected (frontmost_fn) so it's testable
without a real keyboard or osascript. See DOGFOOD.md for privacy + the on/off switch.
"""
import collections
import threading
import time


class ActivityMonitor:
    def __init__(self, frontmost_fn, poll_s=1.0, keystrokes=True, max_events=50000):
        self._frontmost = frontmost_fn          # () -> app name or None
        self._poll_s = poll_s
        self._keystrokes = keystrokes           # accept keystroke feed via record_key()?
        self._apps = collections.deque(maxlen=max_events)   # (ts, app) appended on CHANGE
        self._keys = collections.deque(maxlen=max_events)   # (ts, category, app_at_press)
        self._self_typing = collections.deque(maxlen=max_events)  # (t0, t1) intervals Hovor was typing
        self._cur_app = None
        self._lock = threading.Lock()
        self._started = False

    # ---- lifecycle ----------------------------------------------------------
    def start(self):
        """Start the app poller ONLY. Keystrokes arrive via record_key() from the app's single
        keyboard listener — the monitor must NOT start a second listener (TIS/TSM abort).
        Idempotent; never raises."""
        if self._started:
            return
        self._started = True
        try:
            self._cur_app = self._frontmost()
        except Exception:
            self._cur_app = None
        with self._lock:
            self._apps.append((time.time(), self._cur_app))
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        while True:
            time.sleep(self._poll_s)
            try:
                app = self._frontmost()
            except Exception:
                app = self._cur_app
            if app != self._cur_app:
                self._cur_app = app
                with self._lock:
                    self._apps.append((time.time(), app))

    # ---- collection: fed by the app's single keyboard listener ---------------
    def record_key(self, category):
        """Record one CONTENT-FREE keystroke category (backspace/delete/nav/other) attributed to the
        current app. Called from the app's single keyboard listener; no-op if keystrokes disabled."""
        if not self._keystrokes:
            return
        with self._lock:
            self._keys.append((time.time(), category, self._cur_app))

    def mark_self_typing(self, t0, t1, pad=0.4):
        """Record an interval during which HOVOR was inserting/reconciling text. Keystrokes in this
        interval are Hovor's own synthetic events (paste Cmd+V, CGEvent typing, overlay
        backspace+retype) and must NOT be counted as user edits. End is padded for async delivery."""
        with self._lock:
            self._self_typing.append((t0, t1 + pad))

    def current_app(self):
        return self._cur_app

    # ---- read: the post-commit interpretation slice -------------------------
    def window(self, t0, t1, commit_app=None):
        """Slice the activity logs for the post-commit window [t0, t1] and return the signals that
        distinguish 'fixed the dictation' from 'moved on':

          app_switches: [{t_rel, app}]   switches during the window (relative to commit)
          final_app:                     app focused at the end of the window
          stayed_in_commit_app: bool     never left the commit's app
          switched_away_s:               t_rel of the FIRST switch away from commit_app (or None)
          keystroke_summary:             content-free counts, GATED to the commit app
        """
        with self._lock:
            apps = list(self._apps)
            keys = list(self._keys)
            self_typing = list(self._self_typing)
        switches = [{"t_rel": round(ts - t0, 2), "app": a} for ts, a in apps if t0 < ts <= t1]
        final_app = commit_app
        for ts, a in apps:
            if ts <= t1:
                final_app = a
        away = next((s["t_rel"] for s in switches if s["app"] != commit_app), None)

        def _hovor_typed(ts):     # was this keystroke Hovor's own insertion/reconcile, not the user's?
            return any(s <= ts <= e for s, e in self_typing)

        ks = collections.Counter()
        for ts, cat, app_at in keys:
            if t0 <= ts <= t1 and (commit_app is None or app_at == commit_app) and not _hovor_typed(ts):
                ks[cat] += 1
        return {
            "app_switches": switches,
            "final_app": final_app,
            "stayed_in_commit_app": away is None,
            "switched_away_s": away,
            "keystroke_summary": {
                "backspaces": ks.get("backspace", 0),
                "deletes": ks.get("delete", 0),
                "nav_keys": ks.get("nav", 0),
                "other_keys": ks.get("other", 0),
            },
        }
