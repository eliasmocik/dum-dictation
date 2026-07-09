# Testing system - dum dictation

The dev gate. Run before every commit:

```
scripts/test              # unit tests + bench vs baseline; fails on regression
scripts/test --realtime   # also feed the corpus at mic cadence
scripts/test --update      # accept current bench numbers as the new baseline
```

## Layers

**Unit (pure logic, instant, no audio):**
- `test_pipeline.py` - correction text cleanups (micro-pause punctuation, dedup).
- `test_overlay.py` - overlay diff/reconcile + dry typer (proves keystroke diffs are exact
  *before* they ever drive a real cursor - the "never corrupt text" guarantee).
- `test_lock_trim.py` - offline comparison of growing-window vs lock-and-trim decode.

**Bench (the real pipeline, `bench.py`):**
Replays each golden fixture through the ACTUAL `live.py` loop via `LiveDictation.replay()`
- VAD → previews → lock-trim → corrections → commit, overlay in dry mode - and scores the
real output (not a re-implementation). Metrics: WER%, IT-term recall, preview proc
(median/max + % over the cadence budget = chunk risk), deferred-to-commit count, settle ms.
Compares to `tests/baseline.json`; exits non-zero on regression beyond tolerance
(WER +3pts, term recall -0, proc median +30%).

## Replay mode

`live.py --replay <wav>` pushes a WAV through the real consumer loop headlessly (no mic),
emitting the same `trace.jsonl` / `events.jsonl` a live session does. `--replay-fast` skips
real-time pacing. This is what makes the pipeline testable offline - most iteration is now
metrics-driven; you only do a final live feel-check when the numbers look good.

## Corpus

`tests/fixtures.json` lists WAV + reference-transcript pairs. **Corpus audio is local-only**
(it's voice - gitignored via `*.wav`), so a fresh clone has the manifest + baseline but not
the audio; bench skips fixtures whose WAV is absent. To add a fixture: record a clip, drop it
in `recordings/`, write its reference once, append an entry, and `scripts/test --update`.

## Notes / known
- Bench runs **without the LLM** (deterministic + fast). So homophone fixes that need the LLM
  (`grab→grep`, and `localhost`/`SSH` when mis-heard) show as "missing terms" - expected; the
  gate covers the deterministic pipeline (phonetic, lock-trim, VAD, overlay). An `--llm` bench
  variant can be added when we want to gate the LLM layer too.
- At `STEP=0.10`, preview proc (~150ms) exceeds the 100ms cadence budget (over% ~65%). It's
  **bounded** (lock-trim caps the window), so it doesn't compound - effective cadence ~150ms,
  which felt good. Tightening `LOCK_CONTEXT`/`LOCK_MARGIN` to get proc under 100ms is a future
  optimization the bench will measure.
