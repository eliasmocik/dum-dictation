#!/usr/bin/env python3
"""
Tests for the dogfood logger + user-correction analyzer.
Run: .venv/bin/python test_dogfood_log.py
"""
import os
import json
import time
import importlib.util
import dogfood_log as dl

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"ok  {msg}")


# --- 1. commit event schema ---
os.environ["DUM_DOGFOOD_LOG"] = "0"          # construct without touching disk
dl._ax_window_title = lambda: "pipeline.py - bakeoff"   # deterministic (no GUI focus in tests)
log = dl.DogfoodLogger(path="/tmp/dum_dogfood_test.jsonl")
_STAGES = [{"type": "correction.applied", "stage": "phonetic",
            "before": "get push", "after": "git push", "ms": 0.4}]
cid, evt = log.commit_event("get push", "git push", app="Code",
                            mode="overlay", latency_ms=312.7, stages=_STAGES)
REQUIRED = {"schema", "type", "commit_id", "session", "cwd", "repo_root", "app", "surface",
            "window_title", "mode", "raw", "fixed", "model", "flags", "latency_ms", "stages_fired",
            "audio_ref"}
check(REQUIRED <= set(evt), f"commit event has all required fields (missing {REQUIRED - set(evt)})")
check(evt["type"] == "commit", "type=commit")
check(evt["audio_ref"] is None, "audio_ref None in pure commit_event (set later by log_commit)")
# derivable fields are NOT stored (analyzers compute them from `fixed`)
check(not ({"changed", "committed_len", "n_words"} & set(evt)),
      "derivable fields (changed/committed_len/n_words) dropped from commit record")
check(evt["latency_ms"] == 313 and set(evt["flags"]) == {"global_vocab", "repo_vocab", "fuzzy_symbols", "llm"},
      "latency rounded + flags present")
check(evt["schema"] == 5, "schema version bumped to 5 (surface buckets: shell / coarse vscode)")

# --- 1a. real surface bucket + best-effort window_title ---
# VS Code family -> coarse "vscode" (analyzer refines to editor/vscode-terminal/claude-code post-hoc).
check(evt["surface"] == "vscode", "surface derived from app: Code -> vscode (coarse parent)")
check(evt["window_title"] == "pipeline.py - bakeoff", "window_title captured (best-effort AX)")
check(dl.classify_surface("iTerm2") == "shell" and dl.classify_surface("Google Chrome") == "browser"
      and dl.classify_surface("Cursor") == "vscode" and dl.classify_surface("Sublime Text") == "editor"
      and dl.classify_surface("Notes") == "rich-text" and dl.classify_surface("Some Random App") == "unknown"
      and dl.classify_surface(None) == "unknown",
      "classify_surface: shell/vscode/editor/browser/rich-text/unknown buckets (unknown, not a real surface)")

# --- 1b. stages_fired: embedded stage trace, bus-only `type` stripped, ms kept ---
sf = evt["stages_fired"]
check(len(sf) == 1 and sf[0]["stage"] == "phonetic" and sf[0]["after"] == "git push"
      and sf[0]["before"] == "get push" and sf[0]["ms"] == 0.4 and "type" not in sf[0],
      "stages_fired embeds {stage,before,after,ms}, drops bus-only `type`")
# no stages passed -> empty trace (not None), so the analyzer can always iterate
_, evt0 = log.commit_event("hi", "hi", app="Notes", mode="paste")
check(evt0["stages_fired"] == [] and evt0["surface"] == "rich-text",
      "stages_fired defaults to [] when no stages passed; Notes -> rich-text")

