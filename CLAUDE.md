# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**dum dictation** — a local, private, on-device dictation tool (Python 3.12). A live mic stream
becomes corrected text typed into whatever app is focused. The whole point is getting technical
vocab right (`git`, `kubectl`, `nginx`, `PostgreSQL`) where normal dictation hears "get hub" or
"engine x". No cloud, no network — everything runs on the machine.

> Platform note: OS-specific behaviour is isolated behind `platform_io.py`, one class per OS.
> **macOS** (Quartz/AppKit + the MLX homophone LLM), **Windows** (SendInput/win32clipboard/winsound,
> no LLM), and **Linux/X11** (xdotool + xclip/wl-clipboard, no LLM) are all implemented;
> `FallbackPlatform` is the last resort for any other OS (and what LinuxPlatform degrades to with no
> X11 tools). This dev checkout is Linux/WSL with no GUI, so the live OS paths can't run here — but
> the **unit suites are pure logic and run anywhere**, and they cover the cross-platform decomposable
> parts of every OS backend. The bench needs local corpus audio.

## Commands

```sh
# macOS / Linux (bash):
./setup                   # one-time: venv + pinned deps + Parakeet model (+ LLM pre-pull on macOS only)
./dum                     # run the daily driver (mac: double-tap LEFT ⌘; linux: double-tap RIGHT Ctrl)
./dum --tray              # same, but as a menu-bar/tray app (icon + Start/Stop/Quit, no babysat terminal)
./dum --config            # re-run the first-run mic/hotkey wizard
./dum --install-autostart # login item (auto-start + relaunch-on-crash); --uninstall-autostart / --autostart-status

# Windows (PowerShell) — same flags, mirror scripts:
.\setup.ps1               # venv + deps (pywin32; MLX/pyobjc skipped via markers) + Parakeet model
.\dum.ps1                 # daily driver (double-tap RIGHT Ctrl; no --llm — MLX is Apple-Silicon only)
.\dum.ps1 --tray          # tray app via pythonw (no console window)

scripts/test              # THE DEV GATE — unit suites + bench vs baseline; must print "ALL GREEN"
scripts/test --realtime   # also replay the corpus at true mic cadence (real settle latency)
scripts/test --update     # accept current bench numbers as the new tests/baseline.json
```

Run a single unit suite directly (set `PYTHONPATH` so the flat `import pipeline` style resolves —
engine code is in `src/` but tests import it unqualified):

```sh
PYTHONPATH=src .venv/bin/python tests/test_overlay.py
```

Headless pipeline replay (no mic) — the key offline-iteration tool:

```sh
.venv/bin/python src/live.py --replay <wav>        # push a WAV through the real consumer loop
.venv/bin/python src/live.py --replay-fast <wav>   # same, skip real-time pacing
.venv/bin/python src/live.py --list-devices        # list mics (for --mic / DUM_MIC)
```

**Always run `scripts/test` before committing** — correctness (IT-term recall, WER, zero
over-correction) must not regress (tolerance: WER +3pts, term recall −0, proc median +30%).

## Architecture

Flow: **capture → VAD → recognize → correct → insert**, single-consumer by design.

- **`src/live.py`** (the core, ~1100 lines) — owns the whole loop. The mic callback *only enqueues*
  frames; one consumer thread owns the recognizer + pipeline + insertion. If transcription falls
  behind, **previews are dropped — never audio, never the final commit transcription.** This is also
  where the pipeline and insertion backends are wired together and where most `DUM_*` env defaults
  and CLI flags are resolved.
- **Recognizer** — Parakeet TDT 0.6b v3 (int8) via `sherpa-onnx` (offline transducer, greedy).
  Model lives under `models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/` and is found at runtime by
  a glob (`model_utils.find_model_dir`), not hard-coded. Runs a *growing window per sentence* with a
  **lock-and-trim** scheme that bounds the decode window so per-preview latency stays ~constant.
