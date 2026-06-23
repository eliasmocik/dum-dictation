# Dogfood logging — what's collected, and your privacy controls

The dogfood logger (`dogfood_log.py`) measures the **User Correction Rate** — how much you manually
edit dictated text after it's committed — so we can objectively tell whether vocab/repo/fuzzy-symbol
features reduce your correction burden.

## Flag a bad dictation for review (double-tap left ⌥)

When a dictation comes out wrong and you want to revisit it later, **double-tap the LEFT Option (⌥)
key** — same gesture style as the double-tap left ⌘ that starts/stops dictation. It marks the **last**
committed dictation as a problem (a `user.verdict` event), plays a short cue, and logs `[FLAG]`. You
only ever flag when *dissatisfied* — there's no "good" button. The audio + correction context are
already saved, so each flagged item is fully reviewable offline. List them anytime:

```
.venv/bin/python scripts/analyze_user_corrections.py dogfood/sessions/*.jsonl   # 🚩 FLAGGED PROBLEMS at top
```

## On / off

- **One master switch: `DUM_DOGFOOD_FULL`.** Set it and the whole capture stack turns on —
  dogfood logging, audio retention, keystroke proxy, correction pairs, and fuzzy-symbol recovery.
- **Default OFF** at the code level (every piece defaults off → shipped builds are privacy-first).
- The `./dum` launcher sets `DUM_DOGFOOD_FULL=1` for your dogfood sessions.
- **Each piece still overrides individually**, e.g. `DUM_KEEP_AUDIO=0 ./dum`,
  `DUM_KEYSTROKE_PROXY=0 ./dum`, `DUM_KEEP_CORRECTIONS=0 ./dum`,
  `DUM_FUZZY_SYMBOLS=0 ./dum`. Disable everything: `DUM_DOGFOOD_FULL=0 ./dum`.

## Where it's stored (LOCAL ONLY)

- `dogfood/sessions/dictation-<session>.jsonl` — one file per run.
- `dogfood/audio/<session>/<commit_id>.wav` — the saved utterance clips (see Audio below).
- The entire `dogfood/` tree is **gitignored** — never committed, never uploaded, no network anywhere.
- **Delete everything:** `rm -rf dogfood/sessions dogfood/audio`

## Exactly what is logged

**Per commit** (`type: "commit"`): timestamp, session id, cwd, repo root (if a git repo), focused
app name, surface, insertion mode, the **raw recognizer transcript**, the **final committed text**,
whether it changed, model name, feature flags (`global_vocab`/`repo_vocab`/`fuzzy_symbols`/`llm`),
latency (ms), committed length, word count.

**Per commit, best-effort** (`type: "user.refix"`): the post-commit edit signal — `edit_distance`,
`normalized` rate, `accepted_unchanged`, and **truncated** snippets of the committed vs final text
(≤200 chars). If the focused field can't be read via Accessibility (most non-native apps), the event
is just `edit_capture: "unavailable"` — no field content captured.

## Audio retention (dogfood profile, default ON)

Each committed utterance is saved as a small WAV (16 kHz, ~32 KB/s) so any recognition failure can
be **replayed and re-run offline against pipeline/recognizer changes — in every app**, including the
ones where post-commit edit-capture is blind (VS Code, terminal). The commit record stores an
`audio_ref` (path + sha256 + seconds) pointing at the clip.

- **Default ON** in the dogfood profile (it's your own voice, local-only). Turn off per run with
  `DUM_KEEP_AUDIO=0 ./dum`.
- **Auto-pruned at session start** by BOTH caps, whichever bites first: older than **30 days** OR
  total over **2 GB** (oldest first). Override: `DUM_AUDIO_MAX_DAYS`, `DUM_AUDIO_MAX_GB`. A
  pruning `audio.prune` event is logged so dropped coverage is never silent.
- Written **after** the text is on screen, so it never adds to perceived dictation latency.
- **Shipped builds flip this OFF / opt-in** — audio is the most sensitive signal; only the dogfood
  profile defaults it on.

## Post-commit behaviour: did you fix it, or move on? (Step 4)

To interpret the correction rate honestly we need to know whether a post-commit change was *you
fixing the dictation* or *you moving on to another task*. AX text-capture is blind in your main apps
(VS Code, terminal) and gives false signals in others (a sent ChatGPT message clears the field and
looks like you deleted everything). So Step 4 adds two cheap, robust signals that work in **every**
app, recorded on each `user.refix` for the ~20s after a commit:

- **App-switch timeline** — `commit_app`, `app_switches[{t_rel, app}]`, `final_app`,
  `switched_away_s`. One thread polls the frontmost app ~1×/s. **App names only**, no content. A
  commit followed by an immediate switch to another app ≠ a commit followed by 20s of editing in
  place. This is the most reliable "moved on vs stayed" signal.
- **Keystroke proxy** — `keystroke_summary{backspaces, deletes, nav_keys, other_keys}`, **counts
  only, never which characters**, gated to the commit's app. Backspaces in the commit app ⇒ you
  actually edited; only forward typing ⇒ you kept working. Reuses Input Monitoring.
- **`capture_method`** (`ax` | `keystroke` | `unavailable`) records which edit signal was available.
- **`correction_pair`** (when AX-readable) — the **minimal changed-token diff** `committed -> corrected`
  (e.g. `postgress -> PostgreSQL`), NOT the whole field. This is the core learning signal and the
  vocab/alias-candidate source. Verbatim changed text => **default ON in dogfood** (your own text,
  local-only), `DUM_KEEP_CORRECTIONS=0` to disable (keeps the distance, drops the verbatim pair).
- Disable the keystroke proxy per run: `DUM_KEYSTROKE_PROXY=0 ./dum` (app-switch timeline stays).
- **Shipped builds flip the keystroke proxy AND correction_pair OFF / opt-in.** Dogfood-only default-on.

## Privacy guarantees

- **Never logs the surrounding document.** Edit capture only stores the dictated text and a truncated
  window of the *edited region* — not the whole file/field (`REDACT_MAX = 200` chars).
- The raw/final transcripts are **your dictated speech** (the thing being measured); they stay local.
- No cloud, no telemetry, no network calls.

## Analyze

```
.venv/bin/python scripts/analyze_user_corrections.py dogfood/sessions/*.jsonl
```
Reports first an **exhaustive edit-capture breakdown** — total commits = observable + unobservable
(unobservable = AX-unavailable + no-signal) + **coverage %** — then, **only on the observable subset**,
accepted-unchanged %, avg edit distance, User Correction Rate, corrections per 100 words. Plus
(always available) top repeated mishears and rate by app / repo / feature flags, and a
`DUM_FUZZY_SYMBOLS` on-vs-off comparison. Every correction-rate number is explicitly labelled
observable-only, so a "10% correction rate" at 20% coverage can't be mistaken for one at 90% coverage.

## Known limitation

Post-commit edit capture relies on macOS Accessibility reading the focused text field, which many apps
(terminals, VS Code, browsers) don't expose — so `edit_capture` is often `unavailable`. The analyzer
reports **coverage**; commit-level stats (volume, mishears, by app/repo/flag) are always available even
when edit capture isn't. Improving capture (per-app AX handlers, or an inserted-range diff) is the
defined next step.