# --- 1c. audio retention: WAV saved + audio_ref populated; off when disabled; size-prune ---
import glob
import tempfile
import numpy as np
_sig = (np.arange(8000, dtype="float32") % 100) / 100.0          # deterministic 0.5s @ 16k
with tempfile.TemporaryDirectory() as _ad:
    os.environ["DUM_DOGFOOD_LOG"] = "1"
    os.environ["DUM_KEEP_AUDIO"] = "1"
    _ax_real = dl._ax_focused_value
    dl._ax_focused_value = lambda: None                          # keep observer thread instant/quiet
    alog = dl.DogfoodLogger(path=os.path.join(_ad, "dogfood", "sessions", "s.jsonl"))
    alog.log_commit("hello world", "hello world", app="Code", mode="overlay", audio=_sig, sr=16000)
    c = [json.loads(l) for l in open(alog.path) if l.strip() and json.loads(l)["type"] == "commit"][0]
    ref = c["audio_ref"]
    check(ref is not None and ref["seconds"] == 0.5 and len(ref["sha256"]) == 64,
          f"audio_ref populated (seconds + sha256): {ref}")
    check(os.path.exists(ref["path"]) and ref["path"].endswith(".wav"), "utterance WAV written to disk")

    # disabled: DUM_KEEP_AUDIO=0 -> no file, audio_ref stays None
    os.environ["DUM_KEEP_AUDIO"] = "0"
    blog = dl.DogfoodLogger(path=os.path.join(_ad, "dogfood2", "sessions", "s.jsonl"))
    blog.log_commit("hi", "hi", app="Code", mode="overlay", audio=_sig, sr=16000)
    cb = [json.loads(l) for l in open(blog.path) if l.strip() and json.loads(l)["type"] == "commit"][0]
    check(cb["audio_ref"] is None, "audio_ref None when DUM_KEEP_AUDIO=0 (no clip saved)")

    # size-prune: 3 clips, cap ~1.5 clips -> oldest dropped, 1 kept
    os.environ["DUM_KEEP_AUDIO"] = "1"
    plog = dl.DogfoodLogger(path=os.path.join(_ad, "dogfood3", "sessions", "s.jsonl"))
    for i in range(3):
        plog.log_commit(f"c{i}", f"c{i}", app="Code", mode="overlay", audio=_sig, sr=16000)
    _wavs = glob.glob(os.path.join(_ad, "dogfood3", "audio", "**", "*.wav"), recursive=True)
    _one = os.path.getsize(_wavs[0])
    plog._prune_audio(max_days=99999, max_gb=(_one * 1.5) / 1024 ** 3)
    _after = glob.glob(os.path.join(_ad, "dogfood3", "audio", "**", "*.wav"), recursive=True)
    check(len(_wavs) == 3 and len(_after) == 1, f"size-prune keeps newest within cap (3 -> {len(_after)})")
    dl._ax_focused_value = _ax_real                              # restore real AX reader

# --- 1d. exit-flush: close() wakes pending observers fast (no quit-too-soon loss) ---
with tempfile.TemporaryDirectory() as _fd:
    os.environ["DUM_DOGFOOD_LOG"] = "1"
    _ax_real2 = dl._ax_focused_value
    dl._ax_focused_value = lambda: None
    flog = dl.DogfoodLogger(path=os.path.join(_fd, "dogfood", "sessions", "s.jsonl"), window_s=30)
    flog.log_commit("x", "x", app="Code", mode="overlay")        # observer would otherwise wait 30s
    _t0 = time.monotonic()
    flog.close()                                                 # must wake + write fast, not wait 30s
    _elapsed = time.monotonic() - _t0
    _rx = [json.loads(l) for l in open(flog.path) if l.strip() and json.loads(l)["type"] == "user.refix"]
    check(len(_rx) == 1 and _elapsed < 5,
          f"close() flushes pending observer fast (elapsed {_elapsed:.2f}s, refix written {len(_rx)})")
    dl._ax_focused_value = _ax_real2
os.environ["DUM_DOGFOOD_LOG"] = "0"                            # restore for later sections

