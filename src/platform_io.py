#!/usr/bin/env python3
"""
Platform I/O surface — the ONE place OS-specific behaviour lives, so Linux/Windows
are a drop-in later instead of a retrofit.

The rest of the stack is already portable by construction:
  * inference  — Parakeet via sherpa-onnx (ONNX runtime), not MLX, so the engine
                 isn't Mac-locked; the optional homophone LLM (MLX) sits behind the
                 pipeline `Stage` interface and can be swapped for a GGUF/llama.cpp
                 backend on other platforms with no core change.
  * audio in   — sounddevice (PortAudio): cross-platform.
  * hotkey     — pynput global hotkey: cross-platform.
  * overlay typing — pynput keyboard (type + backspace): cross-platform.

Only three things are actually OS-specific, and they live here behind `Platform`:
  * paste(text)        — put text at the cursor via the clipboard
  * notify(event)      — start/done/empty sound cue
  * frontmost_app()    — focused-app name, for the overlay focus guard

MacPlatform (Quartz/AppKit) and WindowsPlatform (SendInput + win32clipboard + winsound +
GetForegroundWindow) are implemented. FallbackPlatform runs anywhere else in degraded mode
(types via pynput instead of clipboard paste, no sounds, focus guard off) so the app at least
starts — it's what Linux uses until a native LinuxPlatform (xclip/wl-clipboard + a bell +
wmctrl/xdotool) lands in the next port phase.

Note WindowsPlatform overrides type_text too (not just the three above): pynput types through
the active keyboard layout, so a dead-key layout mangles output — both native platforms post raw
Unicode instead (CGEvent on mac, SendInput KEYEVENTF_UNICODE on Windows).
"""
import subprocess
import sys
import time

# How long to let a synthetic Cmd+V consume our clipboard text before we restore the user's clipboard.
# Short, bounded; only on the paste path (one paste per dictation session under the HUD model).
PASTE_SETTLE_S = 0.12


class Platform:
    """Interface. event in {"start", "done", "empty"}."""
    def paste(self, text):
        raise NotImplementedError

    def paste_atomic(self, text):
        """Atomically insert `text` at the cursor AND preserve the user's clipboard. Returns True if the
        insert landed, False if it was blocked (e.g. a secure/password field). Default = the plain
        paste() path with no clipboard preservation (degraded — used by the cross-platform fallback,
        which types via pynput and has no clipboard to clobber). MacPlatform overrides with full
        save/restore + secure-input detection. This is the ONE insertion call under the HUD/session
        model (the whole dictated buffer, once, at stop)."""
        self.paste(text)
        return True

    def type_text(self, text):
        """Insert `text` at the cursor as characters (for the live overlay). Default =
        synthetic typing via pynput. MacPlatform overrides with a layout-independent
        Unicode insertion so non-US keyboard layouts don't mangle the output."""
        if not text:
            return
        if getattr(self, "_kb", None) is None:
            from pynput.keyboard import Controller
            self._kb = Controller()
        self._kb.type(text)

    def notify(self, event):
        pass

    def frontmost_app(self):
        return None          # None => overlay focus guard is simply disabled

    def supports_app_detection(self):
        """True if frontmost_app() reliably names the focused app. When False, the
        app-aware overlay can't gate by app, so it stays on everywhere (current
        behaviour). MacPlatform reports True; the cross-platform fallback can't."""
        return False