- **`src/pipeline.py`** — an ordered list of `Stage`s; each is `text -> (text, events)`. Order
  (built in `live.py`): punctuation cleanup → phonetic/phrase-alias → external-corrector seam (inert)
  → personal-correction seam (inert) → fuzzy-symbol recovery → protected-words → sentence
  capitalization (last, to undo lowercasing by earlier stages). Several stages are **defined-but-inert
  seams** gated off by default — keep them no-op unless their env flag is set.
- **`src/llm_stage.py`** — on-device homophone fixer (`grep`/`grab`, `git`/`get`) using a 4-bit
  Llama-3.2-1B via `mlx_lm`. Guarded to only edit when confident; built lazily on the consumer
  thread. **The bench runs WITHOUT the LLM** (deterministic) — so LLM-only fixes show as "missing
  terms" in bench output; that's expected, not a regression.
- **`src/insertion.py` / `src/overlay.py`** — the one narrow `InsertionBackend` seam, the *only*
  place text reaches the screen. **Overlay** types word-by-word as you speak and reconciles
  (backspace + retype) on pause — used in editors/terminals. **Paste** finalizes at commit via the
  clipboard — used for rich-text surfaces that scramble under synthetic keystrokes. `live.py` picks
  per focused app (overlay by default; a small block list routes to paste).
- **`src/platform_io.py`** — the OS seam, one class per platform behind `get_platform()`.
  `MacPlatform` (Quartz CGEvent keystrokes, AppKit NSPasteboard, AX reads), `WindowsPlatform`
  (ctypes SendInput Unicode typing, win32clipboard save/restore, winsound, GetForegroundWindow),
  `LinuxPlatform` (xdotool type, xclip/wl-clipboard, a bell, xdotool app-detect; degrades to pynput
  when X11 tools are absent), `FallbackPlatform` (pynput typing, last resort for other OSes). All
  native backends override `type_text` with raw-Unicode insertion so a dead-key layout (e.g. Slovak)
  isn't mangled. The hotkey listener + overlay backspaces ride on pynput (cross-platform) everywhere.

### Robust launch (menu bar + auto-start + single-instance)

The "feels like a real app, no babysat terminal" layer — added in the cross-platform port work,
behind thin OS seams (same philosophy as `platform_io.py`):

- **`src/single_instance.py`** — an exclusive lock on `~/.dum/dum.lock` (`fcntl.flock` on macOS/Linux,
  `msvcrt.locking` on Windows, behind one interface); a second live copy exits with `AlreadyRunning`.
  Acquired in `live.py` for the live daily-driver modes only (NOT `--replay`/`--list-devices`/bench),
  so the test gate is unaffected. Guards the single-owner resources: mic, global hotkey (two pynput
  listeners can get the process OS-aborted), and overlay.
- **`src/tray.py`** — `pystray` menu-bar/tray front-end (cross-platform: macOS menu bar, Windows tray).
  **The tray owns the main thread** (required for the macOS GUI run loop); the pynput hotkey listener +
  recognizer stay on background threads. A watcher thread mirrors `app.running` onto the icon, so the
  double-tap hotkey and the menu reflect the same state. `TrayController` is the GUI-free, unit-tested
  glue; pystray/pillow imports are lazy.
- **`src/autostart.py`** — login-item installer, one interface (`install`/`uninstall`/`status`) over
  three backends: macOS = a launchd LaunchAgent (`RunAtLoad` + `KeepAlive={SuccessfulExit:false}`),
  Windows = a Task Scheduler task (`LogonTrigger` + `RestartOnFailure`), Linux = a `systemd --user`
  unit (`WantedBy=default.target` + `Restart=on-failure`) — all running the `dum`/`dum.ps1` launcher
  with `--tray`. The `build_plist_dict` / `build_task_xml` / `build_unit` builders are pure + unit-tested.

The launch + hotkey + platform code is decomposed so its **pure logic is unit-tested and runs on
Linux/WSL** (`test_single_instance`, `test_autostart` — both plist and Windows-task XML —, `test_tray`,
`test_platform_dispatch`, `test_config`). Only the live OS calls (launchctl/schtasks, real menu-bar
rendering, SendInput/win32 clipboard) need their actual OS to exercise.

