# Dev notes

## Session-start ritual

Establish a known-good baseline before changing anything. Numbers and feel catch different bugs; run both.

1. Gate: `scripts/test` must end with `ALL GREEN`.
2. Mic feel-check: launch `./dum`, dictate a couple of sentences into a scratch doc. Read-aloud lines in [`tests/FEEL-CHECK.md`](../tests/FEEL-CHECK.md) and [`smoke-test.md`](smoke-test.md). Watch for: snappy word-by-word reveal, no wrong-word flicker, and above all no corrupted or lost text.

## The gate: `scripts/test`

```
scripts/test              # unit tests + bench vs tests/baseline.json; fails on regression
scripts/test --realtime   # also replay the corpus at mic cadence (true settle latency)
scripts/test --update     # accept current bench numbers as the new baseline
```

- Unit suites (pure logic): pipeline cleanups, overlay diff/reconcile, LLM guard, repo-harvest, fuzzy-recover safety, dogfood log, transcript join, activity monitor, insertion seam, atomic paste.
- Bench (`bench.py`): replays golden fixtures through the real `live.py` loop; scores WER, IT-term recall, per-preview proc latency vs `tests/baseline.json`.
- Corpus audio (`tests/corpus/*.wav`) is local-only voice data, gitignored. Bench skips fixtures whose WAV is missing, so a fresh clone runs unit suites plus whatever audio you have.

### Reading bench results

- Correctness must not regress: `inject=0` everywhere, term recall and WER matching baseline.
- A `proc_med` (median preview latency) flag is usually CPU-contention noise (e.g. bench running while `./dum` is live), not a regression. Re-run the bench alone before treating it as real.

## Known-bugs watch list

- Quick-stop truncation: stopping right after speaking can leave a half-applied overlay reconcile (backspaces land, retype dropped), truncating the sentence tail. Watch this when touching the overlay commit/reconcile path.
- Editor AX blindness: VS Code (Electron) doesn't expose text to macOS Accessibility, so post-commit edit capture there falls back to a content-free keystroke proxy. The VS Code telemetry extension closes this gap.
- Rich-text live preview: paste-at-commit apps don't show the word-by-word reveal. By design (overlay keystrokes would mangle rich text), not a bug.

## Env toggles

Most behavior is overridable per-run via `DUM_*` vars (header comments in `dum` and `live.py`). Common ones:

- `DUM_MIC`: mic by name/index
- `DUM_LLM_MODEL`: swap the correction LLM
- `DUM_VOCAB_DIR`: extra vocab packs
- `DUM_DOGFOOD_FULL=0`: disable all local capture
