#!/usr/bin/env python3
"""Linux (X11 + Wayland) platform backend.

The shared interface is platform_base.Platform; the dispatcher is
platform_io.get_platform(). OS-specific imports stay lazy/method-local.

Supported tools (auto-detected, graceful degradation):
  * type_text  - ydotool type (Wayland) / xdotool type (X11) for
                 layout-independent Unicode; falls back to pynput typing.
  * paste      - wl-copy/wl-paste (Wayland) or xclip (X11) for clipboard
                 save/restore, then Ctrl+V; falls back to typing.
  * notify     - canberra-gtk-play bell event, else terminal bell (\a).
  * frontmost  - xdotool getactivewindow (X11 only); None on Wayland.
 """
import os
import socket
import subprocess
import sys
import time

from platform_base import Platform, PASTE_SETTLE_S


def _session_type():
    """Detect the display server: 'wayland', 'x11', or None (unknown)."""
    st = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if st in ("wayland", "x11"):
        return st
    # Fall back to logind, matching the current user's session.
    try:
        sid = subprocess.run(
            ["loginctl"], capture_output=True, text=True, timeout=1.0
        ).stdout
        cur = subprocess.run(
            ["awk", "-v", "u=" + os.environ.get("USER", ""),
             '$3==u {print $1; exit}'],
            input=sid, capture_output=True, text=True, timeout=1.0
        ).stdout.strip()
        if cur:
            r = subprocess.run(
                ["loginctl", "show-session", cur, "-p", "Type"],
                capture_output=True, text=True, timeout=1.0)
            if r.returncode == 0:
                val = r.stdout.strip().removeprefix("Type=").lower()
                if val in ("wayland", "x11"):
                    return val
    except Exception:
        pass
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return None


def _ydotoold_running():
    """ydotool needs the ydotoold daemon + its socket. Return True only if the
    socket actually accepts a connection, so a stale socket left behind by a dead
    daemon is correctly reported as not running."""
    sock = os.environ.get("YDOTOOL_SOCKET", "/tmp/.ydotool_socket")
    if not os.path.exists(sock):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(sock)
        s.close()
        return True
    except OSError:
        return False


class LinuxPlatform(Platform):
    """Linux I/O via standard CLI tools, auto-detecting X11 vs Wayland.

    Each tool is used only if present so the app still starts on a minimal box.
    The session type is detected once at construction; on pure Wayland the
    xdotool-based typing and app-detection paths are skipped in favour of
    ydotool (typing) / wl-clipboard (paste).
    """

    def __init__(self):
        import shutil

        self._session = _session_type()
        self._has_xdotool = bool(shutil.which("xdotool"))
        self._has_ydotool = bool(shutil.which("ydotool"))
        self._ydotool_ok = self._has_ydotool and _ydotoold_running()

        # Clipboard: prefer Wayland tooling on Wayland, X11 on X11.
        if shutil.which("wl-copy") and shutil.which("wl-paste"):
            self._clip = "wayland"
        elif shutil.which("xclip"):
            self._clip = "xclip"
        else:
            self._clip = None

        # Sound: libcanberra bell event, else terminal bell (\a).
        # (pw-play/paplay need a file argument, so they're not used for a bare cue.)
        if shutil.which("canberra-gtk-play"):
            self._bell_cmd = ("canberra-gtk-play", "-i", "bell")
        else:
            self._bell_cmd = None

        self._kb = None
        self._warned_ydotool = False

    def type_text(self, text):
        if not text:
            return
        # Prefer ydotool on Wayland only when its daemon is actually responding;
        # otherwise fall back to pynput typing so dictation still works.
        if self._session == "wayland" and self._has_ydotool:
            if self._ydotool_ok:
                try:
                    r = subprocess.run(
                        ["ydotool", "type", text], timeout=5.0,
                        capture_output=True)
                    if r.returncode == 0:
                        return
                    # Daemon gone (e.g. stale socket from an exited daemon) - stop
                    # retrying ydotool and fall back to pynput for this text.
                    self._ydotool_ok = False
                except Exception:
                    self._ydotool_ok = False
            if not self._warned_ydotool:
                self._warned_ydotool = True
                print("[linux] ydotoold not responding - falling back to pynput typing. "
                      "Start it with: ydotoold &  (or enable the ydotoold service)",
                      file=sys.stderr, flush=True)
            if self._kb is None:
                from pynput.keyboard import Controller
                self._kb = Controller()
            self._kb.type(text)
            return
        if self._has_xdotool:
            subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text])
            return
        if self._kb is None:
            from pynput.keyboard import Controller
            self._kb = Controller()
        self._kb.type(text)

    def _clip_get(self):
        if self._clip == "wayland":
            r = subprocess.run(["wl-paste", "-n"], capture_output=True, text=True)
        elif self._clip == "xclip":
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                               capture_output=True, text=True)
        else:
            return None
        return r.stdout if r.returncode == 0 else None

    def _clip_set(self, text):
        if self._clip == "wayland":
            subprocess.run(["wl-copy"], input=text, text=True)
        elif self._clip == "xclip":
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True)

    def _send_paste(self):
        if self._has_xdotool:
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
            self.type_text(text)
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
            if self._bell_cmd:
                subprocess.Popen([*self._bell_cmd],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                sys.stderr.write("\a")
                sys.stderr.flush()
        except Exception:
            pass

    def frontmost_app(self):
        if not self._has_xdotool:
            return None
        try:
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True, text=True, timeout=1.0)
            return r.stdout.strip() or None
        except Exception:
            return None

    def supports_app_detection(self):
        return self._has_xdotool