# --- 2. edit-distance + 3. accepted-unchanged detection ---
acc = dl.edit_signal("git push origin main", "so I ran git push origin main and it worked")
check(acc["accepted_unchanged"] and acc["edit_distance"] == 0, "verbatim present -> accepted, dist 0")
rep = dl.edit_signal("git push origin main", "git pull origin main")
check(not rep["accepted_unchanged"] and rep["edit_distance"] == 2,
      f"push->pull -> dist 2 (got {rep['edit_distance']})")
check(0 < rep["normalized"] < 0.2, "normalized rate small for a 2-char edit")
dl.KEEP_CORRECTIONS = True            # Step 7: this now defaults to DUM_DOGFOOD_FULL (off in tests)
rep = dl.edit_signal("git push origin main", "git pull origin main")
check(rep.get("correction_pair") == {"committed_span": "push", "corrected_span": "pull"},
      f"correction_pair = minimal changed token (got {rep.get('correction_pair')})")
empty = dl.edit_signal("", "anything")
check(empty["accepted_unchanged"], "empty inserted -> accepted")
# correction_pair isolates the changed word even embedded in surrounding text
emb = dl.edit_signal("deploy to postgress now", "so we deploy to PostgreSQL now ok")
check(emb.get("correction_pair") == {"committed_span": "postgress", "corrected_span": "PostgreSQL"},
      f"correction_pair isolates changed word in context (got {emb.get('correction_pair')})")

# --- Part A: trim neighbour-commit BLEED - anchor the aligned region to the committed word-extent ---
check(dl._trim_field_bleed("of brew whenever you know", "ofbBre whenever you know Should")
      == "ofbBre whenever you know", "_trim_field_bleed drops a trailing neighbour word")
check(dl._trim_field_bleed("So nothing changed", "anymore. So nothing changed")
      == "So nothing changed", "_trim_field_bleed drops a leading neighbour word")
check(dl._trim_field_bleed("push origin", "git push origin main")
      == "push origin", "_trim_field_bleed trims BOTH sides to the committed extent")
check(dl._trim_field_bleed("git pull origin main", "git pull origin main")
      == "git pull origin main", "_trim_field_bleed is a no-op when there is no bleed")
# end-to-end: bleed words from neighbouring text must NOT leak into the stored correction_pair
bleed = dl.edit_signal("the kubectl command", "earlier words the cube control command and more after")
cp = bleed.get("correction_pair") or {}
check("earlier" not in cp.get("corrected_span", "") and "after" not in cp.get("corrected_span", "")
      and "control" in cp.get("corrected_span", ""),
      f"edit_signal: neighbour bleed trimmed from correction_pair (got {cp})")
# --- 2b. classify_correction: separate genuine corrections from dum's own insertion corruption ---
# The capture layer faithfully records what landed in the field, but that can be a real user fix, a
# scramble (the overlay corrupting its OWN output, Part C), neighbour bleed, or trivial punct/case
# noise. Misclassifying scramble/bleed as a "user correction" was the bug that made the telemetry
# untrustworthy (inflated rate, polluted vocab). These cases come straight from real dogfood logs.
_CC = dl.classify_correction
# genuine corrections (word -> different word, or a meaningful technical join/split) MUST survive
for a, b in [("cloud code", "Claude Code"), ("thing", "VSCODE"), ("Jetson", "Rado's"),
             ("joint", "join"), ("Uh mail", "school email"), ("BC still.", "PC still"),
             ("git hub", "GitHub"), ("web socket", "WebSocket"), ("sherpa onnx", "sherpa-onnx"),
             ("deploy to postgress", "deploy to PostgreSQL")]:
    check(_CC(a, b) == "clean", f"classify_correction: genuine '{a}'->'{b}' = clean (kept as learning signal)")
# scrambles (same letters, sequence/spacing churned) - dum's insertion-corruption bug, NOT a fix
for a, b in [("it. Uh Get service SH SSHD worked. However,", "it U.h get Sesvice s SHSHD worked, .HHwever,,"),
             ("tool. Uh however", "too.l Uhhhoweve"), ("of brew", "ofbBre")]:
    check(_CC(a, b) == "scramble", f"classify_correction: scramble '{a}'->'{b}' = scramble (overlay corruption)")
