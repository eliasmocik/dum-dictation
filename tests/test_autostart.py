#!/usr/bin/env python3
"""Unit tests for the auto-start builders (autostart.py).

Both builders are PURE (no launchctl / no schtasks), so they're tested on any OS:
  * macOS launchd plist — RunAtLoad + KeepAlive-on-crash-only + GUI session
  * Windows Task Scheduler XML — LogonTrigger + RestartOnFailure + InteractiveToken
The install/uninstall/status verbs shell out to the OS scheduler; macOS + Windows are
implemented, so only Linux still raises NotImplementedError (asserted on Linux).
"""
import plistlib
import unittest
import xml.dom.minidom as minidom

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


class TestWindowsTaskXml(unittest.TestCase):
    def _xml(self):
        cmd, arguments = autostart.windows_launcher_command(["--tray"])
        return autostart.build_task_xml(cmd, arguments, r"C:\repo"), cmd, arguments

    def test_runs_launcher_hidden(self):
        _xml, cmd, arguments = self._xml()
        self.assertEqual(cmd, "powershell.exe")
        self.assertIn("-WindowStyle Hidden", arguments)   # no console flash
        self.assertIn("dum.ps1", arguments)               # the launcher = single source of truth
        self.assertIn("--tray", arguments)

    def test_logon_trigger_and_restart(self):
        xml, *_ = self._xml()
        self.assertIn("<LogonTrigger>", xml)              # start at logon
        self.assertIn("<RestartOnFailure>", xml)          # the KeepAlive analog (self-heal)
        self.assertIn("InteractiveToken", xml)            # GUI session (types into apps)

    def test_serializes_to_valid_xml(self):
        xml, *_ = self._xml()
        minidom.parseString(xml.encode("utf-16"))         # raises on malformed; UTF-16 as schtasks wants


class TestLinuxUnit(unittest.TestCase):
    def _unit(self):
        return autostart.build_unit("/repo/dum --tray", "/repo")

    def test_runs_launcher_with_tray(self):
        self.assertIn("ExecStart=/repo/dum --tray", self._unit())
        self.assertIn("WorkingDirectory=/repo", self._unit())

    def test_starts_at_login_and_self_heals(self):
        u = self._unit()
        self.assertIn("WantedBy=default.target", u)        # start at login
        self.assertIn("Restart=on-failure", u)             # the KeepAlive analog
        self.assertIn("After=graphical-session.target", u)  # DISPLAY/clipboard are up first


class TestUnsupportedPlatformGuard(unittest.TestCase):
    """A truly unsupported OS (not darwin/win32/linux) must fail loudly, not silently no-op."""

    def _on_platform(self, value, fn):
        orig = autostart.sys.platform
        autostart.sys.platform = value
        try:
            return fn()
        finally:
            autostart.sys.platform = orig

    def test_install_refuses_on_unknown(self):
        with self.assertRaises(NotImplementedError):
            self._on_platform("freebsd13", autostart.install)

    def test_status_refuses_on_unknown(self):
        with self.assertRaises(NotImplementedError):
            self._on_platform("freebsd13", autostart.status)


if __name__ == "__main__":
    unittest.main()
