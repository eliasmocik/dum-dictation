#!/usr/bin/env python3
"""Unit tests for the tray controller (tray.py).

The GUI (pystray/pillow, the macOS menu-bar loop) can't run headlessly, so we test the
non-GUI glue - TrayController - with a fake app. It must mirror the app's real listening
state (so the icon tracks the hotkey too, not just menu clicks) and tear down exactly once
on quit. Importing tray.py here must NOT require pystray/pillow (they're lazy in run()).
"""
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


if __name__ == "__main__":
    unittest.main()
