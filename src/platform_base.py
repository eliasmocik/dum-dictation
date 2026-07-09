#!/usr/bin/env python3
"""
Platform I/O surface - the ONE place OS-specific behaviour lives, so Linux/Windows
are a drop-in later instead of a retrofit.

The rest of the stack is already portable by construction:
  * inference  - Parakeet via sherpa-onnx (ONNX runtime), not MLX, so the engine
                 isn't Mac-locked; the optional homophone LLM (MLX) sits behind the
                 pipeline `Stage` interface and can be swapped for a GGUF/llama.cpp
                 backend on other platforms with no core change.
  * audio in   - sounddevice (PortAudio): cross-platform.
  * hotkey     - pynput global hotkey: cross-platform.
  * overlay typing - pynput keyboard (type + backspace): cross-platform.

Only three things are actually OS-specific, and they live here behind `Platform`:
  * paste(text)        - put text at the cursor via the clipboard
  * notify(event)      - start/done/empty sound cue
  * frontmost_app()    - focused-app name, for the overlay focus guard

MacPlatform (Quartz/AppKit), WindowsPlatform (SendInput + win32clipboard + winsound +
GetForegroundWindow) and LinuxPlatform (xdotool + xclip/wl-clipboard + a bell) are implemented.
FallbackPlatform is the last resort for any other OS (types via pynput, no sounds, focus guard
off) so the app at least starts; LinuxPlatform itself degrades to that same behaviour when the
X11 CLI tools aren't present (e.g. a bare Wayland session - see its docstring).

Note the native platforms override type_text too (not just paste/notify/frontmost): pynput types
through the active keyboard layout, so a dead-key layout mangles output - instead they post raw
Unicode (CGEvent on mac, SendInput KEYEVENTF_UNICODE on Windows, `xdotool type` on Linux/X11).
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
        paste() path with no clipboard preservation (degraded - used by the cross-platform fallback,
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


class FallbackPlatform(Platform):
    """Runs anywhere: paste by synthetic typing (pynput), no sounds, no focus guard."""
    def __init__(self):
        self._kb = None

    def paste(self, text):
        if self._kb is None:
            from pynput.keyboard import Controller
            self._kb = Controller()
        self._kb.type(text)
