#!/usr/bin/env python3
"""Unit tests for scripts/join_claude_transcripts.py — the Claude Code transcript join that recovers
the EXACT post-commit edit signal for dictation into the Claude Code prompt (which no VS Code
extension can read). Tests the pure core: timestamp parsing, windowed best-match, and join_session."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import join_claude_transcripts as J

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    print(f"ok  {msg}")
    passed += 1


# --- 1. ISO-8601 (Claude transcript) -> epoch, comparable to commit ts (time.time) ---
e1 = J._iso_to_epoch("2026-06-20T08:26:57.778Z")
e2 = J._iso_to_epoch("2026-06-20T08:27:57.778Z")
check(isinstance(e1, float) and e2 is not None and abs((e2 - e1) - 60.0) < 0.01,
      "_iso_to_epoch parses ISO 'Z' UTC to epoch (60s apart -> 60s)")
check(J._iso_to_epoch("not-a-date") is None and J._iso_to_epoch(None) is None,
      "_iso_to_epoch returns None on garbage / missing (never raises)")

# --- 2. best_match: pick the highest-partial-ratio human message inside the time window ---
T0 = 1_000_000.0
msgs = [
    (T0 - 100, "totally unrelated earlier message about lunch"),       # out of window (before)
    (T0 + 5, "I have started the dictation tool again. Can we test it?"),  # the real submit
    (T0 + 8, "something else entirely different here"),
    (T0 + 5000, "I have started the dictation tool again"),            # out of window (too late)
]
m = J.best_match("I have started the location tool again. Can we test it?", T0, msgs)
check(m is not None and m[1].startswith("I have started the dictation tool"),
      "best_match selects the in-window message the committed text best aligns to")
check(J.best_match("a string that appears nowhere in any candidate message at all", T0, msgs) is None,
      "best_match returns None when nothing clears MATCH_THRESHOLD")
check(J.best_match("I have started the dictation tool again. Can we test it?",
                   T0 - 10_000, [(T0 + 5, "I have started the dictation tool again. Can we test it?")]) is None,
      "best_match respects the window (a match far outside WINDOW_AFTER_S is rejected)")

# --- 3. join_session: matching VS Code commit -> exact claude-transcript refix, surface claude-code ---
commits = [
    {"commit_id": "s-0", "surface": "vscode", "app": "Code", "ts": T0,
     "fixed": "I have started the location tool again. Can we test it?"},        # corrected: location->dictation
    {"commit_id": "s-1", "surface": "vscode", "app": "Code", "ts": T0,
     "fixed": "something else entirely different here"},                          # accepted unchanged
    {"commit_id": "s-2", "surface": "rich-text", "app": "Notes", "ts": T0,
     "fixed": "I have started the dictation tool again. Can we test it?"},        # surface not eligible -> skip
    {"commit_id": "s-3", "surface": "vscode", "app": "Code", "ts": T0,
     "fixed": "no corresponding submitted message whatsoever exists for this one"},  # no match -> skip
]
evs = J.join_session(commits, msgs)
by_id = {e["commit_id"]: e for e in evs}

check(set(by_id) == {"s-0", "s-1"},
      "join_session matches only eligible-surface commits that hit a message (Notes + no-match skipped)")
check(all(e["capture_method"] == "claude-transcript" and e["surface_refined"] == "claude-code"
          and e["type"] == "user.refix" and e["edit_capture"] == "ok" for e in evs),
      "join_session emits claude-transcript / claude-code user.refix events")
check(by_id["s-0"]["edit_distance"] > 0 and not by_id["s-0"]["accepted_unchanged"]
      and by_id["s-0"].get("correction_pair", {}).get("corrected_span"),
      "corrected commit: edit_distance>0, accepted_unchanged False, correction_pair captured (location->dictation)")
check(by_id["s-1"]["edit_distance"] == 0 and by_id["s-1"]["accepted_unchanged"] is True,
      "unchanged commit: edit_distance 0, accepted_unchanged True")
check(all("ts" in e and isinstance(e["match_score"], (int, float)) for e in evs),
      "events carry the submitted-message ts and the fuzzy match_score")

# --- 4. JOIN_SURFACES scope guard: browser/rich-text never join even on a perfect text match ---
only_browser = J.join_session(
    [{"commit_id": "b-0", "surface": "browser", "app": "Safari", "ts": T0,
      "fixed": "something else entirely different here"}], msgs)
check(only_browser == [], "browser/rich-text commits are out of JOIN_SURFACES scope (no false claude-code tag)")

print(f"\nALL {passed} CHECKS PASSED")
