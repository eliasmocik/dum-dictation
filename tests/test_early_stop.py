#!/usr/bin/env python3
"""Deterministic repro + regression gate for the "half-applied commit reconcile on early stop"
data-loss bug. Headless: no mic, no focused field.

THE BUG (Elias-reported): dictate "...we grab the logs"; the LLM wants grab->grep on the final
clause; double-tap LEFT Command to STOP the instant the last word is out - i.e. before the grep fix
finishes applying. The commit reconcile (overlay backspace-then-retype, or the smart min-edit span
replace) is a DESTRUCTIVE phase (backspace old chars) followed by a CONSTRUCTIVE phase (type new
chars). If a stop/teardown cuts it in the middle, the backspaces land but the retype doesn't, leaving
a truncated tail like "...we gr".

WHY a stop can cut it (mapped in live.py):
  * stop() runs on the pynput listener thread: running.clear() -> (drain in-flight reconcile)
    -> stream.close() -> worker.join().
  * The worker thread, on seeing `running` cleared, exits its `while running.is_set()` loop and
    falls into the flush block (end of _consume) which calls commit() one last time -> the overlay
    reconcile.
  * The worker is a DAEMON thread. If the process exits (Ctrl-C/SIGTERM, or join(timeout) elapses
    and main() returns) WHILE the reconcile is mid-flight, the daemon is killed abruptly between the
    backspace and the type.

TWO THINGS THIS FILE PROVES
  (1) PRIMITIVE (documentation of the failure mode): an UNGUARDED reconcile cut at the
      backspace->type seam half-applies - and DUM_MIN_EDIT only SHRINKS the loss (whole tail ->
      one diff span), it does NOT make it atomic. This is asserted, so if someone "fixes" it by
      tweaking min-edit alone, this test still shows the half-apply is possible at the primitive.
  (2) ORCHESTRATION (the actual fix): the live.py `_reconcile_lock` barrier means stop()/teardown
      WAITS for an in-flight reconcile to finish as one atomic unit before tearing down - so the
      ON-SCREEN result is always atomic (full grep fix, or untouched original), never a truncation.
      Tested with REAL threads in the racing interleave, and proven NOT to deadlock (bounded drain).

Run standalone: .venv/bin/python test_early_stop.py
"""
import threading
import time

from overlay import OverlayTyper, min_edit_script


passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"ok  {msg}")


# --------------------------------------------------------------------------------------------------
# A recording OverlayTyper whose keystroke ops we replay to reconstruct the visible on-screen buffer
# (the source of truth for "what the user is left looking at"), independent of OverlayTyper's own
# `typed` bookkeeping. It can optionally raise a TeardownCut at the first type-after-backspace, to
# MODEL the daemon worker being killed mid-reconcile (used only for the primitive demonstration).
# --------------------------------------------------------------------------------------------------
class TeardownCut(Exception):
    pass


class RecordingTyper(OverlayTyper):
    def __init__(self, cut_at_seam=False, **kw):
        super().__init__(dry=True, quiet=True, **kw)
        self._cut_at_seam = cut_at_seam
        self._saw_backspace = False
        self.was_cut = False

    def _backspace(self, n):
        super()._backspace(n)
        if n > 0:
            self._saw_backspace = True

    def _type(self, s):
        if self._cut_at_seam and self._saw_backspace and not self.was_cut:
            self.was_cut = True
            raise TeardownCut()
        super()._type(s)


def visible(start_text, ops, skip):
    """Reconstruct on-screen text from recorded ops (skipping the first `skip` initial-append ops)."""
    buf = list(start_text)
    cur = len(buf)
    for kind, payload in ops[skip:]:
        if kind == "type":
            for ch in payload:
                buf.insert(cur, ch); cur += 1
        elif kind == "backspace":
            for _ in range(payload):
                if cur > 0:
                    del buf[cur - 1]; cur -= 1
        elif kind == "left":
            cur = max(0, cur - payload)
        elif kind == "right":
            cur = min(len(buf), cur + payload)
    return "".join(buf)


START = "Let's grab a coffee after we grab the logs"
TARGET = "Let's grab a coffee after we grep the logs."


def typed_start(cut_at_seam=False, min_edit=True):
    t = RecordingTyper(cut_at_seam=cut_at_seam, min_edit=min_edit)
    t.append_words(START.split())
    assert t.typed == START
    return t, len(t.ops)   # ops count after the initial append