# bleed/accumulation - committed survives as an ordered subsequence inside a longer corrected (a
# neighbour commit merged, or the user kept writing) - NOT a scramble of this text, NOT a fix
check(_CC("Everything else can be plugged in.", "jumper.Everything else can be plugged in,.") == "bleed",
      "classify_correction: neighbour bleed = bleed (not a correction of this text)")
check(_CC("Tool that I use.", "tool that I use for") == "bleed",
      "classify_correction: user kept writing (subsequence preserved + appended) = bleed, not scramble")
# trivial - letter-identical, no alnum-token-boundary change: punct/case noise (incl. bad-space split)
for a, b in [("Check.", "Check"), ("Global", "global"), ("weird.", "weird ."), ("two. However,", "two., however")]:
    check(_CC(a, b) == "trivial", f"classify_correction: trivial '{a}'->'{b}' = trivial (low-signal, not a vocab candidate)")
# edit_signal tags the pair kind inline so live logs self-describe
tagged = dl.edit_signal("the auto DS jumper", "the auto DS jmuper")
check(tagged.get("pair_kind") == dl.classify_correction(
        (tagged.get("correction_pair") or {}).get("committed_span"),
        (tagged.get("correction_pair") or {}).get("corrected_span")),
      "edit_signal: correction_pair tagged with pair_kind (live self-describing)")

# gating: KEEP_CORRECTIONS off -> distance kept, no verbatim pair stored
dl.KEEP_CORRECTIONS = False
cp_off = dl.edit_signal("deploy to postgress now", "deploy to PostgreSQL now")
dl.KEEP_CORRECTIONS = True
check("correction_pair" not in cp_off and cp_off["edit_distance"] > 0,
      "KEEP_CORRECTIONS off -> distance kept, no verbatim correction_pair")
# send-and-clear guard: empty FINAL field -> unobservable, NOT a giant fake edit
cleared = dl.edit_signal("a whole dictated sentence", "")
check(cleared["edit_capture"] == "unavailable" and cleared.get("reason") == "field_empty"
      and "edit_distance" not in cleared,
      "empty final field (send-and-clear) -> unavailable, no fabricated edit")

# --- 5. observer: capture_method + activity timeline merged into user.refix ---
_orig = dl._ax_focused_value
dl._ax_focused_value = lambda: None            # simulate unreadable focused field (AX-blind app)
captured = []
dl._EditObserver(window_s=0)._run("c1", "git push", "Code", time.time(), captured.append)
dl._ax_focused_value = _orig
check(len(captured) == 1 and captured[0]["edit_capture"] == "unavailable"
      and captured[0]["capture_method"] == "unavailable" and captured[0]["commit_app"] == "Code",
      "AX unreadable + no monitor -> capture_method=unavailable (no crash)")

# observer that CAN read AX -> ax edit signal + capture_method=ax
dl._ax_focused_value = lambda: "git pull origin"
cap2 = []
dl._EditObserver(window_s=0)._run("c2", "git push origin", "TextEdit", time.time(), cap2.append)
dl._ax_focused_value = _orig
check(cap2 and cap2[0]["edit_capture"] == "ok" and cap2[0]["edit_distance"] == 2
      and cap2[0]["capture_method"] == "ax",
      "AX readable -> ok edit signal, capture_method=ax")

# observer with a monitor reporting backspaces but AX blind -> capture_method=keystroke + timeline
class _FakeMon:
    def window(self, t0, t1, commit_app=None):
        return {"app_switches": [{"t_rel": 3.2, "app": "Terminal"}], "final_app": "Terminal",
                "stayed_in_commit_app": False, "switched_away_s": 3.2,
                "keystroke_summary": {"backspaces": 4, "deletes": 0, "nav_keys": 0, "other_keys": 0}}
