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
_YK_LEFTCTRL = "29"
_YK_V = "47"


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
        # wtype: a daemon-less Wayland typing tool. Debian (and some other distros)
        # don't package ydotool at all, so wtype is the only working Wayland typing
        # backend there. It can type UTF-8 text but cannot send raw keycodes
        # (Backspace / arrows / Ctrl+V), so it covers sentence typing but not the
        # live overlay's character edits or paste - those still need ydotool.
        self._has_wtype = bool(shutil.which("wtype"))
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
        self._Key = None
        self._warned_ydotool = False
        # Surface a Wayland-typing problem up front (before the first dictation), so a
        # missing/dead ydotoold isn't a silent "dictation works but types nothing".
        if self._session == "wayland" and not self._ydotool_ok:
            self._warn_ydotool_once()

    def _ydotool_ready(self):
        """Whether a ydotool call should be attempted, re-probing the daemon socket.

        `self._ydotool_ok` is cleared to False by `_run_ydotool` whenever a call
        fails (daemon died / stale socket), but the daemon may come up (or recover)
        later - e.g. `systemctl enable --now ydotool.service` after launch, or a
        transient first-call error. Re-resolving the live socket here lets typing be
        promoted back to ydotool instead of being permanently stuck on pynput."""
        if not self._has_ydotool:
            return False
        if self._ydotool_ok:
            return True
        sock = _resolve_ydotool_socket()
        if sock:
            os.environ["YDOTOOL_SOCKET"] = sock
            self._ydotool_ok = True
            return True
        return False

    def _xdotool_usable(self):
        """xdotool works under XWayland too (XDG_SESSION_TYPE=wayland but DISPLAY set),
        not just pure X11. It needs an X display, so gate it on $DISPLAY rather than on
        the session type, so it can serve as a Wayland fallback for X11/XWayland apps."""
        return self._has_xdotool and bool(os.environ.get("DISPLAY"))

    def _run_ydotool(self, args):
        """Run a ydotool subprocess; return True only on a clean (exit 0) run.

        On any failure - non-zero exit or an exception, e.g. the daemon died or its
        socket went stale mid-session - mark ydotool unavailable so later calls fall
        back (pynput/wtype/xdotool). It can be re-promoted later via _ydotool_ready."""
        try:
            if subprocess.run(args, timeout=5.0, capture_output=True).returncode == 0:
                return True
        except Exception:
            pass
        self._ydotool_ok = False
        return False

    def _pynput_tap(self, key_name, n):
        """Fallback: tap the pynput special key named `key_name` (e.g. 'backspace',
        'left', 'right') `n` times. Guarded so a missing/blocked pynput degrades to a
        no-op instead of crashing the dictation thread (on Wayland pynput needs X)."""
        if n <= 0:
            return
        try:
            if self._kb is None:
                from pynput.keyboard import Controller, Key
                self._kb = Controller()
                self._Key = Key
            key = getattr(self._Key, key_name)
            for _ in range(n):
                self._kb.press(key)
                self._kb.release(key)
        except Exception:
            pass

    def _warn_ydotool_once(self):
        if self._warned_ydotool:
            return
        self._warned_ydotool = True
        if self._session == "wayland":
            # Under Wayland there is no X, so the pynput fallback below cannot send
            # keys - it fails silently and the user is left with text that dictation
            # heard but never typed. Say so loudly and point at the fix.
            if not self._has_ydotool:
                if self._has_wtype:
                    print("[linux] ydotool is NOT installed (Debian doesn't package it) - "
                          "falling back to 'wtype' for Wayland typing. Whole sentences will "
                          "be typed, but the live overlay's character edits and clipboard "
                          "paste still need ydotool; build it from source for full support.",
                          file=sys.stderr, flush=True)
                else:
                    print("[linux] ydotool is NOT installed - Wayland typing has nothing to "
                          "fall back to, so nothing will be typed.\n"
                          "        Debian doesn't package ydotool; install 'wtype' instead:\n"
                          "        sudo apt install wtype\n"
                          "        (or build ydotool from source for full overlay + paste support)",
                          file=sys.stderr, flush=True)
            else:
                print("[linux] ydotoold not responding - falling back to pynput typing, which "
                      "needs X and fails under Wayland. Start it with: "
                      "sudo systemctl enable --now ydotool.service", file=sys.stderr, flush=True)

    def type_text(self, text):
        if not text:
            return
        if self._session == "wayland":
            # Wayland: ydotool (if its daemon is responsive), else xdotool under XWayland,
            # else wtype (daemon-less, Debian-friendly), else pynput.
            # Order matters: xdotool is checked before wtype because wtype silently exits
            # non-zero on GNOME/KDE Wayland (most Wayland desktops) without typing anything,
            # and we must not return success on a failed wtype call.
            if self._ydotool_ready() and self._run_ydotool(["ydotool", "type", text]):
                return
            # XWayland fallback: xdotool (and pynput) reach X11/XWayland apps even in a
            # Wayland session as long as $DISPLAY is set.
            if self._xdotool_usable():
                try:
                    r = subprocess.run(
                        ["xdotool", "type", "--clearmodifiers", "--", text],
                        timeout=5.0, capture_output=True)
                    if r.returncode == 0:
                        return
                except Exception:
                    pass
            # wtype: daemon-less Wayland typing tool. Debian (and some other distros)
            # don't package ydotool at all, so wtype is the only working Wayland typing
            # backend there. It can type UTF-8 text but cannot send raw keycodes
            # (Backspace / arrows / Ctrl+V), so it covers sentence typing but not the
            # live overlay's character edits or paste - those still need ydotool.
            # Check returncode: wtype exits non-zero on GNOME/KDE Wayland without typing,
            # so we must not return success on failure.
            if self._has_wtype:
                try:
                    r = subprocess.run(["wtype", text], timeout=5.0, capture_output=True)
                    if r.returncode == 0:
                        return
                except Exception:
                    pass
            self._warn_ydotool_once()
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
        """Send `n` Backspace keystrokes. Wayland: via ydotool (uinput), falling back
        to pynput if the daemon is down/died; X11/other: pynput."""
        if n <= 0:
            return
        if self._ydotool_ready() and self._run_ydotool(_ydotool_key(_YK_BACKSPACE, n)):
            return
        if self._xdotool_usable():
            try:
                r = subprocess.run(
                    ["xdotool", "key", "--clearmodifiers"] + ["BackSpace"] * n,
                    timeout=5.0, capture_output=True)
                if r.returncode == 0:
                    return
            except Exception:
                pass
        self._pynput_tap("backspace", n)

    def move_cursor(self, delta):
        """Move the insertion point by `delta` chars (>0 right, <0 left). Wayland: via
        ydotool arrow keycodes, falling back to pynput if the daemon died; else pynput."""
        if delta == 0:
            return
        code = _YK_LEFT if delta < 0 else _YK_RIGHT
        if self._ydotool_ready() and self._run_ydotool(_ydotool_key(code, abs(delta))):
            return
        if self._xdotool_usable():
            key = "Right" if delta > 0 else "Left"
            try:
                r = subprocess.run(
                    ["xdotool", "key", "--clearmodifiers"] + [key] * abs(delta),
                    timeout=5.0, capture_output=True)
                if r.returncode == 0:
                    return
            except Exception:
                pass
        self._pynput_tap("right" if delta > 0 else "left", abs(delta))

    def _clip_get(self):
        # timeout: a wedged clipboard daemon must not hang the dictation thread.
        try:
            if self._clip == "wayland":
                r = subprocess.run(["wl-paste", "-n"], capture_output=True,
                                   text=True, timeout=2.0)
            elif self._clip == "xclip":
                r = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                   capture_output=True, text=True, timeout=2.0)
            else:
                return None
        except Exception:
            return None
        return r.stdout if r.returncode == 0 else None

    def _clip_set(self, text):
        try:
            if self._clip == "wayland":
                subprocess.run(["wl-copy"], input=text, text=True, timeout=2.0)
            elif self._clip == "xclip":
                subprocess.run(["xclip", "-selection", "clipboard"], input=text,
                               text=True, timeout=2.0)
        except Exception:
            pass

    def _send_paste(self):
        if self._session == "x11" and self._has_xdotool:
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                           timeout=5.0, capture_output=True)
            return
        # Wayland: send Ctrl+V via ydotool (Ctrl down, V down/up, Ctrl up). pynput's
        # Ctrl+V needs an X connection Wayland hides, so it's only the last resort.
        if self._session == "wayland" and self._ydotool_ready() and self._run_ydotool(
                ["ydotool", "key", f"{_YK_LEFTCTRL}:1", f"{_YK_V}:1",
                 f"{_YK_V}:0", f"{_YK_LEFTCTRL}:0"]):
            return
        # XWayland: xdotool Ctrl+V reaches X11/XWayland apps and is more reliable than
        # pynput; try it before the pynput last resort.
        if self._xdotool_usable():
            try:
                if subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                                  timeout=5.0, capture_output=True).returncode == 0:
                    return
            except Exception:
                pass
        try:
            from pynput.keyboard import Controller, Key
            kb = Controller()
            with kb.pressed(Key.ctrl):
                kb.press("v")
                kb.release("v")
        except Exception:
            pass

    def paste(self, text):
        if self._clip and self._can_paste():
            self._clip_set(text)
            self._send_paste()
        else:
            self.type_text(text)

    def paste_atomic(self, text):
        if not self._clip or not self._can_paste():
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

    def supports_overlay(self):
        """The live overlay does character-level Backspace/arrow edits; on Wayland that
        needs ydotool's raw uinput keycodes, which reach *any* app. xdotool is
        deliberately NOT accepted here: its keystrokes only land in X11/XWayland apps, so
        driving the overlay with it would backspace the wrong place in a native Wayland
        app. wtype can type text but not keycodes and pynput can't see keys under Wayland,
        so without ydotool callers must fall back to commit-only typing."""
        if self._session == "wayland":
            return self._ydotool_ready()
        return True

    def _can_paste(self):
        """Ctrl+V paste routes through xdotool (X11/XWayland) or ydotool (Wayland). On a
        pure-Wayland session with neither, there's no working paste, so callers should
        type_text instead. xdotool is NOT counted as paste-capable on Wayland: it only
        reaches X11/XWayland apps (gated on $DISPLAY, which GNOME/KDE always set), so
        Ctrl+V into a native Wayland app silently does nothing."""
        if self._session == "wayland":
            return self._ydotool_ready()
        return True