### Vocabulary (`packs/*.aliases`)

Phrase aliases map *misheard spoken forms* to canonical tech terms, e.g. `engine x => nginx`.
Format: `spoken form => CanonicalForm`, left-matched case-insensitively and word-bounded; aliases
are **additive**. The shipped `packs/global-tech.aliases` is always on; extra packs load via
`DUM_VOCAB_DIR`.

**The one rule (read `docs/CONTRIBUTING.md` before adding any alias):** only **General** mishears
(any standard-English speaker hits them — `"ten stack query" => TanStack Query`) belong in the
shipped packs. **Personal** idiolect/accent fixes (`"JITHUB" => GitHub`) must NOT ship — they'd
break the tool for everyone else. Litmus: *"Would a general user speaking standard English produce
this same error?"* When in doubt, leave it out.

### Telemetry / dogfood seam (opt-in, local-only)

`src/dogfood_log.py` + `src/events.py` + `src/activity_monitor.py` measure how often dictated text
gets manually corrected — the signal for finding vocab gaps. Off by default at the engine level; the
`./dum` launcher turns it on for development (`DUM_DOGFOOD_FULL=1`). Writes only to the gitignored
`dogfood/` tree; makes no network calls. `vscode-dum-telemetry/` is an optional VS Code extension
that reports post-commit edits from the document model (Electron apps are invisible to macOS AX) — it
only observes, never inserts. Full detail + privacy controls in `docs/DOGFOOD.md`.

## Conventions & gotchas

- **Behavior is tuned via `DUM_*` env vars**, not config edits — see the header comments in `dum`
  and the docstring of `src/live.py` for the full set. Common: `DUM_MIC` (mic by name/index),
  `DUM_LLM_MODEL`, `DUM_VOCAB_DIR`, `DUM_STEP`/`DUM_MIN_SIL` (cadence), `DUM_DOGFOOD_FULL=0` (kill all
  local capture). Saved user config (mic/hotkey) lives in `~/.dum/config.json`; precedence is
  flag/env > saved config > built-in default — **don't bake a `DUM_MIC` default into `dum`**, it
  would shadow saved config.
- **`src/` on the path, resources at root**: tests and modules `import pipeline` flatly; `scripts/test`
  and `setup` set `PYTHONPATH=src`. Resource files (`terms.txt`, `models/`) stay at the repo root and
  are located via a `HERE` anchor in the modules.
- **Voice data never gets committed** — `models/`, `dogfood/`, `recordings/`, `*.wav`, `*.jsonl`, and
  `tests/corpus/` are gitignored. A fresh clone has the test *manifest* (`tests/fixtures.json`) +
  baseline but no audio; the bench *skips* fixtures whose WAV is absent. `CLAUDE.local.md` is also
  gitignored.
- **The "never corrupt text" guarantee** is enforced by `tests/test_overlay.py` (exact keystroke
  diffs proven before they drive a real cursor) and `tests/test_early_stop.py` (the known
  quick-stop-truncation / half-applied-reconcile bug). Be especially careful editing the overlay
  commit/reconcile path — see the known-bugs watch list in `docs/DEV-NOTES.md`.
- Numbers and feel catch different bugs: after the gate is green, a **manual mic feel-check** is part
  of the loop (see `tests/FEEL-CHECK.md` / `smoke-test.md`) — watch for snappy reveal, no wrong-word
  flicker, and above all no lost/corrupted text.

## Key docs

`docs/ARCHITECTURE.md` (pipeline deep-dive) · `docs/DEV-NOTES.md` (dev loop + known-bugs list) ·
`docs/CONTRIBUTING.md` (the General-vs-Personal vocab rule) · `docs/DOGFOOD.md` (telemetry/privacy) ·
`tests/README.md` (test layers + bench metrics).
