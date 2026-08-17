#!/usr/bin/env python3
"""Unit tests for permissions.py + keylayout.py - the two fresh-install bugs.

Both were found by wiping a Mac to a genuine first-run state and installing the shipped
DMG, and neither could have been found any other way: on a machine that has already run
dum, the permission rows exist and the keyboard layout is warm, so both bugs are invisible.

Pure logic only - no Mac required, so this runs in the Linux gate like everything else.
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import keylayout          # noqa: E402
import permissions        # noqa: E402


class DecideTest(unittest.TestCase):
    """The fix itself: WHICH action a permission click takes."""

    def test_undetermined_asks_rather_than_opening_settings(self):
        """The bug. An app that has never asked is not listed in System Settings, so
        sending the user there shows a pane with no row to toggle."""
        self.assertEqual(permissions.decide(permissions.UNDETERMINED), permissions.REQUEST)

    def test_decided_permissions_go_to_settings(self):
        # Once decided, asking again is a no-op (macOS only ever prompts once), so the
        # only way to change the answer is the toggle in Settings.
        for status in (permissions.GRANTED, permissions.DENIED):
            self.assertEqual(permissions.decide(status), permissions.OPEN_SETTINGS)

    def test_unreadable_status_falls_back_to_settings(self):
        # Settings always works and never fires a prompt we cannot account for.
        self.assertEqual(permissions.decide(permissions.UNKNOWN), permissions.OPEN_SETTINGS)


class EnsureTest(unittest.TestCase):
    """ensure() must route to the request API, not the deep link, on a fresh machine."""

    def setUp(self):
        self._orig = dict(permissions._HANDLERS)

    def tearDown(self):
        permissions._HANDLERS.clear()
        permissions._HANDLERS.update(self._orig)

    def _wire(self, kind, status):
        calls = {"requested": 0, "opened": []}

        def _status():
            return status

        def _request():
            calls["requested"] += 1
            return True

        permissions._HANDLERS[kind] = (_status, _request)
        return calls

    def test_fresh_install_prompts_and_does_not_open_settings(self):
        for kind in ("microphone", "accessibility", "input_monitoring"):
            calls = self._wire(kind, permissions.UNDETERMINED)
            action = permissions.ensure(kind, opener=lambda k: calls["opened"].append(k))
            self.assertEqual(action, permissions.REQUEST, kind)
            self.assertEqual(calls["requested"], 1, kind)
            self.assertEqual(calls["opened"], [], f"{kind} must not deep-link before asking")

    def test_denied_opens_settings_and_does_not_prompt(self):
        # Re-asking a denied permission does nothing at all on macOS - the user would click
        # and see no response whatsoever. Settings is the only route back.
        for kind in ("microphone", "accessibility", "input_monitoring"):
            calls = self._wire(kind, permissions.DENIED)
            action = permissions.ensure(kind, opener=lambda k: calls["opened"].append(k))
            self.assertEqual(action, permissions.OPEN_SETTINGS, kind)
            self.assertEqual(calls["requested"], 0, kind)
            self.assertEqual(calls["opened"], [kind], kind)


class CoverageTest(unittest.TestCase):
    """The app needs three permissions; the menu used to offer two."""

    def test_all_three_required_permissions_are_handled(self):
        self.assertEqual(set(permissions._HANDLERS), {"microphone", "accessibility",
                                                      "input_monitoring"})

    def test_every_handled_permission_has_a_settings_anchor(self):
        # A kind with no anchor would raise KeyError inside a menu callback, where pystray
        # swallows it and the item silently does nothing.
        self.assertEqual(set(permissions.PANES), set(permissions._HANDLERS))

    def test_summary_reports_every_permission(self):
        self.assertEqual(set(permissions.summary()), set(permissions._HANDLERS))

    def test_status_values_are_from_the_known_set(self):
        known = {permissions.UNDETERMINED, permissions.GRANTED,
                 permissions.DENIED, permissions.UNKNOWN}
        for kind, status in permissions.summary().items():
            self.assertIn(status, known, kind)


class NonMacTest(unittest.TestCase):
    """Off macOS every call must degrade quietly - the tray still has to build."""

    def test_unsupported_platform_reports_unknown_and_never_raises(self):
        if permissions.is_supported():
            self.skipTest("running on macOS - the real APIs answer here")
        self.assertEqual(permissions.microphone_status(), permissions.UNKNOWN)
        self.assertEqual(permissions.accessibility_status(), permissions.UNKNOWN)
        self.assertEqual(permissions.input_monitoring_status(), permissions.UNKNOWN)
        self.assertFalse(permissions.request_microphone())
        self.assertFalse(permissions.request_accessibility())
        self.assertFalse(permissions.request_input_monitoring())


class KeyLayoutTest(unittest.TestCase):
    """The SIGTRAP fix: Carbon must never be reached from a worker thread."""

    def tearDown(self):
        keylayout.reset()

    def test_prewarm_refuses_to_run_off_the_main_thread(self):
        """Warming from a worker would make the exact call the crash comes from, on the
        exact thread that makes it fatal. It must refuse, not 'helpfully' try."""
        box = {}

        def worker():
            try:
                keylayout.prewarm()
            except BaseException as e:
                box["err"] = e

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertIsInstance(box.get("err"), RuntimeError)

    def test_patch_serves_the_cache_without_calling_carbon(self):
        """After patching, pynput's lookup must return the cached tuple - proven by
        installing a sentinel and checking no underlying call is made."""
        keylayout._cached = ("KBTYPE", b"LAYOUT")
        calls = []

        class FakeMod:
            def keycode_context(self):      # replaced by _install_patch
                calls.append("carbon")

        mods = (FakeMod(), FakeMod())
        real = keylayout._pynput_modules
        keylayout._pynput_modules = lambda: mods
        try:
            keylayout._install_patch()
            for m in mods:
                with m.keycode_context() as ctx:
                    self.assertEqual(ctx, ("KBTYPE", b"LAYOUT"))
        finally:
            keylayout._pynput_modules = real
        self.assertEqual(calls, [], "the patched lookup must not reach Carbon at all")

    def test_both_pynput_namespaces_are_patched(self):
        """pynput/keyboard/_darwin.py imports keycode_context BY NAME, so it holds its own
        binding. Patching only the util module leaves the Listener - the thread that
        actually crashed - still calling Carbon."""
        keylayout._cached = ("K", b"L")

        class FakeMod:
            keycode_context = None

        mods = (FakeMod(), FakeMod())
        real = keylayout._pynput_modules
        keylayout._pynput_modules = lambda: mods
        try:
            keylayout._install_patch()
        finally:
            keylayout._pynput_modules = real
        for m in mods:
            self.assertTrue(callable(m.keycode_context))

    def test_prewarm_is_a_noop_off_macos(self):
        if sys.platform == "darwin":
            self.skipTest("macOS has a real layout to resolve")
        self.assertFalse(keylayout.prewarm())
        self.assertIsNone(keylayout.cached())


if __name__ == "__main__":
    unittest.main(verbosity=1)
