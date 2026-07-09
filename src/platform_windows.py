#!/usr/bin/env python3
"""Windows platform backend (split out of platform_io.py).
Owner: Rado (@radozaprazny). The shared interface is platform_base.Platform; the dispatcher is
platform_io.get_platform(). OS-specific imports stay lazy/method-local."""
import subprocess
import sys
import time

from platform_base import Platform, PASTE_SETTLE_S


class WindowsPlatform(Platform):
    """Windows-native I/O.

    Typing is layout-independent Unicode via SendInput (KEYEVENTF_UNICODE) - so a Slovak (or
    any dead-key) layout does NOT mangle output, the same guarantee MacPlatform gets from its
    CGEvent path, and the reason we don't just type through pynput here. Clipboard save/restore
    and focused-app detection use pywin32; cues use winsound. The global hotkey and the overlay's
    backspaces ride on pynput (cross-platform), as on every platform.

    All Windows-only imports (ctypes.windll, win32*, winsound) are lazy/method-local, so importing
    this module stays clean on macOS/Linux.
    """

    def __init__(self):
        self._win = None      # lazily-built ctypes SendInput plumbing (cached)

    # ---- layout-independent Unicode typing (the overlay live-type path) ------
    def _sendinput_api(self):
        if self._win is not None:
            return self._win
        import ctypes
        from ctypes import wintypes
        ULONG_PTR = ctypes.POINTER(wintypes.ULONG)

        class MOUSEINPUT(ctypes.Structure):       # only here to size the union correctly
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                        ("wParamH", wintypes.WORD)]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

        self._win = {"ctypes": ctypes, "user32": ctypes.windll.user32,
                     "INPUT": INPUT, "KEYBDINPUT": KEYBDINPUT}
        return self._win

    def type_text(self, text):
        if not text:
            return
        api = self._sendinput_api()
        ctypes, user32, INPUT, KEYBDINPUT = api["ctypes"], api["user32"], api["INPUT"], api["KEYBDINPUT"]
        INPUT_KEYBOARD, KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 1, 0x0004, 0x0002
        # UTF-16-LE code units => one keydown+keyup per unit; surrogate pairs (emoji) sent as
        # two consecutive units, which is exactly what Windows expects for KEYEVENTF_UNICODE.
        units = text.encode("utf-16-le")
        events = []
        for i in range(0, len(units), 2):
            code = units[i] | (units[i + 1] << 8)
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = INPUT(type=INPUT_KEYBOARD)
                inp.u.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=None)
                events.append(inp)
        n = len(events)
        user32.SendInput(n, (INPUT * n)(*events), ctypes.sizeof(INPUT))

    # ---- clipboard-safe paste (rich-text surfaces / the paste backend) -------
    # v1 preserves PLAIN TEXT only (CF_UNICODETEXT): if the user had an image/file on the
    # clipboard it isn't restored (MacPlatform does full-fidelity; full Windows format
    # enumeration is a later refinement). The overlay default types via SendInput and never
    # touches the clipboard, so this path is only hit for paste-at-commit surfaces.
    def _set_clipboard_text(self, text):
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

    def _get_clipboard_text(self):
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return None

    def _send_ctrl_v(self):
        from pynput.keyboard import Controller, Key
        kb = Controller()
        with kb.pressed(Key.ctrl):
            kb.press("v")
            kb.release("v")

    def paste(self, text):
        self._set_clipboard_text(text)
        self._send_ctrl_v()

    def paste_atomic(self, text):
        try:
            prev = self._get_clipboard_text()       # save (plain text only)
            self._set_clipboard_text(text)
            self._send_ctrl_v()
            time.sleep(PASTE_SETTLE_S)               # let Ctrl+V read our text before restore
            if prev is not None:
                self._set_clipboard_text(prev)       # restore
            return True
        except Exception:
            # clipboard contended/unavailable - never lose the text, type it instead
            self.type_text(text)
            return True

    def notify(self, event):
        import winsound
        # MessageBeep is async (non-blocking); distinct system sounds per cue where we can.
        sounds = {
            "start": winsound.MB_ICONASTERISK,
            "done": winsound.MB_OK,
            "empty": winsound.MB_ICONHAND,
            "flag": winsound.MB_ICONEXCLAMATION,
        }
        if event not in sounds:
            return
        try:
            winsound.MessageBeep(sounds[event])
        except Exception:
            pass

    def frontmost_app(self):
        try:
            import os
            import win32api
            import win32con
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            try:
                exe = win32process.GetModuleFileNameEx(h, 0)   # full path
            finally:
                win32api.CloseHandle(h)
            return os.path.basename(exe) or None               # e.g. "Code.exe"
        except Exception:
            return None

    def supports_app_detection(self):
        return True
