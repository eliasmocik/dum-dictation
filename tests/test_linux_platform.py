#!/usr/bin/env python3
"""Headless unit tests for the Linux (X11/Wayland) platform backend.

No real subprocesses are executed: shutil.which / subprocess.run / subprocess.Popen
are mocked, so these run on any OS (including the Linux CI gate and macOS/Windows dev
boxes). Covers session detection, tool selection, the ydotoold fallback, clipboard
routing, and the notify sound chain.
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

import platform_linux


class TestSessionType(unittest.TestCase):
    """_session_type() resolves the display server from env + loginctl (mocked)."""

    def setUp(self):
        self._env = {}
        self._env_patch = mock.patch.dict(os.environ, {}, clear=True)
        # Start from a clean env; tests add what they need.
        self._env_patch.start()
        # By default, no tools available (shutil is imported locally in __init__,
        # so patch the stdlib's which directly).
        self._which = mock.patch("shutil.which", return_value=None)
        self._which.start()
        self._loginctl = mock.patch("platform_linux.subprocess.run")
        self._loginctl.start()

    def tearDown(self):
        self._which.stop()
        self._loginctl.stop()
        self._env_patch.stop()

    def test_xdg_session_type_wayland(self):
        os.environ["XDG_SESSION_TYPE"] = "wayland"
        self.assertEqual(platform_linux._session_type(), "wayland")

    def test_xdg_session_type_x11(self):
        os.environ["XDG_SESSION_TYPE"] = "x11"
        self.assertEqual(platform_linux._session_type(), "x11")

    def test_wayland_display_fallback(self):
        os.environ["WAYLAND_DISPLAY"] = "wayland-0"
        self.assertEqual(platform_linux._session_type(), "wayland")

    def test_display_fallback(self):
        os.environ["DISPLAY"] = ":0"
        self.assertEqual(platform_linux._session_type(), "x11")

    def test_unknown_is_none(self):
        self.assertIsNone(platform_linux._session_type())

    def test_loginctl_fallback_wayland(self):
        # No XDG hint, but loginctl reports the current user's session as Wayland.
        os.environ["USER"] = "alice"
        loginctl_list = mock.Mock()
        loginctl_list.stdout = "1 alice seat0\n2 bob seat0\n"
        awk_out = mock.Mock()
        awk_out.stdout = "1\n"          # session id extracted by awk
        loginctl_show = mock.Mock()
        loginctl_show.returncode = 0
        loginctl_show.stdout = "Type=wayland\n"
        platform_linux.subprocess.run.side_effect = [loginctl_list, awk_out, loginctl_show]
        self.assertEqual(platform_linux._session_type(), "wayland")

    def test_loginctl_fallback_x11(self):
        os.environ["USER"] = "alice"
        loginctl_list = mock.Mock()
        loginctl_list.stdout = "1 alice seat0\n"
        awk_out = mock.Mock()
        awk_out.stdout = "1\n"
        loginctl_show = mock.Mock()
        loginctl_show.returncode = 0
        loginctl_show.stdout = "Type=x11\n"
        platform_linux.subprocess.run.side_effect = [loginctl_list, awk_out, loginctl_show]
        self.assertEqual(platform_linux._session_type(), "x11")


class TestLoginctlAwkExpr(unittest.TestCase):
    def test_loginctl_awk_anchored_to_user_column(self):
        # The awk expression must match only the USER column (col 3), not a
        # substring of another user's name. Runs real awk to validate the expr.
        inp = "1 1000 alice seat0\n2 1001 alicebob seat0\n3 1000 alice seat0\n"
        out = subprocess.run(
            ["awk", "-v", "u=alice", '$3==u {print $1; exit}'],
            input=inp, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "1")


class TestYdotooldRunning(unittest.TestCase):
    def test_default_socket_absent(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform_linux.os.path.exists", return_value=False):
                self.assertFalse(platform_linux._ydotoold_running())

    def test_default_socket_present(self):
        sock = mock.MagicMock()
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("platform_linux.os.path.exists", return_value=True), \
             mock.patch("platform_linux.socket.socket", return_value=sock):
            self.assertTrue(platform_linux._ydotoold_running())

    def test_stale_socket_is_not_running(self):
        # A socket file left behind by a dead daemon must not read as "running".
        sock = mock.MagicMock()
        sock.connect.side_effect = OSError("connection refused")
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("platform_linux.os.path.exists", return_value=True), \
             mock.patch("platform_linux.socket.socket", return_value=sock):
            self.assertFalse(platform_linux._ydotoold_running())

    def test_custom_socket_env(self):
        sock = mock.MagicMock()
        with mock.patch.dict(os.environ, {"YDOTOOL_SOCKET": "/custom/sock"}), \
             mock.patch("platform_linux.os.path.exists",
                        lambda p: p == "/custom/sock"), \
             mock.patch("platform_linux.socket.socket", return_value=sock):
            self.assertTrue(platform_linux._ydotoold_running())


def _make_platform(which_map, session="x11", ydotoold_socket=False):
    """Build a LinuxPlatform with mocked tool availability + ydotoold socket."""
    sock = mock.MagicMock()
    with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": session}, clear=True), \
         mock.patch("shutil.which",
                    side_effect=lambda name: which_map.get(name)), \
         mock.patch("platform_linux.os.path.exists",
                    lambda p: ydotoold_socket and p.endswith("ydotool_socket")), \
         mock.patch("platform_linux.socket.socket", return_value=sock):
        return platform_linux.LinuxPlatform()


class TestToolSelection(unittest.TestCase):
    """Constructor probes pick the right clipboard/sound tools for the session."""

    def test_x11_prefers_xclip(self):
        p = _make_platform({"xclip": "/usr/bin/xclip",
                             "xdotool": "/usr/bin/xdotool",
                             "canberra-gtk-play": "/usr/bin/canberra-gtk-play"})
        self.assertEqual(p._clip, "xclip")
        self.assertEqual(p._bell_cmd, ("canberra-gtk-play", "-i", "bell"))
        self.assertTrue(p.supports_app_detection())

    def test_wayland_prefers_wl_clipboard(self):
        p = _make_platform({"wl-copy": "/usr/bin/wl-copy",
                             "wl-paste": "/usr/bin/wl-paste",
                             "ydotool": "/usr/bin/ydotool",
                             "canberra-gtk-play": "/usr/bin/canberra-gtk-play"},
                            session="wayland", ydotoold_socket=True)
        self.assertEqual(p._clip, "wayland")
        self.assertTrue(p._ydotool_ok)
        self.assertFalse(p.supports_app_detection())

    def test_no_clip_tool(self):
        p = _make_platform({})
        self.assertIsNone(p._clip)

    def test_no_sound_tool_falls_back_to_bell(self):
        p = _make_platform({})  # no canberra-gtk-play
        self.assertIsNone(p._bell_cmd)


class TestTypeText(unittest.TestCase):
    """type_text routes to the right tool and falls back when unavailable."""

    # Inject a fake pynput so the fallback path is testable without the real package.
    _FAKE_PYNPUT = None

    @classmethod
    def setUpClass(cls):
        import types
        fake = types.ModuleType("pynput")
        fake_keyboard = types.ModuleType("pynput.keyboard")
        fake_keyboard.Controller = lambda: mock.MagicMock()
        fake.keyboard = fake_keyboard
        cls._FAKE_PYNPUT = {"pynput": fake, "pynput.keyboard": fake_keyboard}

    def _fake_pynput(self):
        return mock.patch.dict(sys.modules, self._FAKE_PYNPUT, clear=False)

    def test_x11_uses_xdotool(self):
        p = _make_platform({"xdotool": "/usr/bin/xdotool"})
        result = mock.MagicMock()
        result.returncode = 0
        with mock.patch("platform_linux.subprocess.run", return_value=result) as run:
            p.type_text("hello")
            run.assert_called_once_with(
                ["xdotool", "type", "--clearmodifiers", "--", "hello"],
                timeout=5.0, capture_output=True)

    def test_x11_xdotool_failure_falls_back_to_pynput(self):
        p = _make_platform({"xdotool": "/usr/bin/xdotool"})
        result = mock.MagicMock()
        result.returncode = 1
        with mock.patch("platform_linux.subprocess.run", return_value=result), \
             self._fake_pynput():
            p.type_text("hello")
            self.assertTrue(p._kb is not None)
            p._kb.type.assert_called_once_with("hello")

    def test_wayland_with_ydotoold_uses_ydotool(self):
        p = _make_platform({"ydotool": "/usr/bin/ydotool"},
                            session="wayland", ydotoold_socket=True)
        result = mock.MagicMock()
        result.returncode = 0
        with mock.patch("platform_linux.subprocess.run", return_value=result) as run:
            p.type_text("hello")
            run.assert_called_once_with(
                ["ydotool", "type", "hello"], timeout=5.0, capture_output=True)

    def test_wayland_ydotool_failure_falls_back_to_pynput(self):
        # ydotool present + daemon "ok" at construction, but the type call exits
        # non-zero (e.g. stale socket / dead daemon). Must NOT be a silent no-op;
        # it must fall back to pynput and stop retrying ydotool.
        p = _make_platform({"ydotool": "/usr/bin/ydotool"},
                            session="wayland", ydotoold_socket=True)
        result = mock.MagicMock()
        result.returncode = 1
        with mock.patch("platform_linux.subprocess.run", return_value=result) as run, \
             self._fake_pynput():
            p.type_text("hello")
            run.assert_called_once_with(
                ["ydotool", "type", "hello"], timeout=5.0, capture_output=True)
            self.assertFalse(p._ydotool_ok)
            self.assertTrue(p._kb is not None)
            p._kb.type.assert_called_once_with("hello")

    def test_wayland_without_ydotoold_falls_back_to_pynput(self):
        # ydotool present but daemon socket absent -> should NOT call ydotool,
        # and should attempt pynput typing.
        p = _make_platform({"ydotool": "/usr/bin/ydotool"},
                            session="wayland", ydotoold_socket=False)
        with mock.patch("platform_linux.subprocess.run") as run, \
             self._fake_pynput():
            p.type_text("hello")
            run.assert_not_called()           # ydotool was never invoked
            self.assertTrue(p._kb is not None)
            p._kb.type.assert_called_once_with("hello")

    def test_empty_text_is_noop(self):
        p = _make_platform({"xdotool": "/usr/bin/xdotool"})
        with mock.patch("platform_linux.subprocess.run") as run:
            p.type_text("")
            run.assert_not_called()

    def test_no_tools_falls_back_to_pynput(self):
        p = _make_platform({})
        with self._fake_pynput():
            p.type_text("hi")
            self.assertTrue(p._kb is not None)
            p._kb.type.assert_called_once_with("hi")


class TestYdotoolKeySyntax(unittest.TestCase):
    """ydotool `key` uses <code>:1 (down) / <code>:0 (up). A release value other than
    :0 (e.g. :2) is non-interpretable and leaves the key stuck down - which silently
    broke Backspace and the arrow keys. Guard the press/release pairing."""

    def test_key_pairs_press_then_release_zero(self):
        self.assertEqual(platform_linux._ydotool_key("14", 1), ["ydotool", "key", "14:1", "14:0"])

    def test_key_repeats_n_times(self):
        self.assertEqual(
            platform_linux._ydotool_key("14", 3),
            ["ydotool", "key", "14:1", "14:0", "14:1", "14:0", "14:1", "14:0"])

    def test_key_zero_count_is_empty(self):
        self.assertEqual(platform_linux._ydotool_key("14", 0), [])
        self.assertEqual(platform_linux._ydotool_key("14", -2), [])

    def test_backspace_uses_ydotool_when_daemon_ok(self):
        p = _make_platform({"ydotool": "/usr/bin/ydotool"},
                           session="wayland", ydotoold_socket=True)
        with mock.patch("platform_linux.subprocess.run") as run:
            p.backspace(2)
            run.assert_called_once_with(
                ["ydotool", "key", "14:1", "14:0", "14:1", "14:0"], timeout=5.0)

    def test_move_cursor_left_uses_left_keycode(self):
        p = _make_platform({"ydotool": "/usr/bin/ydotool"},
                           session="wayland", ydotoold_socket=True)
        with mock.patch("platform_linux.subprocess.run") as run:
            p.move_cursor(-1)
            run.assert_called_once_with(
                ["ydotool", "key", "105:1", "105:0"], timeout=5.0)

    def test_move_cursor_right_uses_right_keycode(self):
        p = _make_platform({"ydotool": "/usr/bin/ydotool"},
                           session="wayland", ydotoold_socket=True)
        with mock.patch("platform_linux.subprocess.run") as run:
            p.move_cursor(2)
            run.assert_called_once_with(
                ["ydotool", "key", "106:1", "106:0", "106:1", "106:0"], timeout=5.0)


class TestSendPaste(unittest.TestCase):
    """_send_paste routes Ctrl+V to xdotool on X11 and pynput on Wayland."""

    def test_x11_uses_xdotool(self):
        p = _make_platform({"xdotool": "/usr/bin/xdotool"}, session="x11")
        with mock.patch("platform_linux.subprocess.run") as run:
            p._send_paste()
            run.assert_called_once_with(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"])

    def test_wayland_skips_xdotool(self):
        # xdotool is installed (deps install it on all sessions) but on Wayland it
        # must NOT be used - paste goes through pynput instead.
        import types
        fake = types.ModuleType("pynput")
        fk = types.ModuleType("pynput.keyboard")
        fk.Controller = lambda: mock.MagicMock()
        fk.Key = mock.MagicMock()
        fake.keyboard = fk
        p = _make_platform({"xdotool": "/usr/bin/xdotool"},
                           session="wayland", ydotoold_socket=True)
        with mock.patch("platform_linux.subprocess.run") as run, \
             mock.patch.dict(sys.modules, {"pynput": fake, "pynput.keyboard": fk}):
            p._send_paste()
            run.assert_not_called()


class TestFrontmost(unittest.TestCase):
    """frontmost_app / supports_app_detection are X11-only (xdotool is X11-only)."""

    def test_wayland_returns_none(self):
        p = _make_platform({"xdotool": "/usr/bin/xdotool"},
                           session="wayland", ydotoold_socket=True)
        self.assertIsNone(p.frontmost_app())
        self.assertFalse(p.supports_app_detection())

    def test_x11_uses_xdotool(self):
        p = _make_platform({"xdotool": "/usr/bin/xdotool"}, session="x11")
        with mock.patch("platform_linux.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="SomeApp\n")
            self.assertEqual(p.frontmost_app(), "SomeApp")
            self.assertTrue(p.supports_app_detection())


class TestPaste(unittest.TestCase):
    def test_paste_uses_clipboard_when_available(self):
        p = _make_platform({"wl-copy": "/usr/bin/wl-copy",
                             "wl-paste": "/usr/bin/wl-paste"})
        with mock.patch.object(p, "_clip_set") as set_clip, \
             mock.patch.object(p, "_send_paste") as send:
            p.paste("text")
            set_clip.assert_called_once_with("text")
            send.assert_called_once()

    def test_paste_falls_back_to_type_when_no_clip(self):
        p = _make_platform({})
        with mock.patch.object(p, "type_text") as tt:
            p.paste("text")
            tt.assert_called_once_with("text")


class TestNotify(unittest.TestCase):
    def test_notify_uses_canberra(self):
        p = _make_platform({"canberra-gtk-play": "/usr/bin/canberra-gtk-play"})
        with mock.patch("platform_linux.subprocess.Popen") as popen:
            p.notify("start")
            popen.assert_called_once()

    def test_notify_falls_back_to_bell_without_tool(self):
        p = _make_platform({})  # no canberra -> terminal bell
        with mock.patch("platform_linux.subprocess.Popen") as popen, \
             mock.patch("sys.stderr") as stderr:
            p.notify("done")
            popen.assert_not_called()
            # terminal bell written to stderr
            stderr.write.assert_called_once_with("\a")

    def test_notify_ignores_unknown_event(self):
        p = _make_platform({"canberra-gtk-play": "/usr/bin/canberra-gtk-play"})
        with mock.patch("platform_linux.subprocess.Popen") as popen:
            p.notify("bogus")
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
