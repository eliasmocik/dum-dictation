# Dev notes

Operational notes for working on the tool locally.

## Session-start ritual

Before changing anything, establish a known-good baseline:

1. **Run the gate** (objective: correctness + latency vs baseline):
   ```
   scripts/test
   ```
   It must end with `ALL GREEN`.
2. **Manual mic feel-check** (subjective: the gate can't judge how it *feels*). Launch
   `./dum` and dictate a couple of sentences into a scratch document — see
   [`tests/FEEL-CHECK.md`](tests/FEEL-CHECK.md) and [`smoke-test.md`](smoke-test.md) for the
   read-aloud lines. You're watching for: snappy word-by-word reveal, no wrong-word flicker,
   and above all **no corrupted or lost text**.

Numbers and feel catch different bugs — run both.

## The gate: `scripts/test`

```
scripts/test              # unit tests + bench vs tests/baseline.json; fails on regression
scripts/test --realtime   # also replay the corpus at mic cadence (true settle latency)
scripts/test --update     # accept current bench numbers as the new baseline
```

- **Unit suites** (pure logic): pipeline cleanups, overlay diff/reconcile, LLM guard,
  repo-harvest, fuzzy-recover safety, dogfood log, transcript join, activity monitor,
  insertion seam, atomic paste.
- **Bench** (`bench.py`) replays golden fixtures through the *real* `live.py` loop and scores
  WER, IT-term recall, and per-preview proc latency against `tests/baseline.json`.
- The corpus audio (`tests/corpus/*.wav`) is **local-only voice data and gitignored** — the
  bench skips any fixture whose WAV isn't present, so a fresh clone runs the unit suites and
  benches whatever audio you've recorded locally.

### Reading bench results

- Correctness must not regress: aim for `inject=0` everywhere, with term recall and WER matching
  the baseline.
- A `proc_med` (median preview latency) flag is usually **CPU-contention noise** — e.g. the
  bench running while `./dum` is also live — not a code regression. Re-run the bench alone
  to confirm before treating it as real.

## Known-bugs watch list

- **Quick-stop truncation** — stopping dictation immediately after speaking can leave a
  half-applied overlay reconcile (backspaces land but the retype is dropped), truncating the
  sentence tail. Watch for this when touching the overlay commit/reconcile path.
- **Editor AX blindness** — VS Code (Electron) doesn't expose its text to macOS Accessibility,
  so post-commit edit capture falls back to a content-free keystroke proxy there. The optional
  VS Code telemetry extension exists to close this gap.
- **Rich-text live preview** — apps that paste-at-commit (rich-text surfaces) don't show the
  word-by-word reveal; that's by design (overlay keystrokes would mangle rich text), not a bug.

## Useful env toggles

Most behavior is overridable per-run via `DUM_*` env vars (see the header comments in
`dum` and `live.py`). A few common ones: `DUM_MIC` (mic by name/index),
`DUM_LLM_MODEL` (swap the correction LLM), `DUM_VOCAB_DIR` (extra vocab packs),
`DUM_DOGFOOD_FULL=0` (disable all local capture).
