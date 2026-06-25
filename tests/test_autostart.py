#!/usr/bin/env python3
"""Unit tests for the launchd auto-start plist (autostart.py).

The plist BUILDER is pure (no launchctl), so it's tested anywhere. The install/
uninstall/status verbs shell out to launchctl and are macOS-only — here we just
assert they fail loudly (NotImplementedError) off macOS rather than silently no-op.
Covers:
  * plist carries the daily-driver command (python + live.py + flags)
  * RunAtLoad on; KeepAlive relaunches on crash but NOT after a clean Quit (exit 0)
  * round-trips as valid launchd XML
  * off-macOS, the launchctl verbs raise a clear NotImplementedError
"""
import plistlib
import sys
import unittest

import autostart


class TestPlistBuilder(unittest.TestCase):
    def _dict(self):
        return autostart.build_plist_dict(
            ["/repo/dum", "--tray"],
            "/repo", "/repo/dogfood/dum.out.log", "/repo/dogfood/dum.err.log")

    def test_label_and_command(self):
        d = self._dict()
        self.assertEqual(d["Label"], autostart.LABEL)
        # launches the `dum` shell launcher (so login == manual ./dum: same flags + env)
        self.assertEqual(d["ProgramArguments"], ["/repo/dum", "--tray"])
        self.assertIn("--tray", d["ProgramArguments"])

    def test_starts_at_login(self):
        self.assertIs(self._dict()["RunAtLoad"], True)

    def test_keepalive_relaunches_on_crash_only(self):
        # KeepAlive as {SuccessfulExit: False} => relaunch on non-zero exit (crash),
        # leave a clean Quit (exit 0) alone. A bare True would fight the menu-bar Quit.
        self.assertEqual(self._dict()["KeepAlive"], {"SuccessfulExit": False})

    def test_runs_in_gui_session(self):
        self.assertEqual(self._dict()["ProcessType"], "Interactive")

    def test_serializes_to_valid_plist(self):
        raw = autostart.build_plist(
            ["/repo/dum", "--tray"], "/repo", "/repo/o.log", "/repo/e.log")
        self.assertEqual(plistlib.loads(raw)["Label"], autostart.LABEL)


@unittest.skipIf(sys.platform == "darwin", "launchctl verbs are exercised on macOS")
class TestOffMacOSGuard(unittest.TestCase):
    def test_install_refuses(self):
        with self.assertRaises(NotImplementedError):
            autostart.install()

    def test_uninstall_refuses(self):
        with self.assertRaises(NotImplementedError):
            autostart.uninstall()

    def test_status_refuses(self):
        with self.assertRaises(NotImplementedError):
            autostart.status()


if __name__ == "__main__":
    unittest.main()
