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

    It also owns SETTINGS. That matters more than it sounds: in a bundled .app the
    first-run wizard never runs (it is gated on sys.stdin.isatty(), and an .app has no TTY)
    and `./dum --config` needs a terminal the user does not have. Without these, a downloaded
    copy is stuck on built-in defaults - system mic, double-tap left Command - with no way to
    change anything. The tray menu is the only UI the app has, so it is where settings live.
    """

    def __init__(self, app, on_quit=None, on_restart=None,
                 on_hotkey_change=None, on_mic_change=None):
        self._app = app
        self._on_quit = on_quit          # stop the hotkey listener + app on quit
        self._on_restart = on_restart    # legacy escape hatch; unused by the menu
        # Applied LIVE instead of by relaunching. Restarting to pick up a setting means the
        # menu-bar icon vanishes for a few seconds, and if the new copy loses the race for
        # ~/.dum/dum.lock it never comes back at all - the app simply disappears, which is a
        # far worse outcome than the setting not taking effect. Both of these swap state in
        # the running process instead.
        self._on_hotkey_change = on_hotkey_change
        self._on_mic_change = on_mic_change
        self._stopped = False
        self._devices = None             # cached: enumerating mics hits CoreAudio

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

    # --- settings -------------------------------------------------------------------
    # Every accessor is defensive: a menu that raises leaves the user with no menu at all,
    # which is strictly worse than a menu missing one row.

    def _cfg(self):
        try:
            import config
            return config.load_config()
        except Exception:
            return {"mic": None, "hotkey_key": None, "hotkey_mode": None}

    def _save(self, **changes):
        try:
            import config
            cfg = config.load_config()
            cfg.update(changes)
            config.save_config(cfg)
            return True
        except Exception:
            return False

    def devices(self, refresh=False):
        """Input devices as [(index, name)]. Cached - CoreAudio enumeration is not free and
        the menu rebuilds on every open."""
        if self._devices is None or refresh:
            try:
                import config
                devs, _default = config.list_input_devices()
                self._devices = [(i, d) for i, d in devs]
            except Exception:
                self._devices = []
        return self._devices

    @property
    def current_mic(self):
        return self._cfg().get("mic")

    def set_mic(self, name):
        """Store the mic by NAME, not index: indices are reassigned when devices come and go
        (AirPods connecting shifts everything), so an index saved today points somewhere else
        tomorrow. sounddevice accepts a name substring directly.

        Applied live: LiveDictation reads self.device inside start(), so the change lands on
        the next start with no restart. If dictation is running right now, the caller cycles
        the stream so it takes effect immediately."""
        ok = self._save(mic=name)
        if ok and self._on_mic_change:
            try:
                self._on_mic_change(name)
            except Exception:
                pass
        return ok

    def keys(self):
        try:
            import config
            return [(k["key"], k["label"]) for k in config.curated_keys()]
        except Exception:
            return []

    @property
    def current_key(self):
        try:
            import config
            return self._cfg().get("hotkey_key") or config.DEFAULT_KEY
        except Exception:
            return None

    def set_key(self, token):
        ok = self._save(hotkey_key=token)
        if ok:
            self._apply_hotkey()
        return ok

    def modes(self):
        try:
            import config
            return [(m["mode"], m["label"]) for m in config.CURATED_MODES]
        except Exception:
            return []

    @property
    def current_mode(self):
        try:
            import config
            return self._cfg().get("hotkey_mode") or config.DEFAULT_MODE
        except Exception:
            return None

    def set_mode(self, mode):
        ok = self._save(hotkey_mode=mode)
        if ok:
            self._apply_hotkey()
        return ok

    def set_trigger(self, key, mode):
        """Set key AND mode together - the menu's only way to change the trigger.

        Atomic on purpose: as two independent settings a user could select a double-tap key
        with push-to-dictate, which is not a coherent gesture and simply never fired.
        """
        ok = self._save(hotkey_key=key, hotkey_mode=mode)
        if ok:
            self._apply_hotkey()
        return ok

    def triggers(self):
        try:
            import config
            return config.triggers()
        except Exception:
            return []

    def is_current_trigger(self, key, mode):
        return self.current_key == key and self.current_mode == mode

    def _apply_hotkey(self):
        """Rebuild the global hotkey listener in place with the current key + mode.

        Safe because it is the same swap the permission re-arm watcher already performs: build
        the replacement first, adopt it, and only then stop the old one - so there is never a
        moment with no listener, and never two competing taps (two live pynput listeners can
        get the process OS-aborted on macOS)."""
        if not self._on_hotkey_change:
            return False
        try:
            self._on_hotkey_change(self.current_key, self.current_mode)
            return True
        except Exception:
            return False

    @property
    def autostart_on(self):
        try:
            import autostart
            st = autostart.status()          # (installed, loaded) on every platform
            if isinstance(st, (tuple, list)):
                return bool(st and st[0])    # the plist/task/unit existing is the truth;
                                             # "loaded" is transient across a reboot
            return bool(st)
        except Exception:
            return False

    def toggle_autostart(self):
        try:
            import autostart
            if self.autostart_on:
                autostart.uninstall()
            else:
                autostart.install()
            return True
        except Exception:
            return False

    def _open_privacy_pane(self, anchor):
        """Deep-link straight to one Privacy & Security pane.

        These exist because of a real failure mode: with Accessibility ungranted the app
        starts, plays its start cue and types nothing, which reads as "broken app" rather than
        "missing permission". Two menu items turn a dead end into two clicks - and because the
        panes hold live toggles, the user can revoke from here too, not only grant.
        """
        import subprocess
        urls = (f"x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?{anchor}",
                f"x-apple.systempreferences:com.apple.preference.security?{anchor}")
        for candidate in urls:
            try:
                subprocess.run(["open", candidate], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                continue
        return False

    def open_accessibility_permissions(self):
        # Accessibility is what lets dum TYPE the text it recognised.
        return self._open_privacy_pane("Privacy_Accessibility")

    def open_microphone_permissions(self):
        # Microphone is what lets dum HEAR you. Without it macOS hands the app silent
        # samples rather than an error, so dictation looks broken rather than unpermitted.
        return self._open_privacy_pane("Privacy_Microphone")

    def restart(self):
        """Relaunch, so a new trigger key or mode actually takes effect.

        The pynput listener is bound to its key at construction and the recognizer is built
        once at startup, so these cannot be hot-swapped in place. Rather than silently saving
        a setting that appears to do nothing, the menu offers an explicit restart.
        """
        if self._on_restart:
            self._on_restart()
            return True
        return False


def _icon_image(active):
    """The menu-bar glyph: the same "dum" circle monogram as the app icon.

    Listening state lives in the DISC colour - green while listening, white while idle - with
    the letters always black, so the mark stays recognisable either way and stays legible on
    both light and dark menu bars. Sharing packaging.make_icon.monogram() means the status
    item and the app icon are one mark rather than two that drift apart.

    Falls back to the original plain dot if the drawing code is unavailable for any reason: a
    missing icon must never stop the tray from appearing.
    """
    from PIL import Image, ImageDraw

    # 256, not 64. The menu bar is ~22pt, i.e. 44px on a Retina display, and AppKit rescales
    # whatever it is handed. A 64px source lands on a non-integer ratio and comes out soft;
    # supplying 256 lets AppKit downsample cleanly at any scale factor.
    size = 256
    # Wider fit + less vertical stretch than the app icon: at 22pt the letters need to claim
    # more of the disc to stay crisp, and the tall proportions that give the Finder icon its
    # presence just thin the strokes at menu-bar size.
    idle_bg, live_bg = (255, 255, 255, 255), (52, 199, 89, 255)   # white / macOS green
    try:
        from icon import monogram
        return monogram(size, bg=live_bg if active else idle_bg, fit=0.86, stretch=1.30)
    except Exception:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        pad = size // 6
        ImageDraw.Draw(img).ellipse((pad, pad, size - pad, size - pad),
                                    fill=live_bg if active else (142, 142, 147, 255))
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


def run(app, on_quit=None, on_hotkey_change=None, on_mic_change=None):
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

    _icon_ref = []

    def _relaunch():
        """Quit, then start a fresh copy once this one has released the single-instance lock.

        Two things here are load-bearing, both learned the hard way - a failed relaunch makes
        the app simply VANISH from the menu bar, which is far worse than the setting not
        applying:

        * WAIT for the old process to actually exit, don't guess with a fixed sleep. The new
          copy races the old one for ~/.dum/dum.lock; losing that race means it exits with
          "dum is already running" and the user is left with no app at all.
        * Launch by PATH, not `open -b <bundle-id>`. LaunchServices can still consider the
          dying instance current and refuse to start another, and the bundle id may resolve to
          a stale registration elsewhere on disk.
        """
        import os as _os, subprocess, sys as _sys
        try:
            if _sys.platform == "darwin":
                exe = _os.path.realpath(_sys.executable)          # .../dum.app/Contents/MacOS/dum
                app = exe
                for _ in range(3):                                 # -> .../dum.app
                    app = _os.path.dirname(app)
                if not app.endswith(".app"):
                    app = "/Applications/dum.app"                  # frozen layout differs? fall back
                script = (
                    f'p="{exe}"; '
                    'for i in $(seq 1 100); do '
                    '  pgrep -f "$p" >/dev/null 2>&1 || break; '
                    '  sleep 0.2; '
                    'done; '
                    'sleep 0.5; '
                    f'open "{app}"'
                )
                subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        controller.quit()
        if _icon_ref:
            _icon_ref[0].visible = False
            _icon_ref[0].stop()

    def _retina_icon_patch(icon):
        """Make the status-item image Retina-correct.

        pystray's macOS backend (_darwin._assert_image) resizes our artwork to
        NSStatusBar.thickness() PIXELS - about 22 - and hands that to NSImage. AppKit then
        draws it at 22 POINTS, which on a Retina display is 44 device pixels, so the bitmap is
        upscaled 2x and looks visibly rastered. No source resolution helps: pystray discards
        the detail before AppKit ever sees it.

        The fix AppKit expects: supply a bitmap at the DEVICE resolution but declare the
        image's logical size in POINTS. NSImage then treats it as a 2x representation and
        renders it crisply. Patched per-instance so we do not modify the installed library.
        """
        try:
            import io
            import AppKit
            import Foundation

            def _assert_image(self=icon):
                thickness = int(self._status_bar.thickness())
                scale = max(1, int(round(
                    AppKit.NSScreen.mainScreen().backingScaleFactor()
                    if AppKit.NSScreen.mainScreen() else 2)))
                px = thickness * scale
                src = self._icon
                if src.size != (px, px):
                    src = src.resize((px, px), __import__("PIL.Image", fromlist=["Image"]).LANCZOS)
                b = io.BytesIO()
                src.save(b, "png")
                img = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(b.getvalue()))
                # THE line that matters: logical size in points, bitmap in device pixels.
                img.setSize_(Foundation.NSMakeSize(thickness, thickness))
                self._icon_image = img
                self._status_item.button().setImage_(img)

            icon._assert_image = _assert_image
        except Exception:
            pass      # any failure just leaves pystray's own (soft) rendering in place

    controller = TrayController(app, on_quit=on_quit, on_restart=_relaunch,
                                on_hotkey_change=on_hotkey_change,
                                on_mic_change=on_mic_change)

    def _do_quit(icon, _item=None):
        controller.quit()
        icon.visible = False
        icon.stop()

    # pystray invokes an item as `action(icon, item)` - ALWAYS two positional arguments
    # (MenuItem.__call__ -> self._action(icon, self)). So the usual
    # `lambda _i, v=value: ...` trick is a trap here: pystray passes the MenuItem as the
    # second argument, overwriting the captured default, and the setter silently receives a
    # menu object instead of a value. Every save then failed and no setting ever stuck.
    # Real closures capture the value out of the argument list entirely.
    def _setter(fn, value, restart=False):
        def _act(_icon=None, _item=None):
            fn(value)
            if restart:
                controller.restart()
        return _act

    def _is(getter, value):
        def _chk(_item=None):
            return getter() == value
        return _chk

    def _trigger_setter(key, mode):
        def _act(_icon=None, _item=None):
            controller.set_trigger(key, mode)
        return _act

    def _is_trigger(key, mode):
        def _chk(_item=None):
            return controller.is_current_trigger(key, mode)
        return _chk

    def _mic_menu():
        # Built fresh on every open so newly-plugged devices appear without a restart.
        devs = controller.devices(refresh=True)
        if not devs:
            return pystray.Menu(pystray.MenuItem("(no input devices found)", None, enabled=False))
        items = [pystray.MenuItem("System default",
                                  _setter(controller.set_mic, None),
                                  checked=lambda _i: controller.current_mic in (None, ""),
                                  radio=True),
                 pystray.Menu.SEPARATOR]
        for _idx, name in devs:
            items.append(pystray.MenuItem(
                name,
                _setter(controller.set_mic, name),
                checked=_is(lambda: controller.current_mic or "", name),
                radio=True))
        return pystray.Menu(*items)

    def _trigger_menu():
        # Both groups are labelled. Push-to-talk leads because it is the headline trigger;
        # an unlabelled first entry read as an orphan sitting above the real list.
        headings = {"hold": "Push-to-talk", "tap": "Toggle"}
        items, seen = [], None
        for t in controller.triggers():
            if t["group"] != seen:
                if seen is not None:
                    items.append(pystray.Menu.SEPARATOR)
                items.append(pystray.MenuItem(headings[t["group"]], None, enabled=False))
                seen = t["group"]
            items.append(pystray.MenuItem(
                t["label"],
                _trigger_setter(t["key"], t["mode"]),
                checked=_is_trigger(t["key"], t["mode"]),
                radio=True))
        return pystray.Menu(*items)

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda _i: "Stop listening" if controller.listening else "Start listening",
            lambda _i, _it=None: controller.toggle(),
            default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Microphone", _mic_menu()),
        pystray.MenuItem("Trigger", _trigger_menu()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Accessibility permissions",
                         lambda _i, _it=None: controller.open_accessibility_permissions()),
        pystray.MenuItem("Microphone permissions",
                         lambda _i, _it=None: controller.open_microphone_permissions()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Autostart at login",
                         lambda _i, _it=None: controller.toggle_autostart(),
                         checked=lambda _i: controller.autostart_on),
        pystray.MenuItem("Quit", _do_quit),
    )
    icon = pystray.Icon(
        "dum", icon=_icon_image(controller.listening),
        title="dum - idle", menu=menu)

    _icon_ref.append(icon)
    _retina_icon_patch(icon)

    def _setup(icon):
        icon.visible = True
        threading.Thread(target=_watch, args=(icon, controller), daemon=True).start()

    icon.run(setup=_setup)   # blocks on the main thread until _do_quit -> icon.stop()
