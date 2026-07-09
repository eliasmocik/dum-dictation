#!/usr/bin/env python3
"""
analyze_user_corrections.py - report on dogfood logs (dogfood_log.py) to answer "is the tool
improving?". Reads commit + user.refix JSONL events, joins by commit_id, summarizes.

Usage:
    python scripts/analyze_user_corrections.py [dogfood/sessions/*.jsonl]
    (default glob: dogfood/sessions/*.jsonl)

Core metric - User Correction Rate = manual char edit distance after commit, normalized by committed
length, over commits where post-commit edit capture worked. Edit capture is best-effort (AX), so the
report also shows COVERAGE; commit-level stats (volume, mishears, by app/repo/flag) are always available.
"""
import sys, os, json, glob, difflib, collections, re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dogfood_log as dl          # single source of truth for the committed->corrected classifier

_WS = re.compile(r"\s+")

# committed->corrected diff kinds that are NOT a user correction (dum's own insertion scramble or a
# neighbour-commit bleed) - excluded from the user-correction rate and from vocab candidates, surfaced
# separately as an OVERLAY-CORRUPTION (Part C) bug signal. See dogfood_log.classify_correction.
_CORRUPTION_KINDS = {"scramble", "bleed"}


def pair_kind(refix):
    """The classify_correction verdict for a refix's correction_pair, recomputed from the stored span
    so historical logs (written before pair_kind existed) classify identically. 'clean' when no pair."""
    cp = (refix or {}).get("correction_pair") or {}
    if not cp.get("committed_span"):
        return "clean"
    return dl.classify_correction(cp.get("committed_span"), cp.get("corrected_span"))

# AX edit-distance compares ONE commit to the WHOLE field after the observation window. A post-commit
# change touching more than this fraction of the committed text is treated as FIELD DIVERGENCE (the
# user kept writing - next chat message, more paragraphs, code - not a correction of THIS commit) and
# excluded from the correction-rate metrics. Bimodal on dogfood data: real corrections cluster at
# normalized<=0.6, divergence at >0.6 with a clean valley between. Including divergence inflated the
# reported User Correction Rate ~20x (46% vs the true 2%).
DIVERGENCE_NORMALIZED = 0.6
# surfaces where the post-commit KEYSTROKE proxy is confounded: the user types/backspaces as part of
# normal coding, so a backspace there is NOT reliably a correction of the dictation (AX is also blind
# in these Electron/terminal apps). Keystroke "edits" here are reported as ambiguous, not FIXED.
# Uses the REFINED surface (see refined_surface): claude-code is excluded - it's chat prose AND we
# have the exact transcript-join signal there, so its keystroke proxy is never the fallback of record.
_CODING_SURFACES = {"editor", "vscode-terminal", "shell"}


def _norm(w):
    return re.sub(r"[^\w]", "", w).lower()


def is_insertion_corruption(committed_span, corrected_span):
    """True when a committed->corrected pair is NOT a user correction but dum's own insertion
    scramble or a neighbour-commit bleed. Thin wrapper over dogfood_log.classify_correction (the single
    source of truth, also used live and tested) - kept as a named predicate for the report's vocab/
    high-edit filters. See classify_correction for the full taxonomy (clean/trivial/scramble/bleed)."""
    return dl.classify_correction(committed_span, corrected_span) in _CORRUPTION_KINDS


def load(paths):
    events = []
    for p in paths:
        try:
            for line in open(p):
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return events


def mishear_pairs(raw, fixed):
    """Word-level raw->fixed swaps (the tool's recognizer mishears it corrected)."""
    rw, fw = raw.split(), fixed.split()
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=rw, b=fw).get_opcodes():
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for a, b in zip(rw[i1:i2], fw[j1:j2]):
                if _norm(a) != _norm(b):
                    pairs.append((a.strip(".,!?;:"), b.strip(".,!?;:")))
    return pairs


# capture-quality rank: a commit can get >1 user.refix (dum's keystroke proxy AND an exact reader -
# the VS Code extension for editor docs, or the Claude transcript join for the Claude Code prompt).
# Keep the highest-quality one. claude-transcript/vscode-ext/ax are exact; keystroke is a proxy;
# unavailable is no signal.
_CAP_RANK = {"claude-transcript": 3, "vscode-ext": 3, "ax": 2, "keystroke": 1, "unavailable": 0}
# exact (document/AX/transcript) capture methods - a real edit_distance, not the keystroke proxy.
_EXACT_CAP = {"claude-transcript", "vscode-ext", "ax"}