dl._ax_focused_value = lambda: None
cap3 = []
dl._EditObserver(window_s=0, monitor=_FakeMon())._run("c3", "hello", "Code", time.time(), cap3.append)
dl._ax_focused_value = _orig
check(cap3 and cap3[0]["capture_method"] == "keystroke" and cap3[0]["switched_away_s"] == 3.2
      and cap3[0]["keystroke_summary"]["backspaces"] == 4 and cap3[0]["final_app"] == "Terminal",
      "AX-blind + keystrokes -> capture_method=keystroke, activity timeline merged")

# --- 4. analyzer summary ---
spec = importlib.util.spec_from_file_location(
    "auc", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "analyze_user_corrections.py"))
auc = importlib.util.module_from_spec(spec); spec.loader.exec_module(auc)

events = [
    {"type": "commit", "commit_id": "a", "raw": "git push", "fixed": "git push", "n_words": 2,
     "committed_len": 8, "app": "Code", "repo_root": "/x/proj", "flags": {"fuzzy_symbols": True, "repo_vocab": True}},
    {"type": "user.refix", "commit_id": "a", "edit_capture": "ok", "accepted_unchanged": True, "edit_distance": 0},
    {"type": "commit", "commit_id": "b", "raw": "get comet", "fixed": "git commit", "n_words": 2,
     "committed_len": 10, "app": "Code", "repo_root": "/x/proj", "flags": {"fuzzy_symbols": False, "repo_vocab": True}},
    {"type": "user.refix", "commit_id": "b", "edit_capture": "ok", "accepted_unchanged": False, "edit_distance": 3,
     "final_span": "git commit amend"},
    {"type": "commit", "commit_id": "c", "raw": "hello there", "fixed": "hello there", "n_words": 2,
     "committed_len": 11, "app": "Notes", "repo_root": None, "flags": {"fuzzy_symbols": True, "repo_vocab": False}},
    {"type": "user.refix", "commit_id": "c", "edit_capture": "unavailable"},
]
s = auc.summarize(events)
check(s["total_dictations"] == 3, "analyzer: total dictations 3")
cap = s["capture"]
check(cap["total_commits"] == 3 and cap["observable"] == 2 and cap["unobservable"] == 1,
      "analyzer: exhaustive breakdown observable+unobservable==total")
check(cap["unavailable_ax"] == 1 and cap["no_signal"] == 0, "analyzer: unobservable splits ax-unavailable/no-signal")
check(cap["coverage_pct"] == 66.7, "analyzer: capture coverage 66.7%")
check(s["rates_computed_on"] == "clean observable subset (divergence excluded)" and s["observable_n"] == 2,
      "analyzer: rates explicitly flagged clean-observable-only")
check(cap["rate_eligible"] == 2 and cap["diverged"] == 0,
      "analyzer: both observable commits are rate-eligible (no divergence)")

# --- divergence guard: an AX edit touching >60% of the commit is field-accumulation, NOT a fix,
# and must be EXCLUDED from the rate (else it inflates the User Correction Rate ~20x). ---
div_events = [
    {"type": "commit", "commit_id": "k", "raw": "short note", "fixed": "short note", "n_words": 2,
     "committed_len": 10, "app": "ChatGPT"},
    {"type": "user.refix", "commit_id": "k", "edit_capture": "ok", "capture_method": "ax",
     "accepted_unchanged": False, "edit_distance": 90, "normalized": 0.95},   # whole field changed = divergence
]
sd = auc.summarize(div_events)
check(sd["capture"]["observable"] == 1 and sd["capture"]["rate_eligible"] == 0
      and sd["capture"]["diverged"] == 1, "analyzer: high-normalized commit classed as diverged, not rate-eligible")
