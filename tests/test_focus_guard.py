#!/usr/bin/env python3
"""Focus-away hard stop (the alt-tab guard) - unit + orchestration tests. Headless: no mic,
no audio, no real osascript (a scripted frontmost_fn stands in for the OS).

THE BUG: dictation started in app A keeps injecting keystrokes after an alt-tab to app B -
B has no text field, so they act as shortcuts/navigation. Decided behaviour (option c):
text already typed into A STAYS (never backspaced), a SETTLED focus change hard-stops
dictation exactly like a manual stop (same "done" cue), a sub-debounce blip does nothing,
and platforms that can't name the focused app keep current behaviour.

THREE LAYERS TESTED
  (1) FocusGuard   - the pure debounce core (injected clock, no threads).
  (2) FocusWatcher - the session poller thread (scripted frontmost_fn, real threads).
  (3) LiveDictation wiring - _start_focus_watch / _typing_focus_ok / _commit_insert_ok and
      the trip -> stop() path, on a fake Platform: the stop is the SAME manual-stop path
      (running cleared + "done" cue) and it types/erases NOTHING.

Run standalone: PYTHONPATH=src .venv/bin/python tests/test_focus_guard.py
"""
import threading
import time

import focus_guard
from focus_guard import FocusGuard, FocusWatcher, own_app_names

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"ok  {msg}")


# --------------------------------------------------------------------------------------------------
# (1) FocusGuard - the pure debounce core. Time is injected, so the debounce math is exact.
# --------------------------------------------------------------------------------------------------
def test_guard_core():
    print("== (1) FocusGuard: debounced decision core ==")
    DB = 0.35

    g = FocusGuard("Terminal", debounce_s=DB)
    check(not any(g.check("Terminal", t / 10.0) for t in range(20)),
          "same app forever -> never trips")

    g = FocusGuard("Terminal", debounce_s=DB)
    check(not g.check("Safari", 0.0), "first away poll only ARMS the timer (never trips alone)")
    check(not g.check("Safari", 0.2), "still inside the debounce window -> no trip")
    check(g.check("Safari", 0.4), "settled away past the debounce -> trips")

    g = FocusGuard("Terminal", debounce_s=DB)
    g.check("Safari", 0.0)
    g.check("Terminal", 0.2)               # the blip ends - back home resets the timer
    check(not g.check("Safari", 0.3), "a blip that returned home reset the timer (re-arms fresh)")
    check(not g.check("Safari", 0.5), "0.2s after re-arm -> still no trip")
    check(g.check("Safari", 0.7), "second departure settles -> trips on ITS OWN debounce")

    g = FocusGuard("Terminal", debounce_s=DB)
    g.check("Safari", 0.0)
    g.check(None, 0.2)                     # unreadable poll (osascript hiccup) - fail open
    check(not g.check("Safari", 0.4), "a None poll resets the timer (fail open, no trip at 0.4)")

    g = FocusGuard("Terminal", debounce_s=DB)
    g.check("Safari", 0.0)
    check(g.check("Mail", 0.4), "flipping between DIFFERENT away apps still counts as away")

    g = FocusGuard(None, debounce_s=DB)
    check(not g.check("Safari", 9.9), "unknown home app -> guard never trips (fail open)")


def test_guard_own_ui():
    print("\n== (1b) FocusGuard: dum's own UI never reads as a departure ==")
    names = own_app_names()
    check("python" in names and "dum" in names,
          f"own_app_names covers the tray process ({sorted(names)})")
    g = FocusGuard("Terminal", debounce_s=0.1)
    # macOS System Events reports the --tray (AppKit/pystray) python as "Python" (verified);
    # Windows would report the interpreter exe. None of these may arm the timer.
    for own in ("Python", "python", "pythonw.exe", "Dum", "DUM"):
        check(not g.check(own, 0.0) and not g.check(own, 5.0),
              f"own-UI frontmost {own!r} never trips (and resets the timer)")
    g.check("Safari", 10.0)
    g.check("Python", 10.2)                # tray click mid-departure resets, like home
    check(not g.check("Safari", 10.4), "own-UI poll between away polls resets the debounce")

    g = FocusGuard("Code.exe", debounce_s=0.1)
    check(not g.check("code", 0.0) and not g.check("Code.exe", 1.0),
          "'.exe' is normalized - 'Code.exe' home matches 'code'")


