#!/usr/bin/env python3
"""
Unit tests for the overlay engine's pure logic + the dry-run typer.

Run: .venv/bin/python test_overlay.py   (no pytest dependency; plain asserts.)
The point: prove the diff/reconcile is exact BEFORE it ever drives real keystrokes.
"""
from overlay import (stable_prefix, streaming_prefix, age_stable_count,
                     reconcile_ops, reconcile_words, min_edit_script, OverlayTyper,
                     alias_prefix_set, hold_alias_prefix)

passed = 0


def check(label, got, want):
    global passed
    assert got == want, f"FAIL {label}\n  got : {got!r}\n  want: {want!r}"
    passed += 1
    print(f"ok  {label}")


# ---- stable_prefix (two-preview agreement) -----------------------------------
# growing window, two previews agree on a prefix -> that prefix is stable
check("stable: agreement prefix",
      stable_prefix(["clone", "the"], ["clone", "the", "git"]),
      ["clone", "the"])

# the freshly-appeared last word is NOT stable yet (only in cur, not prev)
check("stable: tail word excluded",
      stable_prefix(["clone"], ["clone", "the"]),
      ["clone"])

# flicker: Parakeet changed word 0 between previews -> common prefix is empty
check("stable: flicker breaks prefix at 0",
      stable_prefix(["good", "repo"], ["git", "repo"]),
      [])

# normalized agreement: an early punctuation flip must NOT stall later words
check("stable: punctuation flip still agrees (current form)",
      stable_prefix(["Okay", "let's", "go"], ["Okay,", "let's", "go"]),
      ["Okay,", "let's", "go"])

# prev longer than cur (transcript shrank) -> only the still-agreeing prefix
check("stable: prev longer than cur",
      stable_prefix(["run", "npm", "install"], ["run", "npm"]),
      ["run", "npm"])

# full agreement -> whole list
check("stable: full agreement",
      stable_prefix(["a", "b", "c", "d"], ["a", "b", "c", "d"]),
      ["a", "b", "c", "d"])

check("stable: empty cur", stable_prefix(["a"], []), [])

# ---- streaming_prefix (what to show this preview, with eager) -----------------
# no agreement + no eager -> show nothing yet (the conservative default)
check("stream: no agreement, eager off",
      streaming_prefix([], ["open", "the", "config"]),
      [])
# no agreement + eager -> show just the first word from this single preview
check("stream: eager shows first word",
      streaming_prefix([], ["open", "the", "config"], eager_first=True),
      ["open"])
# when a stable prefix exists, eager defers to it (shows the full agreed run)
check("stream: agreement beats eager",
      streaming_prefix(["open", "the"], ["open", "the", "config"], eager_first=True),
      ["open", "the"])
# eager re-takes word 0 from a later single preview when the model revised it
# (this is the early flicker fix: 'try' -> 'transcriptor' shows the newest guess)
check("stream: eager follows a revised word 0",
      streaming_prefix(["try"], ["transcriptor", "as"], eager_first=True),
      ["transcriptor"])
# eager no-op on empty input
check("stream: eager empty preview", streaming_prefix([], [], eager_first=True), [])
# eager REFUSES a leading onset filler from a single preview (no "what"/"oh"/"okay"
# flash) - it waits for two-preview agreement instead
check("stream: eager skips leading filler",
      streaming_prefix([], ["what", "is", "the"], eager_first=True),
      [])
check("stream: eager skips filler regardless of case/punct",
      streaming_prefix([], ["Okay,", "let's", "go"], eager_first=True),
      [])
# but once two previews AGREE on that filler, it IS shown (a real "okay")
check("stream: confirmed filler still shows",
      streaming_prefix(["okay", "let's"], ["okay", "let's", "go"], eager_first=True),
      ["okay", "let's"])
# a non-filler first word still eager-shows immediately (unchanged behaviour)
check("stream: eager still shows real first word",
      streaming_prefix([], ["grep", "the", "logs"], eager_first=True),
      ["grep"])
