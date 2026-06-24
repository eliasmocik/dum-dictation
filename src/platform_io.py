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

MacPlatform is implemented now. FallbackPlatform runs anywhere in degraded mode
(types via pynput instead of clipboard paste, no sounds, focus guard off) so the app
at least starts on Linux/Windows until native impls land. A future LinuxPlatform =
xclip/xdotool + a bell + wmctrl; WindowsPlatform = the clip + SendInput + GetForegroundWindow.
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
    return MacPlatform() if sys.platform == "darwin" else FallbackPlatform()
