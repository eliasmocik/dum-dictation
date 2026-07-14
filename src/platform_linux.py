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


def _ydotool_socket_candidates():
    """Ordered socket paths to try, most-specific first.

    ydotoold's socket location varies by version/distro: the systemd unit commonly
    serves /tmp/.ydotool_socket, while the `ydotool` client defaults to
    $XDG_RUNTIME_DIR/.ydotool_socket. An explicit YDOTOOL_SOCKET always wins."""
    cands = []
    env = os.environ.get("YDOTOOL_SOCKET")
    if env:
        cands.append(env)
    cands.append("/tmp/.ydotool_socket")
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        cands.append(os.path.join(xdg, ".ydotool_socket"))
    # De-dup while preserving order.
    seen = set()
    return [c for c in cands if not (c in seen or seen.add(c))]


def _socket_live(path):
    """True if `path` is a ydotoold DGRAM socket that accepts a connection.

    ydotoold serves a SOCK_DGRAM (not SOCK_STREAM) Unix socket, so the probe must
    match that type - a STREAM connect fails with EPROTOTYPE even when the daemon
    is up, which would wrongly disable ydotool typing under Wayland."""
    if not os.path.exists(path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(path)
        s.close()
        return True
    except OSError:
        return False


def _resolve_ydotool_socket():
    """Return the first candidate socket a live daemon is listening on, or None.

    Picking the path that actually works (rather than a hardcoded default) means the
    client and daemon meet regardless of which location this ydotool build uses."""
    for path in _ydotool_socket_candidates():
        if _socket_live(path):
            return path
    return None


def _ydotoold_running():
    """ydotool needs the ydotoold daemon + a socket the user can reach. Return True
    only if some candidate socket actually accepts a connection (probed live each
    call), so a stale socket from a dead daemon is correctly reported as not running
    and a daemon that starts after import is still picked up."""
    return _resolve_ydotool_socket() is not None


# Linux keycodes (evdev/uinput) used by ydotool for synthetic key presses.
_YK_BACKSPACE = "14"
_YK_LEFT = "105"
_YK_RIGHT = "106"


def _ydotool_key(code, n):
    """Send `n` press/release cycles of uinput keycode `code` via ydotool.

    ydotool's `key` syntax is <keycode>:<pressed>, where :1 is key-DOWN and :0 is
    key-UP. Any other value (e.g. :2) is "non-interpretable" and only inserts a
    delay - so emitting `<code>:2` presses the key and NEVER releases it, which is
    why Backspace/arrow keys silently did nothing. Release MUST be :0."""
    if n <= 0:
        return []
    args = []
    for _ in range(n):
        args += [f"{code}:1", f"{code}:0"]
    return ["ydotool", "key"] + args


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
        # Resolve the live ydotoold socket once and export it, so every `ydotool` child
        # process inherits YDOTOOL_SOCKET and reaches the daemon. The client defaults to
        # $XDG_RUNTIME_DIR/.ydotool_socket while the systemd daemon commonly serves
        # /tmp/.ydotool_socket, so without this they never meet and typing silently no-ops.
        # setdefault-style: an explicit user YDOTOOL_SOCKET is preferred by the resolver.
        sock = _resolve_ydotool_socket()
        if sock:
            os.environ["YDOTOOL_SOCKET"] = sock
        self._ydotool_ok = self._has_ydotool and sock is not None

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
        if self._session == "wayland":
            # Wayland: ydotool (if its daemon is responsive) or pynput. xdotool is
            # X11-only and a no-op on Wayland, so it is never used here.
            if self._ydotool_ok:
                try:
                    r = subprocess.run(
                        ["ydotool", "type", text],
                        timeout=5.0, capture_output=True)
                    if r.returncode == 0:
                        return
                    # Daemon gone (e.g. stale socket from an exited daemon) - stop
                    # retrying ydotool and fall back to pynput for this text.
                    self._ydotool_ok = False
                except Exception:
                    self._ydotool_ok = False
            if self._has_ydotool and not self._warned_ydotool:
                self._warned_ydotool = True
                print("[linux] ydotoold not responding - falling back to pynput typing, which "
                      "needs X and fails under Wayland. Start it with: "
                      "sudo systemctl enable --now ydotool.service", file=sys.stderr, flush=True)
            # Last resort under Wayland: pynput needs an X connection Wayland hides, so
            # this only works under XWayland-with-auth. Guard it so a failure degrades to
            # a no-op (the commit still lands via clipboard paste) instead of crashing the
            # dictation thread.
            try:
                if self._kb is None:
                    from pynput.keyboard import Controller
                    self._kb = Controller()
                self._kb.type(text)
            except Exception:
                pass
            return
        # X11 / unknown session: xdotool when present (fall back to pynput on
        # failure), else pynput directly.
        if self._has_xdotool:
            r = subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--", text],
                timeout=5.0, capture_output=True)
            if r.returncode == 0:
                return
        if self._kb is None:
            from pynput.keyboard import Controller
            self._kb = Controller()
        self._kb.type(text)

    def backspace(self, n):
        """Send `n` Backspace keystrokes. Wayland: via ydotool (uinput); else pynput."""
        if n <= 0:
            return
        if self._ydotool_ok:
            subprocess.run(_ydotool_key(_YK_BACKSPACE, n), timeout=5.0)
            return
        try:
            if self._kb is None:
                from pynput.keyboard import Controller, Key
                self._kb = Controller()
                self._Key = Key
            for _ in range(n):
                self._kb.press(self._Key.backspace)
                self._kb.release(self._Key.backspace)
        except Exception:
            pass

    def move_cursor(self, delta):
        """Move the insertion point by `delta` chars (>0 right, <0 left). Wayland:
        via ydotool arrow keycodes; else pynput arrow keys."""
        if delta == 0:
            return
        if self._ydotool_ok:
            code = _YK_LEFT if delta < 0 else _YK_RIGHT
            subprocess.run(_ydotool_key(code, abs(delta)), timeout=5.0)
            return
        try:
            if self._kb is None:
                from pynput.keyboard import Controller, Key
                self._kb = Controller()
                self._Key = Key
            key = self._Key.right if delta > 0 else self._Key.left
            for _ in range(abs(delta)):
                self._kb.press(key)
                self._kb.release(key)
        except Exception:
            pass

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
        if self._session == "x11" and self._has_xdotool:
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
        # xdotool is X11-only; on Wayland it is a no-op, so don't even try it.
        if self._session != "x11" or not self._has_xdotool:
            return None
        try:
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True, text=True, timeout=1.0)
            return r.stdout.strip() or None
        except Exception:
            return None

    def supports_app_detection(self):
        return self._session == "x11" and self._has_xdotool
