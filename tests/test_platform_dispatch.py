#!/usr/bin/env python3
"""Unit tests for platform selection (platform_io.get_platform) + the pure bits of
WindowsPlatform that don't touch Win32 — so they run on any OS, including this Linux gate.

The OS-native behaviour (SendInput typing, win32clipboard, winsound, GetForegroundWindow)
can only be exercised on Windows; here we cover the dispatch and the no-API guards.
"""
import unittest

import platform_io


class TestGetPlatform(unittest.TestCase):
    def _select(self, value):
        orig = platform_io.sys.platform
        platform_io.sys.platform = value
        try:
            return platform_io.get_platform()
        finally:
            platform_io.sys.platform = orig

    def test_darwin_is_mac(self):
        self.assertIsInstance(self._select("darwin"), platform_io.MacPlatform)

    def test_win32_is_windows(self):
        self.assertIsInstance(self._select("win32"), platform_io.WindowsPlatform)

    def test_linux_is_linux(self):
        self.assertIsInstance(self._select("linux"), platform_io.LinuxPlatform)

    def test_unknown_is_fallback(self):
        # any other OS (e.g. *BSD) still starts via the degraded fallback
        self.assertIsInstance(self._select("freebsd13"), platform_io.FallbackPlatform)


class TestWindowsPlatformPure(unittest.TestCase):
    """Constructing it and its no-API paths must be safe even off Windows."""

    def test_construct_is_safe(self):
        platform_io.WindowsPlatform()   # __init__ touches no Win32 API

    def test_type_empty_is_noop(self):
        # empty insert must early-return BEFORE loading the SendInput API, so it's a no-op here
        platform_io.WindowsPlatform().type_text("")

    def test_app_detection_supported(self):
        self.assertTrue(platform_io.WindowsPlatform().supports_app_detection())


class TestLinuxPlatformPure(unittest.TestCase):
    """Constructing it (which only probes tool availability via shutil.which) and its no-API
    paths must be safe on any OS — including a box with no xdotool/xclip, like this gate."""

    def test_construct_is_safe(self):
        platform_io.LinuxPlatform()              # only shutil.which() probes, no execution

    def test_type_empty_is_noop(self):
        platform_io.LinuxPlatform().type_text("")

    def test_app_detection_reflects_xdotool(self):
        # supports_app_detection mirrors whether xdotool was found; here we just assert it's a bool
        self.assertIn(platform_io.LinuxPlatform().supports_app_detection(), (True, False))


if __name__ == "__main__":
    unittest.main()
