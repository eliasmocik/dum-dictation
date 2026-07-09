#!/usr/bin/env python3
"""
bench.py - regression bench for the dum dictation pipeline.

Replays each golden fixture (tests/fixtures.json) through the REAL live.py loop
(VAD -> previews -> lock-trim -> corrections -> commit, via LiveDictation.replay),
then scores the ACTUAL output - not a re-implementation. Metrics per fixture:

  WER%        word error rate of the committed text vs the reference (lower=better)
  raw WER%    same, on the pre-correction transcript (shows what corrections buy)
  terms       IT-term recall: expected terms found in the output / total
  proc med/max  preview decode time (ms) - responsiveness (should stay < STEP budget)
  over%       % of previews slower than the cadence budget (chunk risk; want ~0)
  deferred    big tail rewrites pushed to commit (lower=streams more, dumps less)

Compares against tests/baseline.json and FAILS (exit 1) on regression beyond tolerance.

  .venv/bin/python bench.py                 # fast (WER/terms/proc) - the commit gate
  .venv/bin/python bench.py --realtime      # also true first-word/settle latency
  .venv/bin/python bench.py --update-baseline   # accept current numbers as the baseline
"""
import os, sys, json, re
os.environ.setdefault("DUM_OVERLAY_QUIET", "1")   # silence dry-overlay keystroke logs
from pathlib import Path
import jiwer

from live import (build_parakeet, find_model_dir, build_pipeline, load_terms,
                  LiveDictation, Tracer, EventBus, HERE, STEP_S)

FIXTURES = HERE / "tests" / "fixtures.json"
BASELINE = HERE / "tests" / "baseline.json"
OUTDIR = HERE / "tests" / ".bench-out"      # per-fixture events/trace (gitignored)

# regression tolerances (vs baseline)
TOL_WER = 3.0          # WER may rise at most this many absolute points
TOL_TERMS = 0          # term recall may drop at most this many terms
TOL_PROC = 1.30        # proc median may rise at most this factor

_norm = jiwer.Compose([
    jiwer.ToLowerCase(),
    jiwer.RemovePunctuation(),
    jiwer.RemoveMultipleSpaces(),
    jiwer.Strip(),
    jiwer.ReduceToListOfListOfWords(),
])


def wer(ref, hyp):
    if not hyp.strip():
        return 100.0
    return round(jiwer.wer(reference=ref, hypothesis=hyp,
                           reference_transform=_norm, hypothesis_transform=_norm) * 100, 1)


def _terms_in(text, vocab):
    toks = set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())
    return {t for t in vocab if re.sub(r"[^a-z0-9]", "", t.lower()) in toks}


def term_stats(ref, hyp, vocab):
    """Expected terms are derived from the REFERENCE (what was actually said), not a fixed
    list - so each clip is scored on its own content. Returns recall + the over-correction
    signal: IT terms present in the output but NOT in the reference = wrongly injected."""
    rset, hset = _terms_in(ref, vocab), _terms_in(hyp, vocab)
    found = rset & hset
    injected = hset - rset
    return len(found), len(rset), sorted(rset - found), sorted(injected)


