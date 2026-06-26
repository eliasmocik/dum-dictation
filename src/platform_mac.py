#!/usr/bin/env python3
"""macOS platform backend (split out of platform_io.py).
Owner: Elias (@eliasmocik). The shared interface is platform_base.Platform; the dispatcher is
platform_io.get_platform(). OS-specific imports stay lazy/method-local."""
import subprocess
import sys
import time

from platform_base import Platform, PASTE_SETTLE_S


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
