#!/usr/bin/env python3
"""
Menu-bar / system-tray front-end for the dum daily driver.

This is the "no babysat terminal" half of the robust launch: a little icon in the
macOS menu bar (and, in later phases, the Windows tray + Linux indicator) that shows
whether the robot is listening and lets you Start/Stop or Quit - paired with auto-start
(autostart.py) and the single-instance guard (single_instance.py).

THREADING (the important bit): on macOS the GUI run loop MUST own the main thread, so
`run()` blocks the main thread in `icon.run()`. The hotkey listener (pynput) and the
recognizer already live on their own background threads, so they keep working underneath.
The double-tap hotkey and the menu both drive the SAME LiveDictation, so the icon mirrors
whatever state the app is actually in (a watcher thread polls app.running).

Cross-platform by design: `pystray` backs the macOS menu bar, the Windows tray, and the
Linux AppIndicator/XOrg tray from one code path - phases 2/3 reuse this unchanged. GUI
deps (pystray, pillow) are imported lazily inside run()/_icon_image so the headless
controller below (and its tests) need neither.
"""
import threading
import time


class TrayController:
    """Non-GUI glue between the tray menu and LiveDictation - unit-testable on its own.

    The tray's menu/items call into this; it forwards to the app's thread-safe
    start/stop/toggle and exposes the live listening state for the icon to mirror.
    """

    def __init__(self, app, on_quit=None):
        self._app = app
        self._on_quit = on_quit          # stop the hotkey listener + app on quit
        self._stopped = False

    @property
    def listening(self):
        return bool(self._app.running.is_set())

    @property
    def stopped(self):
        return self._stopped

    def toggle(self):
        # start <-> stop; LiveDictation.toggle is guarded by its own lock, so calling
        # it from the GUI thread while the hotkey thread may also call it is safe.
        self._app.toggle()

    def quit(self):
        if self._stopped:
            return
        self._stopped = True
        if self._on_quit:
            self._on_quit()


def _icon_image(active):
    """A simple filled dot: green while listening, grey while idle. Drawn in-process
    (PIL) so we ship no image asset."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = (52, 199, 89, 255) if active else (142, 142, 147, 255)  # macOS green / grey
    d.ellipse((10, 10, size - 10, size - 10), fill=fill)
    return img


def _watch(icon, controller, poll_s=0.2):
    """Mirror the app's real listening state onto the icon - so the double-tap hotkey
    flipping start/stop also updates the menu bar, not just the menu's own clicks."""
    last = None
    while not controller.stopped:
        cur = controller.listening
        if cur != last:
            icon.icon = _icon_image(cur)
            icon.title = "dum - listening" if cur else "dum - idle"
            icon.update_menu()
            last = cur
        time.sleep(poll_s)


def run(app, on_quit=None):
    """Show the tray icon and block the (main) thread until the user picks Quit.

    `on_quit` is called once on quit to tear down the rest (hotkey listener + app);
    we then stop the icon, which returns control from icon.run() and lets main() exit.
    """
    import pystray

    # Free llama.cpp's Metal context before AppKit exits. Every quit path (menu, Ctrl+C via
    # pystray's own SIGINT handler, Apple-menu Quit, logout) routes through
    # -[NSApplication terminate:], which calls C exit() - bypassing Python's atexit and
    # asserting in ggml's Metal device-free if a Llama is still alive. macOS posts
    # NSApplicationWillTerminate BEFORE that exit(), so freeing here is the one hook that
    # covers all paths (can't rely on intercepting SIGINT - pystray overrides our handler).
    # Kept in a list so the observer token + block outlive this call (run() blocks until quit).
    _keepalive = []
    try:
        from AppKit import NSApplicationWillTerminateNotification
        from Foundation import NSNotificationCenter

        def _free_backends(_note):
            try:
                from llm_backend import close_all_backends
                close_all_backends()
            except Exception:
                pass

        _keepalive.append(NSNotificationCenter.defaultCenter()
                          .addObserverForName_object_queue_usingBlock_(
                              NSApplicationWillTerminateNotification, None, None, _free_backends))
    except Exception:
        pass    # non-macOS (Windows/Linux tray) - Metal assert is macOS-only

    controller = TrayController(app, on_quit=on_quit)

    def _do_quit(icon, _item):
        controller.quit()
        icon.visible = False
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda _i: "Stop listening" if controller.listening else "Start listening",
            lambda _i: controller.toggle(),
            default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit dum", _do_quit),
    )
    icon = pystray.Icon(
        "dum", icon=_icon_image(controller.listening),
        title="dum - idle", menu=menu)

    def _setup(icon):
        icon.visible = True
        threading.Thread(target=_watch, args=(icon, controller), daemon=True).start()

    import sys
    if sys.platform == "linux":
        try:
            icon.run(setup=_setup)   # blocks on the main thread until _do_quit -> icon.stop()
        except Exception as e:  # noqa: BLE001 - surface tray failures instead of crashing
            # Most common on Linux/Wayland: no StatusNotifierItem host (GNOME without
            # the AppIndicator extension, etc.). The hotkey + terminal still work, so
            # don't die.
            print(f"[tray] could not start system tray ({type(e).__name__}: {e}).",
                  file=sys.stderr, flush=True)
            print("[tray] dum still runs - use the global hotkey to start/stop, "
                  "Ctrl+C to quit.", file=sys.stderr, flush=True)
    else:
        icon.run(setup=_setup)   # blocks on the main thread until _do_quit -> icon.stop()