check(sd["user_correction_rate_pct"] is None, "analyzer: divergence-only -> no correction rate (not a fake 95%)")
check(sd["post_commit"].get("diverged_field") == 1, "analyzer: divergence shows in behavior, not as a fix")

# --- editor/terminal keystroke 'edit' is AMBIGUOUS (coding confound), excluded from FIXED ---
amb_events = [
    {"type": "commit", "commit_id": "e1", "raw": "x", "fixed": "x", "surface": "editor", "app": "Code"},
    {"type": "user.refix", "commit_id": "e1", "edit_capture": "unavailable", "capture_method": "keystroke",
     "keystroke_summary": {"backspaces": 4, "other_keys": 10}},
    {"type": "commit", "commit_id": "p1", "raw": "y", "fixed": "y", "surface": "rich-text", "app": "Notes"},
    {"type": "user.refix", "commit_id": "p1", "edit_capture": "unavailable", "capture_method": "keystroke",
     "keystroke_summary": {"backspaces": 2}},
]
sa = auc.summarize(amb_events)
check(sa["post_commit"].get("edited_keystroke_ambiguous") == 1
      and sa["post_commit"].get("edited_keystroke") == 1,
      "analyzer: editor backspace -> ambiguous; prose backspace -> counted edit")
check(sa["fixed_total"] == 1, "analyzer: ambiguous editor edit NOT counted in FIXED total")

# --- VS Code extension: an exact vscode-ext refix WINS over the keystroke proxy for the same commit,
# turning an AX-blind editor commit into rate-eligible (closes the coverage gap). ---
vsx = auc.summarize([
    {"type": "commit", "commit_id": "vx", "raw": "git push", "fixed": "git push", "committed_len": 8,
     "surface": "editor", "app": "Code"},
    {"type": "user.refix", "commit_id": "vx", "edit_capture": "unavailable", "capture_method": "keystroke",
     "keystroke_summary": {"backspaces": 5, "other_keys": 12}},
    {"type": "user.refix", "commit_id": "vx", "edit_capture": "ok", "capture_method": "vscode-ext",
     "accepted_unchanged": True, "edit_distance": 0, "normalized": 0.0},
])
check(vsx["capture"]["rate_eligible"] == 1 and vsx["accepted_unchanged_pct"] == 100.0,
      "analyzer: exact vscode-ext capture wins over keystroke proxy -> editor commit becomes rate-eligible")
check(vsx["post_commit"].get("accepted_ax") == 1,
      "analyzer: vscode-ext counted as an exact capture in post-commit behavior")
# a commit with NO refix event at all must count as unobservable (no_signal), not vanish
s2 = auc.summarize(events + [{"type": "commit", "commit_id": "d", "raw": "x", "fixed": "x", "n_words": 1, "committed_len": 1}])
check(s2["capture"]["total_commits"] == 4 and s2["capture"]["observable"] == 2
      and s2["capture"]["unobservable"] == 2 and s2["capture"]["no_signal"] == 1,
      "analyzer: commit with no refix counts as unobservable/no-signal (total preserved)")
check(s["accepted_unchanged_pct"] == 50.0, "analyzer: accepted unchanged 50% (1 of 2 ok)")
check(s["avg_edit_distance"] == 1.5, "analyzer: avg edit distance 1.5")
check(any(m["raw"] == "get" and m["fixed"] == "git" for m in s["top_mishears"]), "analyzer: mishear get->git mined")
check(s["high_edit_examples"] and s["high_edit_examples"][0]["edit_distance"] == 3, "analyzer: high-edit example surfaced")

