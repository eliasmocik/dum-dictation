#!/usr/bin/env python3
"""Unit tests for the tray controller (tray.py).

The GUI (pystray/pillow, the macOS menu-bar loop) can't run headlessly, so we test the
non-GUI glue - TrayController - with a fake app. It must mirror the app's real listening
state (so the icon tracks the hotkey too, not just menu clicks) and tear down exactly once
on quit. Importing tray.py here must NOT require pystray/pillow (they're lazy in run()).
"""
import os
import threading
import unittest

from tray import TrayController


class FakeApp:
    """Stands in for LiveDictation: a `running` Event + a toggle that flips it."""

    def __init__(self):
        self.running = threading.Event()

    def toggle(self):
        if self.running.is_set():
            self.running.clear()
        else:
            self.running.set()


class TestTrayController(unittest.TestCase):
    def test_listening_mirrors_app_state(self):
        app = FakeApp()
        c = TrayController(app)
        self.assertFalse(c.listening)
        app.running.set()                 # e.g. the double-tap hotkey started it
        self.assertTrue(c.listening)      # menu bar must reflect it, not its own clicks

    def test_toggle_forwards_to_app(self):
        app = FakeApp()
        c = TrayController(app)
        c.toggle()
        self.assertTrue(app.running.is_set())
        c.toggle()
        self.assertFalse(app.running.is_set())

    def test_quit_calls_teardown_once(self):
        calls = []
        c = TrayController(FakeApp(), on_quit=lambda: calls.append(1))
        c.quit()
        c.quit()                          # idempotent - a second Quit must not re-tear-down
        self.assertEqual(calls, [1])
        self.assertTrue(c.stopped)

    def test_quit_without_callback_is_safe(self):
        c = TrayController(FakeApp())
        c.quit()                          # must not raise when no on_quit given
        self.assertTrue(c.stopped)




