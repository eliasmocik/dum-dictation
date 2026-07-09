#!/usr/bin/env python3
"""
Tests for the Step-4 ActivityMonitor: app-switch timeline + content-free keystroke proxy,
sliced per post-commit window. Drives the logs directly (no real keyboard / osascript).
Run: .venv/bin/python test_activity_monitor.py
"""
import time
from activity_monitor import ActivityMonitor

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"ok  {msg}")


t0 = time.time()

# --- moved-on case: commit in Code, then switches Terminal -> ChatGPT -> Safari ---
m = ActivityMonitor(frontmost_fn=lambda: None, keystrokes=False)
m._cur_app = "Code"
m._apps.append((t0, "Code"))                     # focused app at commit
m._apps.append((t0 + 3.2, "Terminal"))
m._apps.append((t0 + 11.7, "ChatGPT"))
m._apps.append((t0 + 18.4, "Safari"))
# 5 backspaces in Code (a correction) + 10 keystrokes in Terminal (working elsewhere)
for _ in range(5):
    m._keys.append((t0 + 1.0, "backspace", "Code"))
for _ in range(10):
    m._keys.append((t0 + 4.0, "other", "Terminal"))

w = m.window(t0, t0 + 20, commit_app="Code")
check([s["app"] for s in w["app_switches"]] == ["Terminal", "ChatGPT", "Safari"],
      "app switches captured in order during the window")
check(w["app_switches"][0]["t_rel"] == 3.2, "switch timestamp is relative to commit (+3.2s)")
check(w["final_app"] == "Safari", "final_app = app focused at end of window")
check(w["stayed_in_commit_app"] is False and w["switched_away_s"] == 3.2,
      "moved-on detected: first switch-away time recorded")
check(w["keystroke_summary"]["backspaces"] == 5 and w["keystroke_summary"]["other_keys"] == 0,
      "keystrokes GATED to commit app (Code backspaces counted; Terminal typing excluded)")

# --- stayed-and-edited case: no switches, backspaces in the commit app ---
m2 = ActivityMonitor(frontmost_fn=lambda: None, keystrokes=False)
m2._cur_app = "Code"
m2._apps.append((t0, "Code"))
for _ in range(3):
    m2._keys.append((t0 + 2.0, "backspace", "Code"))
w2 = m2.window(t0, t0 + 20, commit_app="Code")
check(w2["stayed_in_commit_app"] and w2["switched_away_s"] is None
      and w2["app_switches"] == [] and w2["keystroke_summary"]["backspaces"] == 3,
      "stayed-and-edited: no switch-away, backspaces counted in commit app")

# --- continued-working case: stayed, only forward typing (no backspaces) ---
m3 = ActivityMonitor(frontmost_fn=lambda: None, keystrokes=False)
m3._cur_app = "TextEdit"
m3._apps.append((t0, "TextEdit"))
for _ in range(12):
    m3._keys.append((t0 + 5.0, "other", "TextEdit"))
w3 = m3.window(t0, t0 + 20, commit_app="TextEdit")
check(w3["stayed_in_commit_app"] and w3["keystroke_summary"]["backspaces"] == 0
      and w3["keystroke_summary"]["other_keys"] == 12,
      "continued-working: stayed, forward typing, zero backspaces")

# --- record_key (fed by the app's single listener): stores category + app, never a character ---
m4 = ActivityMonitor(frontmost_fn=lambda: "Code", keystrokes=True)
m4._cur_app = "Code"
m4.record_key("backspace")
m4.record_key("other")
check(len(m4._keys) == 2 and m4._keys[0][1] == "backspace" and m4._keys[0][2] == "Code",
      "record_key stores (ts, category, app) only - no character")
# keystrokes disabled -> record_key is a no-op (DogfoodLogger gates via keystroke_proxy)
m4b = ActivityMonitor(frontmost_fn=lambda: "Code", keystrokes=False)
m4b.record_key("backspace")
check(len(m4b._keys) == 0, "record_key no-op when keystrokes disabled")

# --- self-typing exclusion: dum's own insertion/reconcile keystrokes are NOT counted ---
m6 = ActivityMonitor(frontmost_fn=lambda: None, keystrokes=False)
m6._cur_app = "Code"
m6._apps.append((t0, "Code"))
# dum inserts/reconciles at +1.0s..+1.3s (backspaces + retype) -> must be ignored
m6.mark_self_typing(t0 + 1.0, t0 + 1.3)
for _ in range(8):
    m6._keys.append((t0 + 1.1, "backspace", "Code"))   # dum's reconcile backspaces (synthetic)
m6._keys.append((t0 + 1.2, "other", "Code"))           # dum's retype (synthetic)
# the USER then really edits at +6s
for _ in range(2):
    m6._keys.append((t0 + 6.0, "backspace", "Code"))
w6 = m6.window(t0, t0 + 20, commit_app="Code")
check(w6["keystroke_summary"]["backspaces"] == 2 and w6["keystroke_summary"]["other_keys"] == 0,
      "self-typing excluded: only the 2 real user backspaces counted, dum's 8+1 ignored")

# pad covers async event delivery just after the marked interval
m7 = ActivityMonitor(frontmost_fn=lambda: None, keystrokes=False)
m7._cur_app = "Code"
m7._apps.append((t0, "Code"))
m7.mark_self_typing(t0 + 1.0, t0 + 1.2)                 # padded to +1.6s internally
m7._keys.append((t0 + 1.5, "backspace", "Code"))       # synthetic key delivered late, within pad
w7 = m7.window(t0, t0 + 20, commit_app="Code")
check(w7["keystroke_summary"]["backspaces"] == 0, "self-typing pad covers late-delivered synthetic keys")

# --- keystrokes outside the window are excluded ---
m5 = ActivityMonitor(frontmost_fn=lambda: None, keystrokes=False)
m5._cur_app = "Code"
m5._apps.append((t0, "Code"))
m5._keys.append((t0 - 5.0, "backspace", "Code"))     # before window
m5._keys.append((t0 + 25.0, "backspace", "Code"))    # after window
w5 = m5.window(t0, t0 + 20, commit_app="Code")
check(w5["keystroke_summary"]["backspaces"] == 0, "keystrokes outside [t0,t1] excluded")

print(f"\nALL {passed} CHECKS PASSED")