class MacPlatform(Platform):
    def type_text(self, text):
        """Insert `text` as raw Unicode via CGEvent, bypassing the active keyboard
        layout. pynput types through the layout, so a dead-key layout (Slovak: the
        apostrophe is a dead acute) mangles output — e.g. what's -> whatś. Posting the
        Unicode string directly produces the exact characters regardless of layout."""
        if not text:
            return
        import Quartz
        for ch in text:
            down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(down, 1, ch)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
            Quartz.CGEventKeyboardSetUnicodeString(up, 1, ch)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    SOUNDS = {
        "start": "/System/Library/Sounds/Tink.aiff",
        "done": "/System/Library/Sounds/Pop.aiff",     # short mouse-click-like tick on stop
        "empty": "/System/Library/Sounds/Basso.aiff",
        "flag": "/System/Library/Sounds/Blow.aiff",  # double-⌥: last dictation flagged as a problem
    }

    def paste(self, text):
        subprocess.run(["pbcopy"], input=text.encode("utf-8"))
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ---- clipboard-safe atomic paste (HUD/session model) ---------------------
    # paste_atomic puts the dictated text on the clipboard, sends Cmd+V, then RESTORES whatever the
    # user had — fixing the long-standing clipboard-clobber bug now that EVERY surface pastes. The OS
    # primitives below are factored out so the algorithm (snapshot -> set -> paste -> restore-on-success)
    # is unit-testable with an in-memory fake (test_platform_paste.py) without touching the real
    # pasteboard or pasting Cmd+V into a live app.

    def _pasteboard(self):
        from AppKit import NSPasteboard
        return NSPasteboard.generalPasteboard()

    def _change_count(self):
        return int(self._pasteboard().changeCount())

    def _clipboard_snapshot(self):
        """Full-fidelity capture (Q6): every NSPasteboardItem and all its types, plus changeCount —
        so RTF / images / file-urls survive, not just plain text."""
        pb = self._pasteboard()
        items = []
        for it in (pb.pasteboardItems() or []):
            data = {}
            for t in (it.types() or []):
                d = it.dataForType_(t)
                if d is not None:
                    data[t] = d
            if data:
                items.append(data)
        return {"items": items, "change_count": int(pb.changeCount())}

    def _clipboard_set_text(self, text):
        subprocess.run(["pbcopy"], input=text.encode("utf-8"))

    def _clipboard_restore(self, snap):
        from AppKit import NSPasteboardItem
        pb = self._pasteboard()
        pb.clearContents()
        new_items = []
        for data in snap["items"]:
            it = NSPasteboardItem.alloc().init()
            for t, d in data.items():
                it.setData_forType_(d, t)
            new_items.append(it)
        if new_items:
            pb.writeObjects_(new_items)

    def _secure_input_active(self):
        """A focused secure/password field blocks synthetic Cmd+V (and OS secure-input mode is the one
        block we can actually detect). Quartz.IsSecureEventInputEnabled() is the queryable signal."""
        try:
            import Quartz
            return bool(Quartz.IsSecureEventInputEnabled())
        except Exception:
            return False

    def _send_paste(self):
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def paste_atomic(self, text):
        # Q3: if a secure/password field is focused, synthetic Cmd+V won't land. Don't fight it — leave
        # the dictated text ON the clipboard so the user can paste manually, and report failure (the
        # caller shows the red-pill HUD). Crucially: do NOT restore in this case.
        if self._secure_input_active():
            self._clipboard_set_text(text)
            return False
        snap = self._clipboard_snapshot()
        self._clipboard_set_text(text)               # bumps changeCount by 1
        self._send_paste()
        time.sleep(PASTE_SETTLE_S)                    # let Cmd+V read OUR text before we restore
        # Restore the user's clipboard ONLY on success AND only if nothing else grabbed it meanwhile:
        # our set bumped changeCount to snap+1; if it advanced further, the user copied something new —
        # leave that alone rather than clobber it.
        if self._change_count() == snap["change_count"] + 1:
            self._clipboard_restore(snap)
        return True

    def notify(self, event):
        path = self.SOUNDS.get(event)
        if not path:
            return
        try:
            subprocess.Popen(["afplay", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def frontmost_app(self):
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to name of first '
                 'application process whose frontmost is true'],
                capture_output=True, text=True, timeout=1.0)
            return r.stdout.strip() or None
        except Exception:
            return None

    def supports_app_detection(self):
        return True


class WindowsPlatform(Platform):
    """Windows-native I/O.

    Typing is layout-independent Unicode via SendInput (KEYEVENTF_UNICODE) — so a Slovak (or
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
            # clipboard contended/unavailable — never lose the text, type it instead
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


class FallbackPlatform(Platform):
    """Runs anywhere: paste by synthetic typing (pynput), no sounds, no focus guard."""
    def __init__(self):
        self._kb = None

    def paste(self, text):
        if self._kb is None:
            from pynput.keyboard import Controller
            self._kb = Controller()
        self._kb.type(text)


def get_platform():
    if sys.platform == "darwin":
        return MacPlatform()
    if sys.platform == "win32":
        return WindowsPlatform()
    return FallbackPlatform()