# ---- breath-"yeah" suppression (at_start, two-preview path) -------------------
# a breath confirms "yeah" across two previews; at sentence start, HOLD it (don't show)
check("stream: at_start holds confirmed lone yeah",
      streaming_prefix(["yeah"], ["yeah"], eager_first=False, at_start=True),
      [])
check("stream: at_start holds yeah regardless of case/punct",
      streaming_prefix(["Yeah,"], ["Yeah,"], eager_first=False, at_start=True),
      [])
# loud-breath artifacts "H" / "Sh" are held the same way
check("stream: at_start holds lone breath 'H'",
      streaming_prefix(["H"], ["H"], eager_first=False, at_start=True),
      [])
check("stream: at_start holds lone breath 'Sh'",
      streaming_prefix(["Sh"], ["Sh"], eager_first=False, at_start=True),
      [])
# a real word joining a breath token releases it intact ("sh deploy.sh")
check("stream: at_start shows sh once a real word joins",
      streaming_prefix(["sh", "deploy.sh"], ["sh", "deploy.sh"], at_start=True),
      ["sh", "deploy.sh"])
# once a REAL word joins the agreement, show the whole run (a genuine "yeah let's")
check("stream: at_start shows yeah once a real word joins",
      streaming_prefix(["yeah", "let's"], ["yeah", "let's", "go"], at_start=True),
      ["yeah", "let's"])
# scope check: a genuine "so"/"okay" opener is NOT held by the breath gate
check("stream: at_start does NOT hold a real 'so' opener",
      streaming_prefix(["so", "the"], ["so", "the", "timeline"], at_start=True),
      ["so", "the"])
check("stream: at_start does NOT hold a lone 'okay' opener",
      streaming_prefix(["okay"], ["okay"], at_start=True),
      ["okay"])
# mid-sentence (something already typed -> at_start False) never suppresses yeah
check("stream: mid-sentence yeah is not held",
      streaming_prefix(["yeah"], ["yeah"], at_start=False),
      ["yeah"])


# ---- age_stable_count (Phase 1: one-by-one reveal by audio age) ---------------
# Five words at 0.5s spacing; live edge at 2.5s. A word is stable once its SUCCESSOR
# starts <= (window_len - margin). starts = [0.0, 0.5, 1.0, 1.5, 2.0].
STK = [0.0, 0.5, 1.0, 1.5, 2.0]
# LOCK margin 1.5 -> cutoff 1.0 -> words whose successor starts <=1.0: idx0 (succ 0.5),
# idx1 (succ 1.0). idx2's succ is 1.5 > 1.0 -> stop. => 2 stable.
check("age: lock margin (1.5) counts 2", age_stable_count(STK, 2.5, 1.5), 2)
# DISPLAY margin 0.7 -> cutoff 1.8 -> idx0,1,2 stable (succ 0.5/1.0/1.5 all <=1.8);
# idx3 succ 2.0 > 1.8 -> stop. => 3 stable (reveals MORE than the lock count).
check("age: display margin (0.7) counts 3", age_stable_count(STK, 2.5, 0.7), 3)
# smaller margin reveals at least as much as a larger one (monotonic)
check("age: display >= lock (monotonic)",
      age_stable_count(STK, 2.5, 0.7) >= age_stable_count(STK, 2.5, 1.5), True)
# always leaves >=1 word unstable: even with everything far in the past, last word held
check("age: keeps >=1 unstable", age_stable_count([0.0, 0.1, 0.2], 10.0, 0.5), 2)
# a single word can never be stable (no successor to fix its right boundary)
check("age: single word never stable", age_stable_count([0.0], 5.0, 0.5), 0)
check("age: empty -> 0", age_stable_count([], 5.0, 0.5), 0)
# a very large margin (>= window) reveals nothing yet (fully conservative)
check("age: margin >= window reveals nothing", age_stable_count(STK, 2.5, 2.5), 0)
# margin 0 still holds the last word (the forming one), not a free-for-all
check("age: zero margin still holds last word", age_stable_count(STK, 2.5, 0.0), 4)