class TestTraySettings(unittest.TestCase):
    """Settings live in the tray because a bundled .app has no other UI.

    The first-run wizard is gated on sys.stdin.isatty() and an .app has no TTY, and
    `./dum --config` needs a terminal the user does not have - so without these a downloaded
    copy is permanently stuck on built-in defaults. Config is redirected to a temp file so
    these never touch the developer's real ~/.dum/config.json.
    """

    def setUp(self):
        import tempfile, config
        self.tmp = tempfile.TemporaryDirectory()
        self.path = __import__("pathlib").Path(self.tmp.name) / "config.json"
        # NOT enough to reassign config.CONFIG_PATH: load_config/save_config bind it as a
        # DEFAULT ARGUMENT at import time, so the module global is already captured and the
        # writes would land in the developer's real ~/.dum/config.json. (They did, once.)
        # Redirect HOME instead, which config resolves through.
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        self._orig = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        config.load_config.__defaults__ = (self.path,)
        config.save_config.__defaults__ = (self.path,)
        self.c = TrayController(FakeApp())

    def tearDown(self):
        import config
        config.CONFIG_PATH = self._orig
        config.load_config.__defaults__ = (self._orig,)
        config.save_config.__defaults__ = (self._orig,)
        if self._orig_home is not None:
            os.environ["HOME"] = self._orig_home
        self.tmp.cleanup()

    def test_settings_persist_to_config(self):
        self.assertTrue(self.c.set_key("cmd_r"))
        self.assertEqual(self.c.current_key, "cmd_r")
        # Trigger changes go through the atomic setter: mode alone is meaningless now, since
        # an unsupported key/mode PAIR heals on load exactly like an unknown key does.
        self.assertTrue(self.c.set_trigger("alt_r", "push"))
        self.assertEqual((self.c.current_key, self.c.current_mode), ("alt_r", "push"))

    def test_mic_is_stored_by_name_not_index(self):
        # Indices are reassigned whenever devices appear or vanish (AirPods connecting
        # shifts everything), so an index saved today points at a different mic tomorrow.
        self.c.set_mic("MacBook Air Microphone")
        self.assertEqual(self.c.current_mic, "MacBook Air Microphone")
        self.assertIsInstance(self.c.current_mic, str)

    def test_system_default_mic_round_trips(self):
        self.c.set_mic("Some Mic")
        self.c.set_mic(None)
        self.assertIn(self.c.current_mic, (None, ""))

    def test_choices_are_offered(self):
        self.assertTrue(any(t == "cmd_l" for t, _ in self.c.keys()))
        self.assertTrue(any(m == "toggle" for m, _ in self.c.modes()))
        self.assertTrue(all(isinstance(lbl, str) and lbl for _, lbl in self.c.keys()))

    def test_defaults_when_no_config_exists(self):
        # A fresh install has no config.json at all; the menu must still render a checkmark.
        self.assertFalse(self.path.exists())
        self.assertIsNotNone(self.c.current_key)
        self.assertIsNotNone(self.c.current_mode)

    def test_restart_hook_fires(self):
        seen = []
        c = TrayController(FakeApp(), on_restart=lambda: seen.append(1))
        self.assertTrue(c.restart())
        self.assertEqual(len(seen), 1)

    def test_restart_without_hook_is_safe(self):
        # Never raise from a menu callback: an exception there leaves the user with no menu.
        self.assertFalse(TrayController(FakeApp()).restart())

    def test_all_three_permissions_are_reachable(self):
        """One entry point per permission, because each fails differently and silently:
        Accessibility lets dum TYPE, Microphone lets it HEAR, Input Monitoring lets it SEE
        the hotkey. Input Monitoring had no item at all until a fresh-install test showed
        the hotkey dead with no way to fix it from the menu.

        permissions.ensure is stubbed: unstubbed, this test would fire real macOS prompts
        and open System Settings on the machine running the gate.
        """
        import permissions
        seen = []
        real, permissions.ensure = permissions.ensure, lambda kind, **kw: seen.append(kind)
        try:
            c = TrayController(FakeApp())
            c.open_accessibility_permissions()
            c.open_microphone_permissions()
            c.open_input_monitoring_permissions()
        finally:
            permissions.ensure = real
        self.assertEqual(seen, ["accessibility", "microphone", "input_monitoring"])

    def test_permission_status_never_raises(self):
        # Read on every menu open to draw the tick. A raise here means no menu at all.
        c = TrayController(FakeApp())
        for kind in ("accessibility", "microphone", "input_monitoring", "nonsense"):
            try:
                c.permission_status(kind)
            except Exception as e:
                self.fail(f"permission_status({kind!r}) raised: {type(e).__name__}: {e}")

    def test_accessors_never_raise(self):
        # Every one of these runs inside a pystray callback. A raise means a broken menu.
        c = TrayController(FakeApp())
        for fn in (lambda: c.devices(), lambda: c.keys(), lambda: c.modes(),
                   lambda: c.current_key, lambda: c.current_mode, lambda: c.current_mic,
                   lambda: c.autostart_on):
            try:
                fn()
            except Exception as e:
                self.fail(f"settings accessor raised: {type(e).__name__}: {e}")


class TestMenuCallbackArity(unittest.TestCase):
    """pystray invokes an item as `action(icon, item)` - ALWAYS two positional arguments
    (MenuItem.__call__ -> self._action(icon, self)).

    This shipped broken once. The menu used the usual `lambda _i, v=value: ...` default-arg
    capture, so pystray passed the MenuItem as the second argument, overwriting the captured
    value. Every setter then received a menu object instead of a string, the save failed
    silently inside the controller's try/except, and NO setting ever stuck - while the menu
    itself looked perfectly fine.
    """

    def setUp(self):
        import tempfile, config, pathlib
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "config.json"
        self._orig = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        config.load_config.__defaults__ = (self.path,)
        config.save_config.__defaults__ = (self.path,)
        self.c = TrayController(FakeApp())

    def tearDown(self):
        import config
        config.CONFIG_PATH = self._orig
        config.load_config.__defaults__ = (self._orig,)
        config.save_config.__defaults__ = (self._orig,)
        self.tmp.cleanup()

    def _setter(self, fn, value):
        """The exact closure shape src/tray.py uses for radio items."""
        def _act(_icon=None, _item=None):
            fn(value)
        return _act

    def test_setter_survives_pystrays_two_argument_call(self):
        sentinel = object()                      # stands in for the MenuItem pystray passes
        act = self._setter(self.c.set_key, "cmd_r")
        act(sentinel, sentinel)                  # <- exactly how pystray calls it
        self.assertEqual(self.c.current_key, "cmd_r",
                         "the captured value was clobbered by pystray's second argument")

    def test_setter_works_for_every_field(self):
        sentinel = object()
        # push only exists paired with alt_r, so exercise the atomic setter the menu uses.
        def _trig(_i=None, _it=None):
            self.c.set_trigger("alt_r", "push")
        _trig(sentinel, sentinel)
        self._setter(self.c.set_mic, "Some Mic")(sentinel, sentinel)
        self.assertEqual(self.c.current_mode, "push")
        self.assertEqual(self.c.current_mic, "Some Mic")

    def test_the_broken_pattern_really_would_fail(self):
        # Proves the guard has teeth: the old default-arg form silently stores the MenuItem.
        def old_style(_i, v="cmd_r"):
            self.c.set_key(v)
        old_style(object(), object())            # pystray passes 2 args -> v = the MenuItem
        self.assertNotEqual(self.c.current_key, "cmd_r",
                            "expected the old pattern to be broken; it is not")