def _cap_rank(ev):
    cm = ev.get("capture_method")
    if cm in _CAP_RANK:
        return _CAP_RANK[cm]
    return 2 if ev.get("edit_capture") == "ok" else 0


def refined_surface(commit, refix):
    """The precise insertion surface, resolving the coarse live 'vscode' bucket with post-hoc evidence
    from the winning refix: a Claude transcript join => 'claude-code'; the VS Code extension's
    document-model read => 'editor' (a real editor doc); otherwise a 'vscode' commit is the integrated
    terminal/other => 'vscode-terminal'. Non-vscode surfaces (shell/browser/rich-text/editor/unknown)
    pass through unchanged."""
    cm = (refix or {}).get("capture_method")
    if cm == "claude-transcript" or (refix or {}).get("surface_refined") == "claude-code":
        return "claude-code"
    surf = commit.get("surface")
    if cm == "vscode-ext":
        return "editor"
    if surf == "vscode":
        return "vscode-terminal"
    return surf or "unknown"


def summarize(events):
    commits = [e for e in events if e.get("type") == "commit"]
    refix = {}
    for e in events:
        if e.get("type") != "user.refix" or "commit_id" not in e:
            continue
        cur = refix.get(e["commit_id"])
        if cur is None or _cap_rank(e) >= _cap_rank(cur):
            refix[e["commit_id"]] = e          # prefer exact (vscode-ext/ax) over the keystroke proxy

    total = len(commits)
    words = sum(e.get("n_words", len(e.get("fixed", "").split())) for e in commits)
    ok = [(c, refix[c["commit_id"]]) for c in commits
          if c.get("commit_id") in refix and refix[c["commit_id"]].get("edit_capture") == "ok"]
    # Split AX-observable commits into CLEAN (the post-commit change is small enough to be a real
    # correction of this commit) vs DIVERGED (field changed wholesale - accumulation, not a fix).
    # Rates are computed on CLEAN only; diverged is reported separately so it can't inflate them.
    clean = [(c, r) for c, r in ok if r.get("normalized", 0) <= DIVERGENCE_NORMALIZED]
    diverged = [(c, r) for c, r in ok if r.get("normalized", 0) > DIVERGENCE_NORMALIZED]

    # Within the rate-eligible (clean) set, separate a GENUINE user correction from dum's OWN
    # insertion scramble / neighbour-commit bleed. The capture layer records both faithfully, but the
    # latter is NOT a user edit - counting its edit_distance in the correction rate was exactly the bug
    # that made the telemetry untrustworthy (it reported the overlay corrupting its own output as the
    # user correcting the tool). We zero it out of every rate and surface it on its own as the
    # OVERLAY-CORRUPTION (Part C) signal, split by capture_method so a real submitted-text corruption
    # (claude-transcript) is told apart from an AX-read-mid-edit artifact (ax/keystroke).
    def _user_dist(r):
        return 0 if pair_kind(r) in _CORRUPTION_KINDS else r.get("edit_distance", 0)
    corruption = [(c, r) for c, r in clean if pair_kind(r) in _CORRUPTION_KINDS]
    # EXHAUSTIVE breakdown: observable + unobservable == total, always. Unobservable splits into
    # an explicit AX "unavailable" event and commits with no edit signal at all (no refix written).
    observable = len(ok)
    unobservable = total - observable
    unavailable_ax = sum(1 for c in commits
                         if refix.get(c.get("commit_id"), {}).get("edit_capture") == "unavailable")
    no_signal = unobservable - unavailable_ax

    accepted = sum(1 for _c, r in clean if r.get("accepted_unchanged"))
    tot_dist = sum(_user_dist(r) for _c, r in clean)               # USER edits only (corruption zeroed)
    tot_len = sum(_c.get("committed_len", len(_c.get("fixed", ""))) for _c, r in clean) or 1
    obs_words = sum(_c.get("n_words", len(_c.get("fixed", "").split())) for _c, r in clean)  # clean only

    def grouped_rate(keyfn):
        g = collections.defaultdict(lambda: [0, 0, 0])   # [edits_sum, len_sum, n]
        for c, r in clean:
            k = keyfn(c)
            g[k][0] += _user_dist(r)
            g[k][1] += c.get("committed_len", len(c.get("fixed", "")))
            g[k][2] += 1
        return {k: {"corr_rate_pct": round(100 * v[0] / max(1, v[1]), 2), "n": v[2]}
                for k, v in sorted(g.items(), key=lambda kv: -kv[1][2])}

    # BY REFINED SURFACE - the headline slice for "where does my dictation actually go, and how well
    # does it work there?". Uses refined_surface (resolves the coarse 'vscode' bucket into
    # editor / vscode-terminal / claude-code via the exact-capture evidence). Per surface: total
    # commits, how many got an EXACT signal (so coverage is honest), corrected count + correction rate
    # on the clean subset.
    clean_ids = {id(r) for _c, r in clean}
    surf = collections.defaultdict(lambda: collections.Counter())
    surf_rate = collections.defaultdict(lambda: [0, 0])      # [edit_chars, committed_chars] on clean
    for c in commits:
        r = refix.get(c.get("commit_id"))
        s = refined_surface(c, r)
        surf[s]["n"] += 1
        if r and r.get("capture_method") in _EXACT_CAP:
            surf[s]["exact"] += 1
        if r and id(r) in clean_ids:
            surf[s]["rate_eligible"] += 1
            if pair_kind(r) in _CORRUPTION_KINDS:
                surf[s]["corrupted"] += 1                # overlay scramble/bleed, NOT a user correction
            elif r.get("edit_distance", 0) > 0:
                surf[s]["corrected"] += 1                # genuine user correction
            surf_rate[s][0] += _user_dist(r)
            surf_rate[s][1] += c.get("committed_len", len(c.get("fixed", "")))
    by_surface = {s: {"n": v["n"], "exact": v["exact"], "rate_eligible": v["rate_eligible"],
                      "corrected": v["corrected"], "corrupted": v["corrupted"],
                      "exact_pct": round(100 * v["exact"] / max(1, v["n"]), 1),
                      "corr_rate_pct": round(100 * surf_rate[s][0] / max(1, surf_rate[s][1]), 2)}
                  for s, v in sorted(surf.items(), key=lambda kv: -kv[1]["n"])}

    mishears = collections.Counter()
    for c in commits:
        for a, b in mishear_pairs(c.get("raw", ""), c.get("fixed", "")):
            mishears[(a, b)] += 1

    # high-edit examples for manual review - exclude suspected insertion-corruption (it's a tool bug,
    # not a recogniser/user-correction signal worth eyeballing here; surfaced in its own section).
    high_edit = sorted(({"fixed": c.get("fixed", ""), "correction": r.get("correction_pair") or {},
                         "edit_distance": r.get("edit_distance", 0)}
                        for c, r in clean if r.get("edit_distance", 0) > 0
                        and not is_insertion_corruption((r.get("correction_pair") or {}).get("committed_span"),
                                                        (r.get("correction_pair") or {}).get("corrected_span"))),
                       key=lambda x: -x["edit_distance"])[:8]

    # repeated user corrections committed->corrected - the V2/V3 learning candidates a human reviews
    # (General vs Personal, see CONTRIBUTING.md) before hand-adding the General ones to packs. Only
    # 'clean' pairs (genuine word-level fixes) are candidates; 'trivial' punctuation/case diffs are
    # dropped (not worth learning), and 'scramble'/'bleed' OVERLAY CORRUPTION is split out as a bug
    # signal - broken down by capture_method so a real submitted-text scramble (claude-transcript,
    # i.e. Part C actually corrupting the prompt) is told apart from an AX-read-mid-edit artifact.
    corr_pairs = collections.Counter()
    corruption_pairs = collections.Counter()
    scramble_by_method = collections.Counter()          # capture_method -> # of true SCRAMBLE pairs
    corruption_kind = collections.Counter()             # 'scramble' vs 'bleed'
    for r in refix.values():
        cp = r.get("correction_pair")
        if not (cp and cp.get("committed_span")):
            continue
        key = (cp["committed_span"], cp.get("corrected_span", ""))
        kind = dl.classify_correction(key[0], key[1])
        if kind in _CORRUPTION_KINDS:
            corruption_pairs[key] += 1
            corruption_kind[kind] += 1
            if kind == "scramble":                      # only scrambles are the Part C insertion bug;
                scramble_by_method[r.get("capture_method") or "unknown"] += 1   # bleed = accumulation
        elif kind == "clean":
            corr_pairs[key] += 1                        # 'trivial' deliberately excluded - punct/case noise

    # commits the user FLAGGED as a problem (double-tap left ⌥) - to revisit manually. Audio +
    # correction context are saved, so each is fully reviewable offline.
    commit_by_id = {c["commit_id"]: c for c in commits if "commit_id" in c}
    flagged_ids = {e["commit_id"] for e in events
                   if e.get("type") == "user.verdict" and e.get("verdict") == "problem" and "commit_id" in e}
    flagged = [{"commit_id": cid, "app": commit_by_id[cid].get("app"),
                "raw": commit_by_id[cid].get("raw", ""), "fixed": commit_by_id[cid].get("fixed", ""),
                "audio": (commit_by_id[cid].get("audio_ref") or {}).get("path")}
               for cid in flagged_ids if cid in commit_by_id]

    # STAGE USAGE - from the embedded stages_fired (folds in the old analyze_corrections.py stream).
    # Which correction stage changed the text, how often, and how long it took. "Which stages help."
    stage_fires = collections.Counter()
    stage_ms = collections.defaultdict(float)
    for c in commits:
        for sf in (c.get("stages_fired") or []):
            st = sf.get("stage")
            stage_fires[st] += 1
            if isinstance(sf.get("ms"), (int, float)):
                stage_ms[st] += sf["ms"]
    stage_usage = {st: {"fired": n, "avg_ms": round(stage_ms[st] / n, 3) if n else 0.0}
                   for st, n in stage_fires.most_common()}

    # PER-APP CAPTURE COVERAGE - where do we actually SEE post-commit behaviour vs go blind?
    app_cap = collections.defaultdict(collections.Counter)
    for c in commits:
        app = c.get("app") or "(unknown)"
        r = refix.get(c.get("commit_id")) or {}
        app_cap[app]["n"] += 1
        if r.get("edit_capture") == "ok":
            app_cap[app]["ax"] += 1
        elif r.get("capture_method") == "keystroke":
            app_cap[app]["keystroke"] += 1
        else:
            app_cap[app]["unavailable"] += 1
    by_app_capture = {app: {"n": v["n"], "ax": v["ax"], "keystroke": v["keystroke"],
                            "unavailable": v["unavailable"],
                            "any_signal_pct": round(100 * (v["ax"] + v["keystroke"]) / max(1, v["n"]), 1)}
                      for app, v in sorted(app_cap.items(), key=lambda kv: -kv[1]["n"])}

    # PER-APP OVERLAY SCRAMBLE - the overlay-everywhere experiment's scorecard (2026-06-22 flip). For
    # each app, restricted to its OVERLAY commits (paste can't scramble): how many we could actually
    # OBSERVE (exact read-back - ax / vscode-ext / claude-transcript), how many of those were a SCRAMBLE
    # (dum's own insertion char-shuffle, kind=='scramble'; 'bleed' is accumulation, not the bug), the
    # rate on the observable subset, and how many were BLIND (no read-back - e.g. Electron apps where AX
    # can't see). `blind` is the honesty column: a high-blind app's 0 scrambles means UNMEASURED, not
    # clean. Scrambles seen via AX can include an AX-read-mid-edit artifact, so the rate is an UPPER bound.
    app_ov = collections.defaultdict(collections.Counter)
    for c in commits:
        if c.get("mode") != "overlay":
            continue                                   # paste/log commits can't scramble - overlay only
        app = c.get("app") or "(unknown)"
        r = refix.get(c.get("commit_id")) or {}
        app_ov[app]["overlay_n"] += 1
        if r.get("capture_method") in _EXACT_CAP:
            app_ov[app]["observable"] += 1             # we could read the field back -> a scramble is visible
            if pair_kind(r) == "scramble":
                app_ov[app]["scrambles"] += 1
        else:
            app_ov[app]["blind"] += 1                  # no read-back -> a scramble here would be invisible
    by_app_overlay = {app: {"overlay_n": v["overlay_n"], "observable": v["observable"],
                            "scrambles": v["scrambles"], "blind": v["blind"],
                            "scramble_rate_pct": (round(100 * v["scrambles"] / v["observable"], 1)
                                                  if v["observable"] else None)}
                      for app, v in sorted(app_ov.items(), key=lambda kv: -kv[1]["overlay_n"])}

    flag_label = lambda c: ("fuzzy_symbols=" + str(bool(c.get("flags", {}).get("fuzzy_symbols")))
                            + ", repo_vocab=" + str(bool(c.get("flags", {}).get("repo_vocab"))))
    repo_label = lambda c: (os.path.basename(c["repo_root"]) if c.get("repo_root") else "(no repo)")

    # POST-COMMIT BEHAVIOUR (Step 4): the question - did the user FIX the output, or move on? Uses the
    # app-switch + keystroke signals, which work in EVERY app (not just AX-readable ones), so this has
    # far higher coverage than the AX correction rate. Priority cascade, one bucket per commit.
    behavior = collections.Counter()
    for c in commits:
        r = refix.get(c.get("commit_id"))
        if not r:
            behavior["no_refix"] += 1
            continue
        ks = r.get("keystroke_summary") or {}
        backspaces = ks.get("backspaces", 0) + ks.get("deletes", 0)
        cm = r.get("capture_method")
        exact = cm in _EXACT_CAP                          # exact document/AX/transcript capture (not the proxy)
        if exact and pair_kind(r) in _CORRUPTION_KINDS:
            behavior["overlay_corruption"] += 1           # the typer/AX scrambled it - NOT a user fix
        elif exact and r.get("edit_distance", 0) > 0 and r.get("normalized", 0) <= DIVERGENCE_NORMALIZED:
            behavior["edited_ax_confirmed"] += 1          # FIXED (exact capture, real edit, not divergence)
        elif exact and r.get("accepted_unchanged"):
            behavior["accepted_ax"] += 1                  # kept as-is
        elif exact and r.get("edit_distance", 0) > 0:
            behavior["diverged_field"] += 1               # exact capture saw a huge change = accumulation, not a fix
        elif backspaces > 0 and refined_surface(c, r) in _CODING_SURFACES:
            behavior["edited_keystroke_ambiguous"] += 1   # backspaced in an editor/terminal - coding vs fixing, unknown
        elif backspaces > 0:
            behavior["edited_keystroke"] += 1             # FIXED (backspaced in a prose app - ~ a correction)
        elif r.get("switched_away_s") is not None:
            behavior["moved_on_switched"] += 1            # left to another task, no edit seen
        elif ks.get("other_keys", 0) > 0:
            behavior["continued_typing"] += 1             # stayed, typed forward (no fix)
        else:
            behavior["idle_or_unknown"] += 1
    # FIXED = only signals we trust: AX-confirmed real edits + prose-app backspaces. Editor/terminal
    # keystroke edits are AMBIGUOUS (coding confound) and excluded from the headline fix count.
    fixed_n = behavior["edited_ax_confirmed"] + behavior["edited_keystroke"]

    # OVERLAY CORRUPTION - rate-eligible commits whose post-commit diff was a scramble/bleed, NOT a
    # user edit. Split by capture_method: claude-transcript/vscode-ext = the corruption was in the
    # text actually SUBMITTED (a real Part C bug); ax/keystroke = likely an AX read taken mid-edit
    # (a capture artifact). The count the user-correction rate USED to wrongly absorb.
    corruption_chars = sum(r.get("edit_distance", 0) for _c, r in corruption)
    corruption_by_surface = collections.Counter(refined_surface(c, r) for c, r in corruption)

    return {
        "total_dictations": total,
        "total_words": words,
        "capture": {
            "total_commits": total,
            "observable": observable,                  # AX could read the field at all
            "rate_eligible": len(clean),               # observable AND not field-divergence - rates use ONLY these
            "user_corrections": len(clean) - accepted - len(corruption),  # genuine fixes in the clean set
            "overlay_corruption": len(corruption),     # scramble/bleed in the clean set - NOT user edits
            "diverged": len(diverged),                 # observable but field changed wholesale - excluded from rates
            "unobservable": unobservable,              # = unavailable_ax + no_signal == total - observable
            "unavailable_ax": unavailable_ax,          # AX explicitly couldn't read the field
            "no_signal": no_signal,                    # no edit signal written at all
            "coverage_pct": round(100 * observable / max(1, total), 1),
            "rate_eligible_pct": round(100 * len(clean) / max(1, total), 1),
        },
        "rates_computed_on": "clean observable subset (divergence excluded)",
        "observable_n": len(clean),
        "accepted_unchanged_pct": round(100 * accepted / max(1, len(clean)), 1) if clean else None,
        "avg_edit_distance": round(tot_dist / max(1, len(clean)), 2) if clean else None,
        "user_correction_rate_pct": round(100 * tot_dist / tot_len, 2) if clean else None,
        "corrections_per_100_words": round(100 * tot_dist / max(1, obs_words), 2) if clean else None,
        "by_surface": by_surface,
        "by_app": grouped_rate(lambda c: c.get("app") or "(unknown)"),
        "by_repo": grouped_rate(repo_label),
        "by_flags": grouped_rate(flag_label),
        "fuzzy_on_vs_off": {
            "on": grouped_rate(flag_label).get("fuzzy_symbols=True, repo_vocab=True")
                  or grouped_rate(lambda c: c.get("flags", {}).get("fuzzy_symbols")).get(True),
            "off": grouped_rate(lambda c: c.get("flags", {}).get("fuzzy_symbols")).get(False),
        },
        "top_mishears": [{"raw": a, "fixed": b, "n": n} for (a, b), n in mishears.most_common(15)],
        "top_correction_pairs": [{"committed": a, "corrected": b, "n": n}
                                 for (a, b), n in corr_pairs.most_common(15)],
        "suspected_corruption": [{"committed": a, "corrected": b, "n": n}
                                 for (a, b), n in corruption_pairs.most_common(15)],
        "corruption_pair_count": sum(corruption_pairs.values()),
        "scramble_by_method": dict(scramble_by_method),
        "corruption_kind": dict(corruption_kind),
        "corruption_by_surface": dict(corruption_by_surface),
        "corruption_chars": corruption_chars,
        "overlay_corruption_n": len(corruption),
        "high_edit_examples": high_edit,
        "post_commit": dict(behavior),
        "fixed_total": fixed_n,
        "flagged_problems": flagged,
        "stage_usage": stage_usage,
        "by_app_capture": by_app_capture,
        "by_app_overlay": by_app_overlay,
    }


