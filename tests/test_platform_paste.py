#!/usr/bin/env python3
"""Test the clipboard-safe atomic paste algorithm (platform_io.MacPlatform.paste_atomic) — the fix for
the long-standing clipboard-clobber bug now that EVERY surface pastes (HUD/session model). We exercise
the ALGORITHM (snapshot -> set our text -> Cmd+V -> restore-on-success, leave-on-block) against an
in-memory fake clipboard, so the real NSPasteboard and a real Cmd+V are never touched."""
import copy
import platform_io

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"ok  {msg}")


TEXT_T = "public.utf8-plain-text"
RTF_T = "public.rtf"


class FakeClipboardMac(platform_io.MacPlatform):
    """MacPlatform with the five OS primitives replaced by an in-memory clipboard model, so paste_atomic
    runs its real logic without AppKit / Quartz / osascript. `secure` simulates a focused password field;
    `intruder` simulates a third party copying something new during the paste settle window."""

    def __init__(self, initial, secure=False, intruder=None):
        self._items = copy.deepcopy(initial)   # list of {type: bytes}
        self._cc = 10                           # arbitrary starting changeCount
        self._secure = secure
        self._intruder = intruder
        self.pastes_sent = 0

    # --- overridden primitives (in-memory) ---
    def _change_count(self):
        return self._cc

    def _clipboard_snapshot(self):
        return {"items": copy.deepcopy(self._items), "change_count": self._cc}

    def _clipboard_set_text(self, text):
        self._items = [{TEXT_T: text.encode("utf-8")}]
        self._cc += 1

    def _clipboard_restore(self, snap):
        self._items = copy.deepcopy(snap["items"])
        self._cc += 1

    def _secure_input_active(self):
        return self._secure

    def _send_paste(self):
        self.pastes_sent += 1
        if self._intruder is not None:          # someone copies AFTER our Cmd+V, before restore
            self._items = [{TEXT_T: self._intruder.encode("utf-8")}]
            self._cc += 1

    # helper for assertions
    def text(self):
        return self._items[0].get(TEXT_T, b"").decode("utf-8", "ignore") if self._items else ""


ORIGINAL = [{TEXT_T: b"USER ORIGINAL", RTF_T: b"{\\rtf USER}"}]

# --- success path: clipboard is restored to the user's original (full fidelity), paste was sent -------
p = FakeClipboardMac(ORIGINAL)
ok = p.paste_atomic("git status")
check(ok is True, "success: paste_atomic returns True")
check(p.pastes_sent == 1, "success: exactly one Cmd+V sent")
check(p.text() == "USER ORIGINAL", "success: user's clipboard text restored")
check(p._items[0].get(RTF_T) == b"{\\rtf USER}", "success: full fidelity — RTF type also restored")

# --- blocked path (secure input): returns False, our text LEFT on clipboard, NO paste, NO restore -----
p = FakeClipboardMac(ORIGINAL, secure=True)
ok = p.paste_atomic("sudo secret")
check(ok is False, "blocked: paste_atomic returns False on secure input")
check(p.pastes_sent == 0, "blocked: no Cmd+V attempted into a secure field")
check(p.text() == "sudo secret", "blocked: dictated text is LEFT on the clipboard for manual paste")

# --- intruder path: user copies during the settle window -> do NOT clobber their new content ----------
p = FakeClipboardMac(ORIGINAL, intruder="USER COPIED LATER")
ok = p.paste_atomic("npm install")
check(ok is True, "intruder: paste still reported success")
check(p.text() == "USER COPIED LATER",
      "intruder: a clipboard change during the window is preserved (no blind restore)")

print(f"\nALL {passed} CHECKS PASSED")