class TestLiveApply(unittest.TestCase):
    """Settings apply to the RUNNING process - never by relaunching.

    A restart makes the menu-bar icon vanish for seconds, and if the new copy loses the race
    for ~/.dum/dum.lock it never returns: the app simply disappears. That happened in testing,
    and losing the app is far worse than a setting not applying. So the hotkey listener is
    rebuilt in place and the mic is swapped on the live object.
    """

    def setUp(self):
        import tempfile, config, pathlib
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "config.json"
        self._orig = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        config.load_config.__defaults__ = (self.path,)
        config.save_config.__defaults__ = (self.path,)
        self.hotkeys, self.mics = [], []
        self.c = TrayController(FakeApp(),
                                on_hotkey_change=lambda k, m: self.hotkeys.append((k, m)),
                                on_mic_change=lambda n: self.mics.append(n))

    def tearDown(self):
        import config
        config.CONFIG_PATH = self._orig
        config.load_config.__defaults__ = (self._orig,)
        config.save_config.__defaults__ = (self._orig,)
        self.tmp.cleanup()

    def test_changing_the_key_rebuilds_the_listener(self):
        self.c.set_key("cmd_r")
        self.assertEqual(self.hotkeys[-1], ("cmd_r", self.c.current_mode))

    def test_changing_the_mode_rebuilds_the_listener(self):
        # Set the PAIR, as the menu does. Setting mode alone would pair push with the
        # toggle-only default key, and load_config rightly heals that back to toggle.
        self.c.set_trigger("alt_r", "push")
        self.assertEqual(self.hotkeys[-1], ("alt_r", "push"))
        self.assertEqual(self.c.current_mode, "push")

    def test_changing_the_mic_is_applied_live(self):
        self.c.set_mic("Some Mic")
        self.assertEqual(self.mics[-1], "Some Mic")

    def test_no_relaunch_is_triggered_by_any_setting(self):
        # The whole point: none of these may take the vanish-and-hope path.
        relaunched = []
        c = TrayController(FakeApp(),
                           on_restart=lambda: relaunched.append(1),
                           on_hotkey_change=lambda k, m: None,
                           on_mic_change=lambda n: None)
        c.set_trigger("alt_r", "push"); c.set_key("cmd_r"); c.set_mic("X")
        self.assertEqual(relaunched, [], "a setting triggered a relaunch")

    def test_a_failing_handler_never_breaks_the_menu(self):
        def boom(*a):
            raise RuntimeError("listener rebuild failed")
        c = TrayController(FakeApp(), on_hotkey_change=boom, on_mic_change=boom)
        c.set_key("cmd_r")          # must not raise out of the menu callback
        c.set_mic("X")
        self.assertEqual(c.current_key, "cmd_r", "the setting should still have been saved")

    def test_settings_still_persist_without_handlers(self):
        # The dev/terminal path constructs the controller with no handlers at all.
        c = TrayController(FakeApp())
        # Use a key that is actually in the catalog: "fn" is deliberately absent (pynput has
        # no Key.function on macOS) and would be healed away on load, which is correct.
        self.assertTrue(c.set_key("shift_r"))
        self.assertEqual(c.current_key, "shift_r")


if __name__ == "__main__":
    unittest.main()
