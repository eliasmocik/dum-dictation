#!/usr/bin/env python3
"""Linux (X11) platform backend (split out of platform_io.py).
Owner: unassigned - parked. The shared interface is platform_base.Platform; the dispatcher is
platform_io.get_platform(). OS-specific imports stay lazy/method-local."""
import subprocess
import sys
import time

from platform_base import Platform, PASTE_SETTLE_S


class LinuxPlatform(Platform):
    """Linux (X11) I/O via the standard CLI tools, each used only if present so the app still
    starts on a minimal box:

      * type_text  - `xdotool type` (layout-independent Unicode, like the mac/win native paths);
                     falls back to pynput typing if xdotool is absent.
      * paste      - `wl-copy`/`wl-paste` (Wayland) or `xclip` (X11) for clipboard save/restore,
                     then Ctrl+V; falls back to typing if no clipboard tool is present.
      * notify     - `canberra-gtk-play` bell if available, else the terminal bell (\\a).
      * frontmost  - `xdotool getactivewindow getwindowclassname` (X11 only).

    Wayland note: xdotool/xclip are X11; under a pure Wayland session install wl-clipboard (paste
    works) and ydotool (typing) or run under XWayland. With nothing available it degrades to pynput
    typing + no focus guard - i.e. exactly the old FallbackPlatform behaviour, never a hard failure.
    """

    def __init__(self):
        import shutil
        self._has_xdotool = bool(shutil.which("xdotool"))
        if shutil.which("wl-copy") and shutil.which("wl-paste"):
            self._clip = "wayland"
        elif shutil.which("xclip"):
            self._clip = "xclip"
        else:
            self._clip = None
        self._bell = shutil.which("canberra-gtk-play")
        self._kb = None

    def type_text(self, text):
        if not text:
            return
        if self._has_xdotool:
            import subprocess
            subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text])
            return
        if self._kb is None:                       # fallback: pynput (types through the layout)
            from pynput.keyboard import Controller
            self._kb = Controller()
        self._kb.type(text)

    def _clip_get(self):
        import subprocess
        if self._clip == "wayland":
            r = subprocess.run(["wl-paste", "-n"], capture_output=True, text=True)
        elif self._clip == "xclip":
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True)
        else:
            return None
        return r.stdout if r.returncode == 0 else None

    def _clip_set(self, text):
        import subprocess
        if self._clip == "wayland":
            subprocess.run(["wl-copy"], input=text, text=True)
        elif self._clip == "xclip":
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True)

    def _send_paste(self):
        if self._has_xdotool:
            import subprocess
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"])
            return
        from pynput.keyboard import Controller, Key
        kb = Controller()
        with kb.pressed(Key.ctrl):
            kb.press("v")
            kb.release("v")

    def paste(self, text):
        if self._clip:
            self._clip_set(text)
            self._send_paste()
        else:
            self.type_text(text)

    def paste_atomic(self, text):
        if not self._clip:
            self.type_text(text)            # no clipboard tool - type it (nothing to preserve)
            return True
        try:
            prev = self._clip_get()
            self._clip_set(text)
            self._send_paste()
            time.sleep(PASTE_SETTLE_S)
            if prev is not None:
                self._clip_set(prev)
            return True
        except Exception:
            self.type_text(text)
            return True

    def notify(self, event):
        if event not in ("start", "done", "empty", "flag"):
            return
        try:
            if self._bell:
                import subprocess
                subprocess.Popen([self._bell, "-i", "bell"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                sys.stderr.write("\a")
                sys.stderr.flush()
        except Exception:
            pass

    def frontmost_app(self):
        if not self._has_xdotool:
            return None
        import subprocess
        try:
            r = subprocess.run(["xdotool", "getactivewindow", "getwindowclassname"],
                               capture_output=True, text=True, timeout=1.0)
            return r.stdout.strip() or None
        except Exception:
            return None

    def supports_app_detection(self):
        return self._has_xdotool
