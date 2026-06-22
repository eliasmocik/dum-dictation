# Feel-check — the manual UX gate (~2 min)

The bench (`scripts/test`) measures correctness + latency objectively. It **cannot** judge
how the tool *feels*: snappiness, word-by-word smoothness, wrong-word flicker, and the one
thing that matters most — **does it ever corrupt or lose text**. Run this by hand after any
change that could affect the live overlay, and a few times a week while dogfooding.

> Lesson that created this file (2026-06-15): the bench *disproved* the theory that chunkiness
> came from compute (it didn't), but a manual feel-check is what found the real fix (STEP=0.10).
> Numbers and feel catch different bugs. Keep both.

## Setup

Launch the real daily driver and put the cursor in a **scratch** doc (never real work):

```
./hovor-it
```

Test in **both** surfaces — they take different overlay paths:
- a plain-text app (TextEdit / a scratch note) → live-typed overlay
- a VS Code editor or terminal → live-typed overlay
- (optional) a rich-text app (Notes) → paste-at-commit path

Double-tap **left ⌘** to start/stop.

## Probes — dictate each, watch the screen

1. **Short:** "Let's ship this today."
2. **Long run-on (no pause):** "So the plan is to grow the corpus first and then fix the
   language model over-correction because the bench showed it is hurting accuracy right now."
3. **Technical:** "Get clone the GitHub repo and grab the errors in the nginx logs on localhost."
4. **Homophone precision:** "Let's grab a coffee after we grep the logs."

## Rate (pass / note)

| # | Dimension | What to feel for | P/F |
|---|---|---|---|
| 1 | **No corruption** (CRITICAL) | never deletes/scrambles text already on screen; cursor stays put | |
| 2 | **First word** | appears fast (~Apple), not a long stall | |
| 3 | **Word-by-word** | words stream out as you talk, not in big clumps | |
| 4 | **Flicker** | few/no wrong words that flash then change | |
| 5 | **Settle** | on pause, the final corrected line lands cleanly & quickly | |
| 6 | **Accuracy/terms** | tech terms right; "grab a coffee" stays grab; "grep the logs" becomes grep | |
| 7 | **Annoyance** | no stray sounds, no double-spaces, nothing that breaks flow | |

**Dimension 1 is a hard gate** — any text corruption = fail, stop and report, regardless of the rest.

## Log it

Append one line to `tests/feel-log.md` (date, build/commit, 1-line verdict + any fail).
That's how we notice feel drifting over time the way the baseline catches WER drift.