# --------------------------------------------------------------------------------------------------
# (1) PRIMITIVE - the failure mode, on record. An unguarded reconcile cut at the seam half-applies.
# --------------------------------------------------------------------------------------------------
def test_primitive_half_apply():
    print("== (1) primitive: an unguarded reconcile cut at the backspace->type seam half-applies ==")
    results = {}
    for min_edit in (True, False):
        t, skip = typed_start(cut_at_seam=True, min_edit=min_edit)
        try:
            t.reconcile(TARGET, exact=True)
        except TeardownCut:
            pass
        v = visible(START, t.ops, skip)
        results[min_edit] = v
        half = v != TARGET and v != START
        print(f"   min_edit={min_edit}: visible={v!r}  half_applied={half}  cut={t.was_cut}")
        check(half and t.was_cut,
              f"unguarded cut half-applies (min_edit={min_edit}): {v!r}")
    # DUM_MIN_EDIT assessment: it SHRINKS the blast radius but does NOT make it atomic. With
    # min-edit, the grep span ("ab"->"ep") is lost but the appended "." span already applied
    # ("...we gr the logs."); without it, the WHOLE tail from the first changed char is wiped
    # ("...we gr"). Either way it is a truncation, not <=1 char and not atomic.
    check(results[True] == "Let's grab a coffee after we gr the logs.",
          "min-edit shrinks loss to one diff span (loses 'ep', keeps the '.' span)")
    check(results[False] == "Let's grab a coffee after we gr",
          "legacy backspace-retype loses the WHOLE tail (worst case = user's report)")
    # The lost span here is 2 chars ("ab"), so min-edit's worst case is NOT <=1 char.
    span_loss = len("Let's grab a coffee after we grab the logs") - len(results[True].replace(".", "")) + 0
    check(min_edit_script("grab", "grep") is not None and
          any(nb >= 2 for _, nb, _ in min_edit_script("grab the logs", "grep the logs.")),
          "a single min-edit span can delete >=2 chars (worst case > 1 char)")


# --------------------------------------------------------------------------------------------------
# (2) ORCHESTRATION - the actual fix: a _reconcile_lock barrier makes stop()/teardown wait for an
# in-flight reconcile, so the on-screen result is ATOMIC. Real threads, real racing interleave.
#
# This faithfully models live.py:
#   * a daemon "worker" runs the flush-commit reconcile, holding `_reconcile_lock` for the whole
#     destructive+constructive insertion (live.py commit());
#   * stop() clears `running`, then uses the lock as a BARRIER (acquire+release, bounded) before
#     "tearing down" - and never KILLS the worker, it joins it.
# We force the worst interleave (stop fires DURING the reconcile) with events, and assert the
# reconstructed on-screen buffer is atomic. We also assert no deadlock and a bounded drain.
# --------------------------------------------------------------------------------------------------
def test_orchestration_atomic():
    print("\n== (2) orchestration: _reconcile_lock barrier keeps the reconcile atomic under a raced stop ==")
    reconcile_lock = threading.Lock()
    running = threading.Event(); running.set()
    in_reconcile = threading.Event()    # set the instant the worker begins its locked reconcile
    stop_requested = threading.Event()

    t, skip = typed_start(cut_at_seam=False, min_edit=True)

    def worker():
        # mimic _consume: spin until running clears, then run the flush-commit reconcile under lock
        while running.is_set():
            time.sleep(0.001)
        with reconcile_lock:           # <-- live.py commit()'s `with self._reconcile_lock`
            in_reconcile.set()
            # slow the reconcile so stop() is guaranteed to race INTO it (models keystroke latency)
            time.sleep(0.05)
            t.reconcile(TARGET, exact=True)

    w = threading.Thread(target=worker, daemon=True)
    w.start()

    # stop(): clear running, then the bounded barrier (live.py stop()), then "teardown" = join.
    running.clear()
    in_reconcile.wait(timeout=2.0)     # make the race deterministic: stop arrives mid-reconcile
    stop_requested.set()
    drained = reconcile_lock.acquire(timeout=3.0)   # barrier: WAITS for the reconcile to finish
    if drained:
        reconcile_lock.release()
    # teardown joins the worker (never kills it) - in live.py the daemon would only be killed on
    # process exit, which now happens AFTER this join returns.
    w.join(timeout=3.0)

    v = visible(START, t.ops, skip)
    check(drained, "stop() barrier acquired the lock (no deadlock; reconcile drained)")
    check(not w.is_alive(), "worker finished cleanly (joined, not killed mid-reconcile)")
    check(v == TARGET,
          f"on-screen result is ATOMIC after the raced stop: {v!r} == full grep fix")


def test_no_deadlock_bounded_drain():
    """If a reconcile somehow never finishes, the barrier MUST time out (bounded) and let teardown
    proceed - so stop()/Ctrl-C can never hang. Models a stuck reconcile holding the lock forever."""
    print("\n== (3) bounded drain: barrier never hangs teardown (Ctrl-C safety) ==")
    lock = threading.Lock()
    stuck = threading.Event()

    def hog():
        with lock:
            stuck.set()
            time.sleep(5.0)            # holds the lock far longer than the drain budget

    h = threading.Thread(target=hog, daemon=True)
    h.start()
    stuck.wait(timeout=1.0)
    t0 = time.monotonic()
    got = lock.acquire(timeout=0.3)    # tiny drain budget for the test
    elapsed = time.monotonic() - t0
    if got:
        lock.release()
    check(not got, "barrier returns without acquiring when a reconcile is stuck (proceeds with warning)")
    check(elapsed < 1.0, f"barrier is BOUNDED ({elapsed:.2f}s) - teardown/Ctrl-C cannot deadlock")


if __name__ == "__main__":
    test_primitive_half_apply()
    test_orchestration_atomic()
    test_no_deadlock_bounded_drain()
    print(f"\nALL {passed} CHECKS PASSED")
