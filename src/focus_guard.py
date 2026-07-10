#!/usr/bin/env python3
"""
Focus-away hard stop (the alt-tab safety guard).

THE BUG: start dictating in app A, alt-tab to app B mid-sentence - dum keeps injecting
keystrokes, and B has no text field, so they act as shortcuts/navigation and wreak havoc.

THE BEHAVIOUR (decided): text already typed into A STAYS (never backspaced/reconciled -
commit()'s existing focus guard already skips the reconcile, and the paste path now skips
too); when focus SETTLES on a genuinely different app, dictation hard-stops exactly like a
manual stop (flush-commit + the same "done" cue). No auto-resume - the user re-triggers.

TWO PIECES, both pure enough to unit-test without a mic (test_focus_guard.py):

  * FocusGuard    - the debounced decision core. Fed one focus poll at a time; trips only
                    once focus has stayed away from the session's home app for longer than
                    FOCUS_DEBOUNCE_S, so a momentary flip (mission control, a notification
                    click-through, dum's own tray) can NEVER kill dictation. Polls that
                    read None (osascript hiccup) or name dum's OWN process reset the timer:
                    fail open, keep dictating.
  * FocusWatcher  - the session-scoped poller thread. Runs ONLY while dictation is live
                    (LiveDictation.start() arms it, stop() cancels it), polls
                    platform.frontmost_app() every FOCUS_POLL_S, and calls on_trip() once
                    when the guard fires. It also caches the latest poll in `focus_now`,
                    which is the cheap (no subprocess) focus source the 100ms preview loop
                    uses to HOLD live typing the moment focus looks away - the forward-path
                    protection while the debounce decides between blip and real departure.

Why not reuse activity_monitor's poller: that one only exists when dogfood logging is on
(DUM_DOGFOOD_LOG=1), polls at 1s (too coarse for a ~350ms debounce), and never stops - this
watcher is always-available, fast, and scoped to the dictation session. It starts NO
keyboard listener (pynput stays single-owner in run_double_tap_toggle).

Own-UI exclusion (verified 2026-07-10): a plain `./dum` python process is not an
"application process" to System Events at all (query by unix id errors), so it can never
be reported frontmost; in --tray mode the pystray/AppKit process IS one and reports as
"Python". own_app_names() covers both plus the Windows .exe spellings, so clicking dum's
own tray never reads as a focus departure.

Platforms where frontmost_app() can't name the focused app (supports_app_detection()
False, e.g. the fallback or a bare Wayland session) never arm the watcher - dictation
keeps today's behaviour there.

Env:
    DUM_FOCUS_GUARD=0        disable the focus-away hard stop entirely
    DUM_FOCUS_POLL           poll cadence, seconds (default 0.15)
    DUM_FOCUS_DEBOUNCE       how long focus must stay away before the stop (default 0.35)
"""
import os
import sys
import threading
import time

FOCUS_GUARD_ON = os.environ.get("DUM_FOCUS_GUARD", "1") != "0"
FOCUS_POLL_S = float(os.environ.get("DUM_FOCUS_POLL", 0.15))
FOCUS_DEBOUNCE_S = float(os.environ.get("DUM_FOCUS_DEBOUNCE", 0.35))


def _norm(name):
    """Normalize a frontmost-app name for comparison: case/whitespace-insensitive, and
    Windows process names lose their .exe so "Code.exe" and "code" compare equal."""
    n = (name or "").strip().lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return n


def own_app_names():
    """Process names dum's OWN UI could present as frontmost - excluded so the guard never
    misfires on itself. macOS System Events reports the --tray (AppKit) process as "Python"
    (verified; a headless run has no application process at all); Windows would report the
    interpreter exe. Includes the running interpreter's basename to survive renames."""
    names = {"dum", "python", "python3", "pythonw"}
    exe = _norm(os.path.basename(sys.executable or ""))
    if exe:
        names.add(exe)
    return frozenset(names)


class FocusGuard:
    """Debounced focus-away decision core (pure logic - inject `now` for tests).

    check(app, now) is fed one focus poll at a time and returns True exactly when focus
    has stayed on a different REAL app for at least debounce_s: home / None (unreadable) /
    dum's own UI all reset the away timer, so only a settled departure trips. A tripped
    guard is one-shot by construction (the caller stops dictation)."""

    def __init__(self, home_app, debounce_s=None, own_names=None):
        self.home = _norm(home_app)
        self.debounce_s = FOCUS_DEBOUNCE_S if debounce_s is None else debounce_s
        self.own = frozenset(_norm(x) for x in (own_app_names() if own_names is None
                                                else own_names))
        self._away_since = None

    def check(self, app, now):
        if not self.home:
            return False               # couldn't name the home app - fail open, never trip
        n = _norm(app)
        if not n or n == self.home or n in self.own:
            self._away_since = None    # home, unreadable, or dum itself - not a departure
            return False
        if self._away_since is None:
            self._away_since = now     # first away poll arms the timer, never trips alone
            return False
        return (now - self._away_since) >= self.debounce_s


class FocusWatcher:
    """Session-scoped focus poller: while `running` is set (and not cancelled), poll
    frontmost_fn every poll_s, cache the latest name in `focus_now` (read lock-free by the
    preview loop's typing hold), and call on_trip(app) ONCE when the guard settles on a
    real departure. Daemon thread; cancel() is idempotent and safe from any thread -
    including from inside on_trip itself (LiveDictation.stop() cancels its watcher)."""

    def __init__(self, frontmost_fn, home, on_trip, running,
                 poll_s=None, debounce_s=None, own_names=None):
        self._frontmost = frontmost_fn
        self._on_trip = on_trip
        self._running = running
        self._poll_s = FOCUS_POLL_S if poll_s is None else poll_s
        self._guard = FocusGuard(home, debounce_s=debounce_s, own_names=own_names)
        self._cancel = threading.Event()
        self.focus_now = home          # latest poll (None = unreadable -> holds fail open)
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def cancel(self):
        self._cancel.set()

    def _loop(self):
        while self._running.is_set() and not self._cancel.is_set():
            time.sleep(self._poll_s)
            try:
                app = self._frontmost()
            except Exception:
                app = None             # a failed poll must never kill the watcher
            self.focus_now = app
            if self._guard.check(app, time.monotonic()) and not self._cancel.is_set():
                self._on_trip(app)
                return