# --------------------------------------------------------------------------------------------------
# (2) FocusWatcher - the session poller thread, with a scripted frontmost_fn. Real threads and
# real (tiny) sleeps, like test_early_stop.
# --------------------------------------------------------------------------------------------------
def _watcher(front, running, trips, poll_s=0.01, debounce_s=0.05):
    return FocusWatcher(front, "Terminal", trips.append, running,
                        poll_s=poll_s, debounce_s=debounce_s).start()


def _wait(pred, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def test_watcher():
    print("\n== (2) FocusWatcher: poller thread trips once on a settled change ==")
    holder = {"app": "Terminal"}
    running = threading.Event(); running.set()
    trips = []
    w = _watcher(lambda: holder["app"], running, trips)
    time.sleep(0.1)
    check(not trips, "same app -> no trip")
    check(w.focus_now == "Terminal", "focus_now caches the latest poll")

    holder["app"] = "Safari"
    check(_wait(lambda: trips), "settled focus change -> on_trip fires")
    time.sleep(0.1)
    check(trips == ["Safari"], f"on_trip fired exactly ONCE, with the new app ({trips})")
    check(w.focus_now == "Safari", "cache followed the change (the typing hold reads this)")
    running.clear()

    # a blip shorter than the debounce never trips
    holder = {"app": "Terminal"}
    running = threading.Event(); running.set()
    trips = []
    w = _watcher(lambda: holder["app"], running, trips, poll_s=0.01, debounce_s=0.2)
    time.sleep(0.05)
    holder["app"] = "Safari"
    time.sleep(0.05)                        # away well under the 0.2s debounce
    holder["app"] = "Terminal"
    time.sleep(0.3)                         # past the debounce horizon - would have tripped
    check(not trips, "a sub-debounce blip does NOT stop dictation")
    running.clear()
    check(_wait(lambda: not w._thread.is_alive()), "watcher thread exits when running clears")

    # cancel() retires a watcher even while running stays set (stale-watcher safety)
    holder = {"app": "Terminal"}
    running = threading.Event(); running.set()
    trips = []
    w = _watcher(lambda: holder["app"], running, trips)
    w.cancel()
    holder["app"] = "Safari"
    time.sleep(0.15)
    check(not trips and not w._thread.is_alive(),
          "a cancelled watcher never trips (survives stop->restart with the shared Event)")

    # a raising frontmost_fn must never kill the watcher (treated as an unreadable poll)
    running = threading.Event(); running.set()
    trips = []
    w = FocusWatcher(lambda: 1 / 0, "Terminal", trips.append, running,
                     poll_s=0.01, debounce_s=0.05).start()
    time.sleep(0.1)
    check(w._thread.is_alive() and not trips and w.focus_now is None,
          "raising frontmost_fn -> None polls, no trip, watcher alive (fail open)")
    running.clear()


# --------------------------------------------------------------------------------------------------
# (3) LiveDictation wiring - fake Platform, no mic (running/worker driven by hand, like the
# state test_tray drives). The trip path must BE the manual-stop path and type nothing.
# --------------------------------------------------------------------------------------------------
from platform_base import Platform
from overlay import OverlayTyper
from live import LiveDictation


class FakePlatform(Platform):
    """Scripted focus + recorded side effects; no OS calls."""
    def __init__(self, app="Terminal", supports=True):
        self.app = app
        self.supports = supports
        self.notified = []
        self.pasted = []

    def paste(self, text):
        self.pasted.append(text)

    def notify(self, event):
        self.notified.append(event)

    def frontmost_app(self):
        return self.app

    def supports_app_detection(self):
        return self.supports


def _app(platform):
    # rec/pipe/bus are untouched on the paths under test (no audio flows)
    return LiveDictation(None, None, None, do_paste=True, platform=platform, overlay=False)


def test_live_wiring():
    print("\n== (3) LiveDictation wiring: trip == manual stop; nothing typed or erased ==")
    # shrink the module-level defaults the watcher falls back to, so the test runs fast
    saved = focus_guard.FOCUS_POLL_S, focus_guard.FOCUS_DEBOUNCE_S
    focus_guard.FOCUS_POLL_S, focus_guard.FOCUS_DEBOUNCE_S = 0.01, 0.05
    try:
        plat = FakePlatform("Terminal")
        app = _app(plat)
        # arm the session by hand (start() would open a real mic; stop() handles worker=None)
        app.running.set()
        app._start_focus_watch()
        check(app._focus_watch is not None, "watcher armed when the platform names apps")

        # already-typed text: a dry overlay stands in for what dum put on screen in app A
        typer = OverlayTyper(dry=True, quiet=True)
        typer.append_words("deploy the new build".split())
        ops_before = list(typer.ops)

        time.sleep(0.1)
        check(app.running.is_set(), "same app -> still listening (no false stop)")

        plat.app = "Safari"                       # the alt-tab, settled
        check(_wait(lambda: not app.running.is_set()), "settled focus change -> dictation STOPPED")
        check(plat.notified == ["done"], f"the trip played the manual-stop cue ({plat.notified})")
        check(app._focus_watch is None, "stop() retired the watcher (no stale thread)")
        check(typer.ops == ops_before and typer.typed == "deploy the new build",
              "focus-away stop sent ZERO keystrokes - typed text stays untouched")
        check(not plat.pasted, "nothing was pasted into the newly focused app")

        # no auto-resume: only an explicit re-trigger starts again
        time.sleep(0.1)
        check(not app.running.is_set(), "no auto-resume after focus settles elsewhere")
    finally:
        focus_guard.FOCUS_POLL_S, focus_guard.FOCUS_DEBOUNCE_S = saved


def test_live_blip_and_typing_hold():
    print("\n== (3b) forward-path hold + blip survival on the live object ==")
    saved = focus_guard.FOCUS_POLL_S, focus_guard.FOCUS_DEBOUNCE_S
    focus_guard.FOCUS_POLL_S, focus_guard.FOCUS_DEBOUNCE_S = 0.01, 0.2
    try:
        plat = FakePlatform("Terminal")
        app = _app(plat)
        app.running.set()
        app._start_focus_watch()

        check(app._typing_focus_ok("Terminal"), "same app -> live typing keeps flowing")
        plat.app = "Safari"
        check(_wait(lambda: app._focus_watch.focus_now == "Safari"), "cache saw the flip")
        check(not app._typing_focus_ok("Terminal"),
              "typing HOLDS the moment the cached poll looks away (no stray keystrokes)")
        time.sleep(0.05)                          # sub-debounce blip...
        plat.app = "Terminal"
        check(_wait(lambda: app._focus_watch.focus_now == "Terminal"), "cache saw the return")
        check(app._typing_focus_ok("Terminal"), "typing resumes after the blip")
        time.sleep(0.3)                           # well past the debounce horizon
        check(app.running.is_set(), "the blip did NOT stop dictation")

        # commit-time fresh check (overlay reconcile skip + paste skip share it)
        check(app._commit_insert_ok("Terminal"), "commit insert allowed when focus is home")
        plat.app = "Safari"
        check(not app._commit_insert_ok("Terminal"), "commit insert blocked when focus moved")
        check(app._commit_insert_ok(None), "unknown onset app -> current behaviour (allow)")
        app.running.clear()
        app._focus_watch.cancel()
    finally:
        focus_guard.FOCUS_POLL_S, focus_guard.FOCUS_DEBOUNCE_S = saved


def test_live_unsupported_platform():
    print("\n== (3c) supports_app_detection()=False -> current behaviour, guard fully off ==")
    plat = FakePlatform("whatever", supports=False)
    app = _app(plat)
    app.running.set()
    app._start_focus_watch()
    check(app._focus_watch is None, "no watcher on a platform that can't name apps")
    check(app._typing_focus_ok(None) and app._typing_focus_ok("X"),
          "typing is never held without a watcher (fallback = today's behaviour)")
    check(app._commit_insert_ok(None), "commit path unchanged when the app can't be named")
    app.running.clear()

    # DUM_FOCUS_GUARD=0 kill switch (live.py reads its own imported copy of the flag)
    import live
    saved = live.FOCUS_GUARD_ON
    try:
        live.FOCUS_GUARD_ON = False
        plat = FakePlatform("Terminal")
        app = _app(plat)
        app.running.set()
        app._start_focus_watch()
        check(app._focus_watch is None, "DUM_FOCUS_GUARD=0 disables the watcher entirely")
        app.running.clear()
    finally:
        live.FOCUS_GUARD_ON = saved


if __name__ == "__main__":
    test_guard_core()
    test_guard_own_ui()
    test_watcher()
    test_live_wiring()
    test_live_blip_and_typing_hold()
    test_live_unsupported_platform()
    print(f"\nALL {passed} CHECKS PASSED")
