#!/usr/bin/env python3
"""Unit tests for the auto-start builders (autostart.py).

Both builders are PURE (no launchctl / no schtasks), so they're tested on any OS:
  * macOS launchd plist - RunAtLoad + KeepAlive-on-crash-only + GUI session
  * Windows Task Scheduler XML - LogonTrigger + RestartOnFailure + InteractiveToken
The install/uninstall/status verbs shell out to the OS scheduler; macOS + Windows are
implemented, so only Linux still raises NotImplementedError (asserted on Linux).
"""
import plistlib
import unittest
from pathlib import Path
import xml.dom.minidom as minidom

import autostart
import autostart_mac


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


class TestMacBundledAutostart(unittest.TestCase):
    """Auto-start from a bundled .app targets the APP, not the repo's shell script.

    Shipped broken once: the LaunchAgent pointed at <REPO_ROOT>/dum and refused to install
    unless <REPO_ROOT>/.venv/bin/python existed. Inside a downloaded .app neither exists, so
    install() raised, the tray swallowed it, and "Open at login" silently did nothing - a menu
    item that could never work.
    """

    def _frozen(self, app="/Applications/dum.app"):
        """Fake a frozen bundle the way PyInstaller sets it up."""
        import sys, contextlib
        @contextlib.contextmanager
        def ctx():
            old_frozen = getattr(sys, "frozen", None)
            old_exe = sys.executable
            sys.frozen = True
            sys.executable = f"{app}/Contents/MacOS/dum"
            try:
                yield
            finally:
                sys.executable = old_exe
                if old_frozen is None:
                    del sys.frozen
                else:
                    sys.frozen = old_frozen
        return ctx()

    def test_bundle_is_detected_from_sys_executable(self):
        with self._frozen():
            self.assertEqual(str(autostart_mac.app_bundle_path()), "/Applications/dum.app")

    def test_checkout_reports_no_bundle(self):
        self.assertIsNone(autostart_mac.app_bundle_path())

    def test_bundled_job_launches_the_app_not_a_script(self):
        # Apple: a SCRIPT as the main executable causes TCC problems. Launch via LaunchServices
        # so the login copy is a real app with the same identity - and therefore the same
        # already-granted permissions.
        with self._frozen():
            prog, _wd, out, _err = autostart_mac._mac_job_paths()
            self.assertEqual(prog[0], "/usr/bin/open")
            self.assertIn(autostart_mac.LABEL, prog)
            self.assertFalse(any(str(p).endswith("/dum") and "open" not in str(p)
                                 for p in prog[:1]))

    def test_bundled_logs_never_land_inside_the_app(self):
        # Writing into the bundle invalidates its signature, and macOS keys the user's
        # Microphone / Accessibility / Input Monitoring grants to that signature.
        with self._frozen():
            _prog, _wd, out, err = autostart_mac._mac_job_paths()
            for p in (out, err):
                self.assertNotIn(".app/", str(p))
                self.assertTrue(str(p).startswith(str(Path.home())))

    def test_bundled_plist_declares_its_bundle_id(self):
        # Without this macOS lists the job as an anonymous background item the user cannot
        # identify, instead of "dum" under Login Items.
        d = autostart_mac.build_plist_dict(["/usr/bin/open", "-b", autostart_mac.LABEL],
                                           "/tmp", "/tmp/o.log", "/tmp/e.log",
                                           bundle_id=autostart_mac.LABEL)
        self.assertEqual(d["AssociatedBundleIdentifiers"], [autostart_mac.LABEL])

    def test_checkout_plist_omits_the_bundle_id(self):
        d = autostart_mac.build_plist_dict(["/repo/dum", "--tray"], "/repo",
                                           "/repo/o.log", "/repo/e.log")
        self.assertNotIn("AssociatedBundleIdentifiers", d)

    def test_bundled_job_passes_no_dictation_flags(self):
        # `open -b` would treat trailing flags as FILES to open and fail. The frozen entry
        # point supplies its own defaults when launched with no argv.
        with self._frozen():
            prog, _wd, _o, _e = autostart_mac._mac_job_paths()
            self.assertNotIn("--tray", prog)


if __name__ == "__main__":
    unittest.main()