# analyzer surfaces repeated USER correction pairs (committed -> corrected) = vocab/alias candidates
s_cp = auc.summarize([
    {"type": "commit", "commit_id": "p", "raw": "x", "fixed": "x"},
    {"type": "user.refix", "commit_id": "p", "edit_capture": "ok", "edit_distance": 4,
     "correction_pair": {"committed_span": "postgress", "corrected_span": "PostgreSQL"}},
    {"type": "commit", "commit_id": "q", "raw": "y", "fixed": "y"},
    {"type": "user.refix", "commit_id": "q", "edit_capture": "ok", "edit_distance": 4,
     "correction_pair": {"committed_span": "postgress", "corrected_span": "PostgreSQL"}},
])
check(s_cp["top_correction_pairs"][0] == {"committed": "postgress", "corrected": "PostgreSQL", "n": 2},
      "analyzer: repeated correction pairs surfaced (committed->corrected, counted)")

# Part B: insertion-corruption classifier - dum scrambling its own output (overlay reconcile bug)
# is NOT a user correction. Signature = near-identical chars + broken word segmentation.
check(auc.is_insertion_corruption("tool. Uh however", "too.l Uhhhoweve")
      and auc.is_insertion_corruption("of brew", "ofbBre"),
      "is_insertion_corruption: flags char-scrambles with reordered letters + mangled spacing")
# known miss (documented): a pure bad-space scramble keeps its letter sequence, so it is NOT flagged
# (the cost of never eating a legit space/punct correction); Part A will trim these.
check(not auc.is_insertion_corruption("weird.", "weird ."),
      "is_insertion_corruption: pure bad-space scramble is a known miss (letter seq preserved)")
check(not auc.is_insertion_corruption("joint", "join")
      and not auc.is_insertion_corruption("Manually.", "manually.")
      and not auc.is_insertion_corruption("thing", "VSCODE")
      and not auc.is_insertion_corruption("recieve", "receive"),
      "is_insertion_corruption: leaves genuine corrections (mishear/casing/typo-fix) alone")
# CRITICAL: must NOT eat the most valuable vocab candidates - compound/hyphenation/casing fixes keep
# the same letter sequence (only spaces/punct/case move), so they are never corruption.
check(not auc.is_insertion_corruption("git hub", "GitHub")
      and not auc.is_insertion_corruption("web socket", "WebSocket")
      and not auc.is_insertion_corruption("sherpa onnx", "sherpa-onnx")
      and not auc.is_insertion_corruption("whisper cpp", "whisper.cpp"),
      "is_insertion_corruption: NEVER flags compound/hyphenation vocab corrections (letter seq preserved)")

# corruption pairs are split OUT of vocab candidates and surfaced separately
s_cor = auc.summarize([
    {"type": "commit", "commit_id": "g", "raw": "x", "fixed": "x"},
    {"type": "user.refix", "commit_id": "g", "edit_capture": "ok", "edit_distance": 3,
     "correction_pair": {"committed_span": "git hub", "corrected_span": "GitHub"}},        # real
    {"type": "commit", "commit_id": "h", "raw": "y", "fixed": "y"},
    {"type": "user.refix", "commit_id": "h", "edit_capture": "ok", "edit_distance": 4,
     "correction_pair": {"committed_span": "tool. Uh however", "corrected_span": "too.l Uhhhoweve"}},  # corruption
])
check([p["committed"] for p in s_cor["top_correction_pairs"]] == ["git hub"]
      and [p["committed"] for p in s_cor["suspected_corruption"]] == ["tool. Uh however"]
      and s_cor["corruption_pair_count"] == 1,
      "analyzer: corruption quarantined out of vocab candidates, surfaced separately")

# Step 8: unified analyzer derives STAGE USAGE from embedded stages_fired + PER-APP capture coverage
s8 = auc.summarize([
    {"type": "commit", "commit_id": "m", "raw": "x", "fixed": "x", "app": "Code",
     "stages_fired": [{"stage": "phonetic", "before": "a", "after": "b", "ms": 0.2},
                      {"stage": "sentcap", "before": "b", "after": "B", "ms": 0.1}]},
    {"type": "user.refix", "commit_id": "m", "capture_method": "keystroke", "edit_capture": "unavailable",
     "keystroke_summary": {"backspaces": 0, "other_keys": 1}},
    {"type": "commit", "commit_id": "n", "raw": "y", "fixed": "y", "app": "TextEdit",
     "stages_fired": [{"stage": "phonetic", "before": "c", "after": "d", "ms": 0.4}]},
    {"type": "user.refix", "commit_id": "n", "capture_method": "ax", "edit_capture": "ok",
     "accepted_unchanged": True, "edit_distance": 0},
])
check(s8["stage_usage"]["phonetic"]["fired"] == 2 and s8["stage_usage"]["sentcap"]["fired"] == 1,
      "analyzer: STAGE usage counted from embedded stages_fired")
