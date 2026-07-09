#!/usr/bin/env python3
"""
Unit tests for the LLM correction GUARDS - the deterministic logic that decides whether and
where a homophone swap lands, WITHOUT loading the model. Run: .venv/bin/python test_llm_guard.py

Covers the two bugs the live feel-check surfaced:
  - grep over-correction onto ordinary 'grab' ("grab the database" / "grab a coffee")
  - wrong-occurrence: "grab a coffee ... grab the logs" must grep the LOGS one, not the coffee
"""
from llm_stage import _grep_match, _plausible

passed = 0


def check(desc, got, want):
    global passed
    ok = got == want
    print(("ok  " if ok else "XX  ") + f"{desc}: {got!r}")
    assert ok, f"{desc}: got {got!r} want {want!r}"
    passed += 1


def grep_occ(text):
    """Which 'grab' (0-indexed) gets turned into grep, or None."""
    m = _grep_match(text, "grab")
    return None if not m else text[:m.start()].count("grab")


# --- grep is gated on a search target in the next few words ---
check("coffee -> no grep", grep_occ("Let's grab a coffee"), None)
check("database -> no grep", grep_occ("Let's grab the database. Args are output and seconds"), None)
check("errors -> grep", grep_occ("Open the nginx config and grab the errors in the logs"), 0)
check("logs -> grep", grep_occ("please grab the logs now"), 0)

# --- wrong-occurrence regression: the LOGS grab, not the COFFEE grab ---
check("two grabs -> grep the second (logs)",
      grep_occ("Let's grab a coffee after we grab the logs"), 1)
check("two grabs, logs first -> grep the first",
      grep_occ("grab the logs then grab a coffee"), 0)

# --- _plausible still rejects phonetically-far nonsense the term-filter alone would pass ---
check("grab->grep plausible", _plausible("grab", "grep"), True)
check("coffee->grep implausible", _plausible("coffee", "grep"), False)
check("redis->ssh implausible", _plausible("redis", "ssh"), False)

print(f"\nALL {passed} CHECKS PASSED")