# ---- reconcile_ops -----------------------------------------------------------
# identical -> no-op
check("recon: identical", reconcile_ops("git push", "git push"), (0, ""))

# pure append (complete the unlocked tail) -> 0 backspaces, type the suffix
check("recon: append tail",
      reconcile_ops("run npm", "run npm install"),
      (0, " install"))

# single-word fix near the start -> backspace to the divergence, retype
check("recon: fix near start",
      reconcile_ops("grab the logs", "grep the logs"),
      (len("ab the logs"), "ep the logs"))

# typed is longer than target (overshoot) -> backspace the excess, type nothing
check("recon: shrink",
      reconcile_ops("git checkout", "git"),
      (len(" checkout"), ""))

# empty typed -> just type everything
check("recon: from empty",
      reconcile_ops("", "git push"),
      (0, "git push"))


# ---- reconcile_words (low-churn: skip cosmetic-only leading words) -----------
# cosmetic-only diffs (period->comma, case) -> ZERO edits (the daily-driver win)
check("words: cosmetic-only is a no-op",
      reconcile_words("Hello. how are you?", "Hello, how are you?"),
      (0, ""))

# real word change still fixed, exploiting the shared 'gr' prefix
check("words: real fix near start",
      reconcile_words("grab the logs", "grep the logs"),
      (len("ab the logs"), "ep the logs"))

# cosmetic prefix kept, real fix later -> only the tail from the changed word churns
check("words: skip cosmetic, fix later",
      reconcile_words("run sudo q apply", "run sudo kubectl apply"),
      (len("q apply"), "kubectl apply"))

# pure tail completion (unlocked last word)
check("words: tail completion",
      reconcile_words("run npm", "run npm install"),
      (0, " install"))

# JSO -> JSON is meaningful (letters differ), shares the 'JSO' prefix
check("words: JSO->JSON",
      reconcile_words("write the JSO payload", "write the JSON payload"),
      (len(" payload"), "N payload"))

# THE PUNCTUATION-DROP BUG: cosmetic word-reconcile treats 'cross'->'cross?' as
# normalized-equal and types nothing, silently dropping the sentence-final '?'.
check("words: cosmetic DROPS trailing '?' (the bug)",
      reconcile_words("a cross", "a cross?"), (0, ""))
# exact char-reconcile (used at commit) KEEPS it.
check("ops: exact KEEPS trailing '?' (the fix)",
      reconcile_ops("a cross", "a cross?"), (0, "?"))
# same for a dropped period mid-paragraph
check("ops: exact KEEPS trailing '.'",
      reconcile_ops("my belief", "my belief."), (0, "."))


# ---- min_edit_script (Phase 2: multi-span surgical diff) ---------------------
# identical -> no edits
check("minedit: identical", min_edit_script("git push", "git push"), [])

# get->git: ONE in-place span (e->i), not a tail rewrite
check("minedit: get->git single char span",
      min_edit_script("get push", "git push"), [(1, 1, "i")])

# grab->grep + trailing period: TWO spans (the whole point of multi-span)
check("minedit: grab->grep + append '.'",
      min_edit_script("grab the logs", "grep the logs."),
      [(2, 2, "ep"), (13, 0, ".")])

# pure append (complete the unlocked tail) -> one zero-backspace span
check("minedit: pure append",
      min_edit_script("run npm", "run npm install"), [(7, 0, " install")])

# shrink (typed longer) -> one delete span, no text
check("minedit: shrink/delete",
      min_edit_script("git checkout", "git"), [(3, 9, "")])

# cosmetic trailing '?' kept (exact path) as a cheap end-append
check("minedit: trailing '?' append",
      min_edit_script("a cross", "a cross?"), [(7, 0, "?")])

# from empty -> single insert span
check("minedit: from empty",
      min_edit_script("", "git push"), [(0, 0, "git push")])

