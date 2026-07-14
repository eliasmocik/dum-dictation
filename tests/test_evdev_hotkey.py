#!/usr/bin/env python3
"""_build_evdev_hotkey device selection (Linux raw-input hotkey).

THE BUG: the evdev listener opened EVERY key-capable device, including ydotool's uinput
virtual device. dum types dictation through ydotool, so the listener read back its own
synthetic keystrokes (letters, Backspace/arrow corrections, the Ctrl of a Ctrl+V paste).
Those spurious events reset the pending double-tap, so the stop gesture stopped
registering the instant typing began. The user's real keyboards must be read; the
ydotool virtual device must be skipped.

Run standalone: PYTHONPATH=src .venv/bin/python tests/test_evdev_hotkey.py
"""
import sys
import types
import unittest
from unittest import mock

import live


class _FakeCaps:
    def __init__(self, keys):
        self._keys = keys

    def get(self, _key, default=None):
        return self._keys


class _FakeDevice:
    def __init__(self, name, keys):
        self.name = name
        self._keys = keys
        self.closed = False

    def capabilities(self):
        return {_EV_KEY: self._keys}

    def close(self):
        self.closed = True

    def read_loop(self):
        return iter(())


_EV_KEY = 1
_KEY_LEFTCTRL = 29
_KEY_A = 30


def _fake_evdev(devices_by_path):
    ev = types.ModuleType("evdev")
    ecodes = types.SimpleNamespace(
        EV_KEY=_EV_KEY,
        KEY_LEFTCTRL=_KEY_LEFTCTRL, KEY_RIGHTCTRL=97,
        KEY_LEFTALT=56, KEY_RIGHTALT=100,
        KEY_LEFTMETA=125, KEY_RIGHTMETA=126,
        KEY_BACKSPACE=14, KEY_DELETE=111,
        KEY_LEFT=105, KEY_RIGHT=106, KEY_UP=103, KEY_DOWN=108,
        KEY_HOME=102, KEY_END=107, KEY_PAGEUP=104, KEY_PAGEDOWN=109,
    )
    ev.ecodes = ecodes
    ev.list_devices = lambda: list(devices_by_path.keys())
    ev.InputDevice = lambda p: devices_by_path[p]
    return ev


class BuildEvdevHotkey(unittest.TestCase):
    def _run(self, devices_by_path):
        fake = _fake_evdev(devices_by_path)
        with mock.patch.dict(sys.modules, {"evdev": fake}):
            return live._build_evdev_hotkey(lambda *a: None, lambda *a: None)

    def test_skips_ydotool_virtual_device(self):
        real = _FakeDevice("AT Translated Set 2 keyboard", [_KEY_LEFTCTRL, _KEY_A])
        virt = _FakeDevice("ydotoold virtual device", [_KEY_LEFTCTRL, _KEY_A])
        hk = self._run({"/dev/input/event3": real, "/dev/input/event17": virt})
        self.assertIsNotNone(hk)
        self.assertTrue(virt.closed, "ydotool virtual device must be closed/skipped")
        self.assertFalse(real.closed, "real keyboard must be kept open")

    def test_case_insensitive_ydotool_match(self):
        virt = _FakeDevice("YDOTOOLd Virtual Device", [_KEY_LEFTCTRL])
        real = _FakeDevice("Real KB", [_KEY_LEFTCTRL])
        self._run({"/a": virt, "/b": real})
        self.assertTrue(virt.closed)
        self.assertFalse(real.closed)

    def test_keeps_normal_keyboards(self):
        kb1 = _FakeDevice("AT Translated Set 2 keyboard", [_KEY_LEFTCTRL])
        kb2 = _FakeDevice("USB Gaming Keyboard", [_KEY_LEFTCTRL])
        hk = self._run({"/a": kb1, "/b": kb2})
        self.assertIsNotNone(hk)
        self.assertFalse(kb1.closed)
        self.assertFalse(kb2.closed)

    def test_skips_devices_without_modifier_keys(self):
        mouse = _FakeDevice("USB Mouse", [_KEY_A])  # no ctrl/alt/meta
        kb = _FakeDevice("KB", [_KEY_LEFTCTRL])
        self._run({"/m": mouse, "/k": kb})
        self.assertTrue(mouse.closed)
        self.assertFalse(kb.closed)

    def test_returns_none_when_only_virtual_device(self):
        virt = _FakeDevice("ydotoold virtual device", [_KEY_LEFTCTRL])
        self.assertIsNone(self._run({"/v": virt}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
