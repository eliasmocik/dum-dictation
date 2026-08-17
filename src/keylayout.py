#!/usr/bin/env python3
"""Resolve the macOS keyboard layout ONCE on the main thread, then serve it from cache.

THE CRASH THIS PREVENTS
-----------------------
On a fresh machine the bundled app died repeatedly with EXC_BREAKPOINT / SIGTRAP. The
faulting stack is unambiguous, and every frame of it is somebody else's code:

    <a Python worker thread>
      ctypes -> TSMGetInputSourceProperty
             -> isValidateInputSourceRef
             -> islGetInputSourceListWithAdditions
             -> dispatch_assert_queue        <- BUG IN CLIENT OF LIBDISPATCH
             -> _dispatch_assert_queue_fail

That is pynput's `keycode_context()` asking Carbon for the current keyboard layout. Carbon
has to (re)build the input-source list to answer, and that rebuild asserts it is running on
the main queue. pynput calls it from its listener thread, so when the rebuild is needed the
process traps - and a SIGTRAP cannot be caught, so the whole app dies, taking the menu bar
icon with it.

WHY THE FIX IS A CACHE AND NOT A LOCK
-------------------------------------
The rebuild is not needed on every call - which is exactly why this is so slippery. It
depends on process state and on which input source is active, so the same binary runs fine
for weeks and then trap-loops on a machine where the list happens to be cold. Attempts to
reproduce it in isolation (bare worker thread; worker thread under a live NSApplication run
loop; forcing pynput's ASCII-capable fallback branch) all SURVIVED, so a "reproduce, then
fix, then prove by reproducing again" loop was not available here.

So this does not try to make the off-main-thread call safe. It removes it. The layout is
resolved once, on the main thread, where the assertion cannot fire by definition, and
pynput's own lookup is then replaced by one that returns the cached value and touches
Carbon never again. The failure mode is gone rather than made less likely.

A keyboard layout can be changed while dum runs, and a cached one will then be stale. That
is a deliberate trade: a stale layout mis-renders the character for a raw key code, while
the alternative is the application terminating. The hotkey is a modifier double-tap and does
not depend on this at all, and text is inserted through the platform backend's raw-Unicode
path (see platform_io), not through this table.
"""
import contextlib
import sys
import threading

_cached = None                      # (keyboard_type, layout_data) once resolved
_lock = threading.Lock()


def _pynput_modules():
    """The two namespaces holding a reference to `keycode_context`.

    Patching one is not enough: `pynput/keyboard/_darwin.py` does
    `from pynput._util.darwin import keycode_context` at import time, so it holds its own
    binding. The Controller reaches the util module's copy (via `get_unicode_to_keycode_map`)
    and the Listener reaches its own. Both have to be replaced or the crash survives in
    whichever one was missed.
    """
    import pynput._util.darwin as util
    import pynput.keyboard._darwin as kbd
    return util, kbd


def prewarm(force=False):
    """Resolve and cache the layout. MUST be called on the main thread, before any
    pynput Listener or Controller exists.

    Returns True if the layout is cached (so the patch below can serve it), False if this
    is not macOS or Carbon would not answer - in which case nothing is patched and pynput
    behaves exactly as it does today.
    """
    global _cached
    if sys.platform != "darwin":
        return False
    if threading.current_thread() is not threading.main_thread():
        # Warming from a worker thread would perform the very call we are trying to move,
        # on the very thread that makes it fatal. Refuse rather than "helpfully" crash.
        raise RuntimeError("keylayout.prewarm() must run on the main thread")
    with _lock:
        if _cached is not None and not force:
            return True
        try:
            util, _ = _pynput_modules()
            with util.keycode_context() as ctx:
                keyboard_type, layout_data = ctx
            if layout_data is None:
                return False
            _cached = (keyboard_type, layout_data)
        except Exception:
            return False
    _install_patch()
    return True


def _install_patch():
    """Replace pynput's Carbon lookup with one that hands back the cached tuple."""
    cached = _cached

    @contextlib.contextmanager
    def _cached_keycode_context():
        yield cached

    for mod in _pynput_modules():
        if hasattr(mod, "keycode_context"):
            mod.keycode_context = _cached_keycode_context


def cached():
    """The cached (keyboard_type, layout_data), or None if prewarm never succeeded."""
    return _cached


def reset():
    """Test hook - drop the cache so a suite can exercise prewarm more than once."""
    global _cached
    with _lock:
        _cached = None