# too fragmented (>max_spans interleaved inserts) -> None => caller falls back
check("minedit: fragmented -> None",
      min_edit_script("0123456789", "0a1a2a3a4a5a6a7a8a9a"), None)


# ---- apply_edits / smart-edit reconcile (dry) end-to-end --------------------
# get->git via the smart path: surgical left/backspace/type/right, NOT a tail wipe
sm = OverlayTyper(dry=True, min_edit=True)
sm.append_words(["get", "push"])
ok = sm.reconcile("git push", exact=True)
check("smart: get->git applied", (ok, sm.typed), (True, "git push"))
check("smart: get->git op log is surgical",
      sm.ops,
      [("type", "get push"), ("left", 6), ("backspace", 1), ("type", "i"), ("right", 6)])

# multi-span grab->grep + '.' : right-to-left application, cursor returns to end
sm2 = OverlayTyper(dry=True, min_edit=True)
sm2.append_words(["grab", "the", "logs"])
ok = sm2.reconcile("grep the logs.", exact=True)
check("smart: multi-span applied", (ok, sm2.typed), (True, "grep the logs."))
check("smart: multi-span op log (period appended first, then grab->grep)",
      sm2.ops,
      [("type", "grab the logs"), ("type", "."),
       ("left", 10), ("backspace", 2), ("type", "ep"), ("right", 10)])

# smart-edit moves FEWER backspaces than the cursor-at-end path (the win)
smart_bs = sum(n for k, n in sm2.ops if k == "backspace")
plain_bs = reconcile_ops("grab the logs", "grep the logs.")[0]
check("smart: fewer backspaces than reconcile_ops", smart_bs < plain_bs, True)

# travel cap exceeded -> falls back to safe backspace-retype (no arrow keys)
cap = OverlayTyper(dry=True, min_edit=True, max_travel=2)
cap.append_words(["get", "push"])
ok = cap.reconcile("git push", exact=True)
check("smart: travel-cap falls back correctly", (ok, cap.typed), (True, "git push"))
check("smart: fallback uses no arrow keys",
      any(k in ("left", "right") for k, _ in cap.ops), False)

# min_edit ON must NOT touch the streaming (exact=False) path -> still word-reconcile
strm = OverlayTyper(dry=True, min_edit=True)
strm.append_words(["grab", "the", "logs"])
strm.reconcile("grep the logs")          # exact defaults False
check("smart: streaming path unaffected (no arrows)",
      any(k in ("left", "right") for k, _ in strm.ops), False)


# ---- OverlayTyper (dry) end-to-end -------------------------------------------
# simulate: live-lock "grab the logs", then reconcile to corrected "grep the logs"
t = OverlayTyper(dry=True)
t.append_words(["grab"])
check("typer: first append text", t.typed, "grab")
t.append_words(["the", "logs"])
check("typer: appended with spaces", t.typed, "grab the logs")
ok = t.reconcile("grep the logs")
check("typer: reconcile applied", (ok, t.typed), (True, "grep the logs"))
t.finish(" ")
check("typer: finish resets", t.typed, "")
# the recorded op stream is exactly what we'd send to the cursor
check("typer: op log",
      t.ops,
      [("type", "grab"), ("type", " the logs"),
       ("backspace", len("ab the logs")), ("type", "ep the logs"),
       ("type", " ")])

# safety: an oversized edit bails out without touching anything
big = OverlayTyper(dry=True, max_backspace=3)
big.append_words(["hello", "world"])
ok = big.reconcile("xyz")
check("typer: max_backspace bail-out", (ok, big.typed), (False, "hello world"))


# ---- streaming model end-to-end (reconcile-to-prefix each preview) ------------
def stream(previews, eager_first=True):
    """Drive an OverlayTyper through a sequence of previews exactly like live.py:
    reconcile the on-screen text to streaming_prefix() each tick. Returns the typer."""
    ov = OverlayTyper(dry=True)
    prev = []
    for words in previews:
        show = streaming_prefix(prev, words, eager_first=eager_first)
        if show:
            ov.reconcile(" ".join(show))
        prev = words
    return ov