check(s8["by_app_capture"]["Code"]["keystroke"] == 1 and s8["by_app_capture"]["Code"]["ax"] == 0
      and s8["by_app_capture"]["TextEdit"]["ax"] == 1,
      "analyzer: per-app capture split (Code=keystroke, TextEdit=ax) - 'where am I blind'")

# --- 6. flag-as-problem hotkey: writes user.verdict for the last commit; analyzer surfaces it ---
with tempfile.TemporaryDirectory() as _gd:
    os.environ["DUM_DOGFOOD_LOG"] = "1"
    _ax_real3 = dl._ax_focused_value
    dl._ax_focused_value = lambda: None
    glog = dl.DogfoodLogger(path=os.path.join(_gd, "dogfood", "sessions", "s.jsonl"))
    check(glog.flag_problem() is None, "flag_problem() before any commit -> None (nothing to flag)")
    glog.log_commit("the bug is here", "the bug is here", app="Code", mode="overlay")
    fid = glog.flag_problem()
    glog.close()
    dl._ax_focused_value = _ax_real3
    rows_g = [json.loads(l) for l in open(glog.path) if l.strip()]
    verdicts = [r for r in rows_g if r.get("type") == "user.verdict"]
    check(fid is not None and len(verdicts) == 1 and verdicts[0]["verdict"] == "problem"
          and verdicts[0]["commit_id"] == fid,
          "flag_problem() writes user.verdict=problem for the last commit_id")
    s_fl = auc.summarize(rows_g)
    check(len(s_fl["flagged_problems"]) == 1 and s_fl["flagged_problems"][0]["raw"] == "the bug is here",
          "analyzer: flagged problem surfaced with its raw text for manual review")
os.environ["DUM_DOGFOOD_LOG"] = "0"

# --- 7. master switch: DUM_DOGFOOD_FULL drives the per-feature defaults; individual flags override ---
for _k in ("DUM_DOGFOOD_LOG", "DUM_KEEP_AUDIO", "DUM_KEYSTROKE_PROXY"):
    os.environ.pop(_k, None)
_full_save = dl.DOGFOOD_FULL
dl.DOGFOOD_FULL = True
lg_full = dl.DogfoodLogger(path="/tmp/dum_full_on.jsonl")
check(lg_full.enabled and lg_full.keep_audio and lg_full.keystroke_proxy,
      "DUM_DOGFOOD_FULL on -> log + audio + keystroke proxy all default ON")
dl.DOGFOOD_FULL = False
lg_off = dl.DogfoodLogger(path="/tmp/dum_full_off.jsonl")
check(not lg_off.enabled and not lg_off.keep_audio and not lg_off.keystroke_proxy,
      "DUM_DOGFOOD_FULL off (shipped) -> log + audio + keystroke proxy all default OFF")
os.environ["DUM_KEEP_AUDIO"] = "1"                      # individual override wins over the profile
lg_ov = dl.DogfoodLogger(path="/tmp/dum_full_ov.jsonl")
check(lg_ov.keep_audio and not lg_ov.enabled,
      "individual flag (DUM_KEEP_AUDIO=1) overrides the OFF profile; others stay off")
os.environ.pop("DUM_KEEP_AUDIO", None)
dl.DOGFOOD_FULL = _full_save

print(f"\nALL {passed} CHECKS PASSED")