def run_fixture(rec, fx, realtime, use_llm=False):
    name = fx["name"]
    OUTDIR.mkdir(parents=True, exist_ok=True)
    ev_path = OUTDIR / f"{name}.events.jsonl"
    tr_path = OUTDIR / f"{name}.trace.jsonl"
    ev_path.unlink(missing_ok=True); tr_path.unlink(missing_ok=True)

    terms = load_terms([HERE / "terms.txt"], None)
    pipe = build_pipeline(terms)
    bus = EventBus(str(ev_path))
    tracer = Tracer(str(tr_path))
    app = LiveDictation(rec, pipe, bus, do_paste=False, use_llm=use_llm, terms=terms,
                        overlay=True, tracer=tracer)
    app.replay(str(HERE / fx["wav"]), realtime=realtime)
    tracer.close()

    raw, fixed = [], []
    for l in ev_path.read_text().splitlines():
        if not l.strip():
            continue
        e = json.loads(l)
        if e.get("type") == "commit":
            raw.append(e["raw"]); fixed.append(e["fixed"])
    raw_txt, fixed_txt = " ".join(raw), " ".join(fixed)

    ref = (HERE / fx["ref"]).read_text().strip()
    vocab = [t.strip() for t in (HERE / fx["terms"]).read_text().splitlines() if t.strip()]
    nfound, ntot, missing, injected = term_stats(ref, fixed_txt, vocab)

    procs, deferred, locks, settle = [], 0, [], []
    # reveal granularity: new words that appear per FORWARD overlay step (the chunkiness
    # Elias feels). Tracked via a per-sentence high-water mark of words shown, so we count
    # only genuinely-new words and correction churn (early_fix backspace+regrow) doesn't
    # inflate it. mean ~1.0 == true one-by-one; >2 == words arrive in clumps. This is the
    # Phase-1 baseline metric we tune DISPLAY_MARGIN against (not a gate - a feel signal).
    reveals, hwm = [], 0
    for l in tr_path.read_text().splitlines():
        if not l.strip():
            continue
        e = json.loads(l)
        ev = e.get("ev")
        if ev == "onset":
            hwm = 0
        elif ev == "preview":
            procs.append(e["proc_ms"]); locks.append(e.get("locked", 0))
        elif ev in ("lock", "early_fix"):
            n = len(e.get("words", []))
            if n > hwm:                      # forward progress -> n-hwm new words this step
                reveals.append(n - hwm); hwm = n
        elif ev == "deferred":
            deferred += 1
        elif ev == "commit":
            hwm = 0
            if e.get("settle_ms") is not None:
                settle.append(e["settle_ms"])

    def med(xs):
        return round(sorted(xs)[len(xs) // 2]) if xs else 0

    m = {
        "wer": wer(ref, fixed_txt),
        "raw_wer": wer(ref, raw_txt),
        "terms": nfound, "terms_total": ntot, "missing": missing, "injected": injected,
        "proc_med": med(procs), "proc_max": max(procs) if procs else 0,
        "over_pct": round(100 * sum(1 for p in procs if p > STEP_S * 1000) / len(procs)) if procs else 0,
        "deferred": deferred, "lock_max": max(locks) if locks else 0,
        "settle_med": med(settle), "sentences": len(fixed),
        "reveal_mean": round(sum(reveals) / len(reveals), 2) if reveals else 0,
        "reveal_max": max(reveals) if reveals else 0,
        "reveal_steps": len(reveals),
    }
    return m


def main():
    argv = sys.argv[1:]
    realtime = "--realtime" in argv
    update = "--update-baseline" in argv
    use_llm = "--llm" in argv      # inspection run: real daily-driver quality, NOT gated
    fixtures = json.loads(FIXTURES.read_text())["fixtures"]

    present = [fx for fx in fixtures if (HERE / fx["wav"]).exists()]
    missing = [fx for fx in fixtures if not (HERE / fx["wav"]).exists()]
    for fx in missing:
        print(f"  [skip] {fx['name']}: {fx['wav']} not found (corpus audio is local-only)")
    if not present:
        print("no fixture audio found - nothing to bench. Place the corpus WAVs locally.")
        return 0

    print("building model (once)...")
    rec = build_parakeet(find_model_dir("sherpa-onnx-nemo-parakeet-tdt-*"))
    results = {}
    for fx in present:
        tag = ("realtime" if realtime else "fast") + (" +LLM" if use_llm else "")
        print(f"  replaying {fx['name']} ({tag})...")
        results[fx["name"]] = run_fixture(rec, fx, realtime, use_llm=use_llm)

    print("\n" + "=" * 74)
    print(f"BENCH  (STEP budget {int(STEP_S*1000)}ms)")
    print("=" * 74)
    hdr = (f"{'fixture':<16}{'WER%':>6}{'raw%':>6}{'terms':>7}{'inject':>7}"
           f"{'procMed':>8}{'over%':>7}{'defer':>7}")
    print(hdr)
    for name, m in results.items():
        print(f"{name:<16}{m['wer']:>6}{m['raw_wer']:>6}{str(m['terms'])+'/'+str(m['terms_total']):>7}"
              f"{len(m['injected']):>7}{m['proc_med']:>8}{m['over_pct']:>7}{m['deferred']:>7}")
        if m["missing"]:
            print(f"{'':<16}missing terms: {', '.join(m['missing'])}")
        if m["injected"]:
            print(f"{'':<16}OVER-CORRECTED (injected terms): {', '.join(m['injected'])}")
        print(f"{'':<16}settle {m['settle_med']}ms  sentences {m['sentences']}  "
              f"reveal {m['reveal_mean']}/{m['reveal_max']} words per step ({m['reveal_steps']} steps)")

    if use_llm and not update:
        print("\n[--llm inspection run: real daily-driver quality, NOT gated "
              "(the committed baseline is deterministic no-LLM)]")
        return 0

    if update or not BASELINE.exists():
        BASELINE.write_text(json.dumps(results, indent=2) + "\n")
        print(f"\n[baseline {'updated' if BASELINE.exists() else 'written'}] -> {BASELINE.name}")
        return 0

    base = json.loads(BASELINE.read_text())
    fails = []
    for name, m in results.items():
        b = base.get(name)
        if not b:
            print(f"\n[new fixture {name} - no baseline, skipping gate]")
            continue
        if m["wer"] > b["wer"] + TOL_WER:
            fails.append(f"{name}: WER {b['wer']}->{m['wer']} (> +{TOL_WER})")
        if m["terms"] < b["terms"] - TOL_TERMS:
            fails.append(f"{name}: term recall {b['terms']}->{m['terms']}")
        if b["proc_med"] and m["proc_med"] > b["proc_med"] * TOL_PROC:
            fails.append(f"{name}: proc_med {b['proc_med']}->{m['proc_med']} (> x{TOL_PROC})")
        if len(m["injected"]) > len(b.get("injected", [])):
            fails.append(f"{name}: over-correction {b.get('injected', [])}->{m['injected']}")

    if fails:
        print("\n*** REGRESSION ***")
        for f in fails:
            print("  -", f)
        return 1
    print("\nOK: no regression vs baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