# eager mis-guess 'try' self-corrects to 'transcriptor' MID-STREAM (the flicker fix):
# the wrong word does not survive to the end, and a backspace proves it was corrected
# live (not just appended around).
ov = stream([["try"], ["transcriptor", "as"], ["transcriptor", "as", "good"],
             ["transcriptor", "as", "good"]])
check("stream e2e: eager word-0 self-corrects", ov.typed, "transcriptor as good")
check("stream e2e: a mid-stream correction backspaced",
      any(k == "backspace" for k, _ in ov.ops), True)

# clean speech (no revision) stays append-only - NO backspaces, no churn
ov2 = stream([["open"], ["open", "the"], ["open", "the", "config"],
              ["open", "the", "config"]])
check("stream e2e: clean append result", ov2.typed, "open the config")
check("stream e2e: clean append has no backspaces",
      any(k == "backspace" for k, _ in ov2.ops), False)


# ---- hold_alias_prefix (reveal a multi-word alias whole, no typed-then-retyped letters) ------
PS = alias_prefix_set([["v", "s", "code"], ["vs", "code"], ["java", "script"], ["web", "socket"]])
check("alias_prefix_set: proper prefixes only (full alias excluded)",
      PS, frozenset({("v",), ("v", "s"), ("vs",), ("java",), ("web",)}))

# onset: a lone letter that starts an alias is held off-screen
check("hold: lone 'V' held", hold_alias_prefix(["V"], PS), [])
check("hold: 'V S' both held", hold_alias_prefix(["V", "S"], PS), [])
# resolved alias (corrector already produced 'VS Code') reveals immediately
check("hold: resolved 'VS Code' reveals", hold_alias_prefix(["VS", "Code"], PS), ["VS", "Code"])
# only the TRAILING in-progress run is held; earlier words stay shown
check("hold: trailing run held, prefix kept",
      hold_alias_prefix(["I", "love", "V", "S"], PS), ["I", "love"])
# broken prefix ('V S Go' != 'V S code') -> reveal everything (one beat later, never a retype)
check("hold: broken prefix reveals all", hold_alias_prefix(["V", "S", "Go"], PS), ["V", "S", "Go"])
# single-token acronym alias ('vs code') held until it resolves
check("hold: 'vs' single-token prefix held", hold_alias_prefix(["open", "vs"], PS), ["open"])
# unrelated words are never touched; empty prefix set is a pure passthrough
check("hold: no alias involvement", hold_alias_prefix(["hello", "world"], PS), ["hello", "world"])
check("hold: empty prefix set passthrough", hold_alias_prefix(["V", "S"], frozenset()), ["V", "S"])
check("hold: idempotent", hold_alias_prefix(hold_alias_prefix(["I", "V", "S"], PS), PS), ["I"])

# end-to-end: with the hold, the overlay types "VS Code" in ONE shot - NO backspace/retype.
# previews arrive already corrected (live.py runs the preview corrector first), so the letters
# merge to "VS Code" on the tick "code" lands.
def stream_held(previews, prefix_set):
    ov = OverlayTyper(dry=True)
    prev = []
    for words in previews:
        show = streaming_prefix(prev, words, eager_first=True)
        show = hold_alias_prefix(show, prefix_set)
        if show:
            ov.reconcile(" ".join(show))
        prev = words
    return ov

ovh = stream_held([["V"], ["V", "S"], ["VS", "Code"], ["VS", "Code"]], PS)
check("hold e2e: result is 'VS Code'", ovh.typed, "VS Code")
check("hold e2e: NO backspace (revealed whole, never retyped)",
      any(k == "backspace" for k, _ in ovh.ops), False)
# and nothing leaks to screen before it resolves: only the final 'VS Code' is ever typed
check("hold e2e: no partial 'V'/'V S' typed before resolve",
      [t for k, t in ovh.ops if k == "type" and t.strip()], ["VS Code"])


print(f"\nALL {passed} CHECKS PASSED")