def print_report(s):
    print("=" * 70)
    print("USER CORRECTION REPORT")
    print("=" * 70)
    fp = s.get("flagged_problems", [])
    if fp:
        print(f"\n🚩 FLAGGED PROBLEMS - {len(fp)} dictation(s) you marked to revisit (double-tap ⌥):")
        for f in fp:
            print(f"    [{f['app']}] raw={f['raw'][:70]!r}")
            if f["raw"] != f["fixed"]:
                print(f"             committed={f['fixed'][:70]!r}")
            if f.get("audio"):
                print(f"             audio: {f['audio']}")
        print()
    c = s["capture"]
    print("EDIT-CAPTURE BREAKDOWN (observable + unobservable == total):")
    print(f"  total commits         : {c['total_commits']}  ({s['total_words']} words)")
    print(f"  observable (captured) : {c['observable']}")
    print(f"    - rate-eligible     : {c['rate_eligible']}  (real-correction signal - rates use these)")
    print(f"        · user fixes    : {c.get('user_corrections', '?')}  (genuine corrections, in the rate)")
    print(f"        · overlay-corrupt: {c.get('overlay_corruption', 0)}  (dum scramble/bleed - EXCLUDED from the rate, see below)")
    print(f"    - field-diverged    : {c['diverged']}  (field changed wholesale = accumulation, EXCLUDED from rates)")
    print(f"  unobservable          : {c['unobservable']}  "
          f"(AX-unavailable {c['unavailable_ax']}, no-signal {c['no_signal']})")
    print(f"  CAPTURE COVERAGE      : {c['coverage_pct']}%   (rate-eligible {c['rate_eligible_pct']}%)")
    bac = s.get("by_app_capture", {})
    if bac:
        print("\n  per-app capture (where do we SEE post-commit behaviour vs go blind?):")
        print(f"    {'app':<22} {'n':>4}  {'ax':>4} {'keys':>5} {'blind':>6}   any-signal")
        for app, v in list(bac.items())[:10]:
            print(f"    {app[:22]:<22} {v['n']:>4}  {v['ax']:>4} {v['keystroke']:>5} {v['unavailable']:>6}   {v['any_signal_pct']}%")
    bao = s.get("by_app_overlay", {})
    if bao:
        print("\n  per-app OVERLAY SCRAMBLE - overlay-everywhere experiment scorecard (overlay commits only):")
        print(f"    {'app':<22} {'ovl':>4} {'obs':>4} {'scrm':>5} {'rate':>7}  {'blind':>6}")
        for app, v in list(bao.items())[:14]:
            rate = "-(blind)" if v["scramble_rate_pct"] is None else f"{v['scramble_rate_pct']}%"
            print(f"    {app[:22]:<22} {v['overlay_n']:>4} {v['observable']:>4} {v['scrambles']:>5} {rate:>7}  {v['blind']:>6}")
        print("    ovl=overlay commits · obs=observable (exact read-back) · scrm=scrambles · rate=scrm/obs · blind=unmeasured")
        print("    ⚠ blind≈all (Electron: ChatGPT/Discord/Obsidian) = AX can't see -> 0 scrm means UNMEASURED, not clean;")
        print("      flag those by hand (double-tap ⌥). AX-seen scrambles may include read-mid-edit artifacts -> rate is an upper bound.")
    bs = s.get("by_surface", {})
    if bs:
        print("\n  by SURFACE (where dictation lands; 'vscode' refined -> editor/vscode-terminal/claude-code):")
        print(f"    {'surface':<16} {'n':>5} {'exact':>6} {'exact%':>7}  {'corr':>5} {'crpt':>5}  corr-rate (clean)")
        for name, v in bs.items():
            print(f"    {name:<16} {v['n']:>5} {v['exact']:>6} {v['exact_pct']:>6}%  {v['corrected']:>5} "
                  f"{v.get('corrupted', 0):>5}  {v['corr_rate_pct']}%  (rate-elig {v['rate_eligible']})")
        print("    cols: corr = genuine user corrections · crpt = overlay scramble/bleed (NOT user edits).")
        if "claude-code" in bs:
            print("    note: claude-code rows come from the Claude transcript join (exact, local-only) -"
                  " not terminal reading.")
        if "vscode-terminal" in bs:
            print("    note: vscode-terminal = VS Code integrated terminal, NOT exact-captured yet"
                  " (keystroke proxy only).")
    print(f"\n  ⚑ ALL correction-rate metrics below are computed ONLY on the {c['rate_eligible']} "
          f"rate-eligible commits\n    (observable AND not field-divergence). At {c['rate_eligible_pct']}% "
          f"rate-eligible coverage, read them as representative of that subset, not all dictation.")
    if not c["rate_eligible"]:
        print("\n  ⚠ no rate-eligible commits yet (AX unreadable or only field-divergence) - no correction rate.")
        print("    commit-level stats below (mishears, volume) are still valid.")
    else:
        print(f"\n  [rate-eligible subset, n={c['rate_eligible']}]")
        print(f"  accepted unchanged      : {s['accepted_unchanged_pct']}%")
        print(f"  avg edit distance       : {s['avg_edit_distance']} chars")
        print(f"  USER CORRECTION RATE    : {s['user_correction_rate_pct']}%  "
              f"({s['corrections_per_100_words']} edits/100 words)")
        print("\n  fuzzy_symbols ON vs OFF (user correction rate):")
        print(f"    ON : {s['fuzzy_on_vs_off']['on']}")
        print(f"    OFF: {s['fuzzy_on_vs_off']['off']}")
        for label, key in [("by app", "by_app"), ("by repo", "by_repo"), ("by flags", "by_flags")]:
            print(f"\n  {label}:")
            for k, v in list(s[key].items())[:8]:
                print(f"    {k:<40} {v['corr_rate_pct']}%  (n={v['n']})")
        if s["high_edit_examples"]:
            print("\n  high-edit commits (committed -> your correction):")
            for ex in s["high_edit_examples"]:
                cp = ex.get("correction") or {}
                shown = (f"{cp.get('committed_span','')!r} -> {cp.get('corrected_span','')!r}"
                         if cp else f"{ex['fixed'][:40]!r} (pair not captured)")
                print(f"    d={ex['edit_distance']:<4} {shown}")
    b = s.get("post_commit", {})
    if b:
        tot = sum(b.values()) or 1
        print("\n  POST-COMMIT BEHAVIOR - did you FIX the output, or move on? (all apps, not just AX):")
        order = [("edited (AX-confirmed)", "edited_ax_confirmed"), ("edited (backspaced, prose)", "edited_keystroke"),
                 ("accepted as-is (AX)", "accepted_ax"),
                 ("overlay corruption (NOT a fix)", "overlay_corruption"),
                 ("edited? (editor/terminal - ambiguous)", "edited_keystroke_ambiguous"),
                 ("field diverged (accumulation)", "diverged_field"),
                 ("continued typing (no fix)", "continued_typing"),
                 ("moved on (switched away)", "moved_on_switched"), ("idle / no signal", "idle_or_unknown"),
                 ("no refix event (old logs)", "no_refix")]
        for label, key in order:
            n = b.get(key, 0)
            if n:
                print(f"    {label:<38} {n:>4}  ({100*n/tot:.1f}%)")
        print(f"    {'-> FIXED (trusted signals only)':<38} {s.get('fixed_total', 0):>4}  ({100*s.get('fixed_total',0)/tot:.1f}%)")
        amb = b.get("edited_keystroke_ambiguous", 0)
        if amb:
            print(f"    (+ {amb} ambiguous editor/terminal edits NOT counted as FIXED - coding confound)")
    su = s.get("stage_usage", {})
    if su:
        print("\n  correction STAGE usage (which stage changed the text, from the embedded trace):")
        for st, v in su.items():
            print(f"    {st:<12} fired {v['fired']:>4}x   avg {v['avg_ms']} ms")
    print("\n  top repeated recognizer mishears (raw -> fixed, TOOL-corrected):")
    for m in s["top_mishears"]:
        print(f"    {m['n']:>3}x  {m['raw']!r} -> {m['fixed']!r}")
    cps = s.get("top_correction_pairs", [])
    print("\n  top repeated USER corrections (committed -> corrected) - vocab/alias candidates:")
    print("    (classify General vs Personal before adding to packs - see CONTRIBUTING.md)")
    if not cps:
        print("    (none captured yet - needs AX-readable edits with DUM_KEEP_CORRECTIONS on)")
    for p in cps:
        print(f"    {p['n']:>3}x  {p['committed']!r} -> {p['corrected']!r}")
    corr = s.get("suspected_corruption", [])
    if corr or s.get("corruption_pair_count"):
        sm = s.get("scramble_by_method", {})
        kinds = s.get("corruption_kind", {})
        surf = s.get("corruption_by_surface", {})
        # SCRAMBLE via a submitted-text capture (claude-transcript/vscode-ext) = the corruption was in
        # the text the user actually submitted => a real Part C bug. Via ax/keystroke = likely an AX
        # read taken mid-edit (a capture artifact). BLEED is accumulation (a neighbour commit merged or
        # the user kept writing), not a fix and not the insertion bug.
        real = sum(v for k, v in sm.items() if k in ("claude-transcript", "vscode-ext"))
        artifact = sum(v for k, v in sm.items() if k in ("ax", "keystroke"))
        print(f"\n  ⚠ OVERLAY CORRUPTION - {s.get('corruption_pair_count', 0)} pair(s), "
              f"{s.get('corruption_chars', 0)} chars (NOT user corrections - excluded from the rate & vocab above):")
        print(f"    scramble (insertion bug, Part C) : {kinds.get('scramble', 0)}   "
              f"bleed (accumulation / merged) : {kinds.get('bleed', 0)}")
        print(f"      └─ scramble in SUBMITTED text (claude-transcript/vscode-ext) : {real}  <- the live Part C bug")
        print(f"      └─ scramble likely a capture artifact (AX read mid-edit)     : {artifact}")
        if surf:
            print("    by surface: " + ", ".join(f"{k}={v}" for k, v in sorted(surf.items(), key=lambda kv: -kv[1])))
        for p in corr:
            print(f"    {p['n']:>3}x  {p['committed']!r} -> {p['corrected']!r}")


def main(argv):
    paths = []
    for a in (argv or ["dogfood/sessions/*.jsonl"]):
        paths.extend(glob.glob(a))
    if not paths:
        print("no dogfood logs found. Run: DUM_DOGFOOD_LOG=1 ./dum"); return 0
    print_report(summarize(load(paths)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
