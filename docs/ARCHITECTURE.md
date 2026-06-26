# Architecture

A live microphone stream becomes corrected text in the focused app, entirely on-device. The
flow is: **capture → VAD → recognize → correct → insert**, with optional local telemetry on the
side.

```
mic ─► audio callback (enqueue only)
         │
         ▼
   consumer thread
     energy VAD ──► segments on pauses
         │
         ▼
   Parakeet recognizer (sherpa-onnx, offline transducer, growing window per sentence)
         │  live word-by-word previews + a final transcript at the pause
         ▼
   correction pipeline (ordered stages)
         │
         ▼
   insertion backend (overlay-type or paste at the cursor)
```

The core (`live.py`) is single-consumer by design: the audio callback only enqueues frames, and
one worker thread owns the recognizer, the pipeline, and insertion. If transcription falls
behind, **previews** are dropped — never audio, and never the final commit transcription.

## The engine

Speech recognition is **Parakeet TDT 0.6b v3 (int8)** run through
[`sherpa-onnx`](https://github.com/k2-fsa/sherpa-onnx) as an offline transducer
(`model_type="nemo_transducer"`, greedy decoding). The model lives under
`models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/` (3 `.onnx` files + `tokens.txt`) and is
located at runtime by a glob, so the exact directory is discovered rather than hard-coded.

Per sentence the recognizer runs on a growing audio window, producing word-by-word previews as
you speak and a final transcript once the VAD detects a trailing pause. A lock-and-trim scheme
keeps the decode window bounded so per-preview latency stays roughly constant instead of
growing with sentence length.

## The correction pipeline

`pipeline.py` is an ordered list of stages; each takes text and returns `(text, events)`.
Built and ordered in `live.py`:

1. **Punctuation cleanup** — drops the spurious sentence-final marks Parakeet inserts at
   micro-pauses (e.g. `See? this` → `See this`).
2. **Phonetic / phrase-alias correction** — the technical-vocab layer. A shipped global pack
   (`packs/*.aliases`, always on) maps misheard spoken forms to canonical tech terms
   (`engine x` → `nginx`, `cube control` → `kubectl`), plus optional user/repo packs via
   `DUM_VOCAB_DIR`. Aliases are *additive* and word-bounded.
3. **External corrector seam** — an inert boundary where an out-of-process corrector can plug in
   over stdio; disabled unless `DUM_EXTERNAL_CORRECTOR` points at an executable.
4. **Personal-correction seam** — a defined-but-inert passthrough for future per-user learned
   corrections; gated by `DUM_PERSONAL_CORRECTIONS`, no-op by default.
5. **Fuzzy-symbol recovery** — best-effort recovery of distinctive identifiers (gated).
6. **Protected words** — guards canonical forms from being re-mangled.
7. **Sentence capitalization** — re-capitalizes real sentence starts last, after the alias/LLM
   layers may have lowercased a leading word.

There is also an **on-device LLM stage** (`llm_stage.py`): a 4-bit Llama-3.2-1B run via
[`mlx_lm`](https://github.com/ml-explore/mlx) on Apple Silicon, used for homophone-class fixes
(`grep`/`grab`, `git`/`get`). It is guarded so it only edits when confident and is built lazily
on the consumer thread. Model id defaults to `mlx-community/Llama-3.2-1B-Instruct-4bit`,
overridable with `DUM_LLM_MODEL`.

## Insertion: overlay vs paste

`insertion.py` defines one narrow `InsertionBackend` seam — the only place text reaches the
screen. Backends do insertion *only* (no recognition/correction/telemetry):

- **Overlay** (`overlay.py`) — types text via synthetic keystrokes word-by-word as you speak,
  reconciling (backspace + retype) on a pause when the corrected sentence differs from the
  preview. This gives the live, growing feel and is used in editors and terminals.
- **Paste** — finalizes the corrected sentence at the cursor via the clipboard at commit time
  (clipboard saved/restored). Used where live keystroke editing would mangle rich text.

`live.py` chooses per focused app: it overlays everywhere by default and routes a small block
list of surfaces that scramble under synthetic keystrokes to paste-at-commit instead. App
detection is handled in `platform_io.py` — the single OS seam, one class per platform behind
`get_platform()`: `MacPlatform` (Quartz CGEvent keystrokes, AppKit `NSPasteboard`, Accessibility
reads), `WindowsPlatform` (ctypes `SendInput` Unicode typing, `win32clipboard` save/restore,
`winsound`, `GetForegroundWindow`), `LinuxPlatform` (`xdotool type`, `xclip`/`wl-clipboard`, a bell,
`xdotool` app-detect — degrading to pynput when the X11 tools are absent), and `FallbackPlatform`
(pynput typing, the last resort for any other OS). All native backends post **raw Unicode** for
typing rather than going through pynput, so a dead-key keyboard layout (e.g. Slovak) doesn't mangle
the output. The hotkey listener and the overlay's backspaces are pynput on every platform.

## Telemetry / dogfood seam (opt-in)

`dogfood_log.py` + `events.py` + `activity_monitor.py` form an **opt-in, local-only** seam that
measures how often dictated text gets manually corrected — the signal used to find vocab gaps.
It is off by default at the engine level (the `./dum` launcher turns it on for development),
writes only to the gitignored `dogfood/` tree, and makes no network calls. The optional VS Code
extension in `vscode-dum-telemetry/` closes the editor-coverage gap by reporting post-commit
edits from the document model; it only observes, never inserts. Full detail and the privacy
controls are in [`DOGFOOD.md`](DOGFOOD.md).

## Launch & lifecycle (the "real app" layer)

Three small modules turn the babysat-terminal launcher into an always-there app, each behind a
thin OS seam (macOS, Windows and Linux all implemented):

- **single-instance** (`single_instance.py`) — an exclusive lock on `~/.dum/dum.lock` (`flock` on
  macOS/Linux, `msvcrt.locking` on Windows); a second live copy exits cleanly. The mic, the global
  double-tap hotkey, and the overlay are single-owner (two hotkey listeners can even get the process
  OS-aborted), so exactly one robot may run. The lock is taken only for the live modes, never for
  `--replay`/bench, so the test gate is unaffected.
- **tray** (`tray.py`, `--tray`) — a `pystray` menu-bar/tray icon (Start/Stop/Quit + listening
  state), the same code on the macOS menu bar and the Windows tray. It **owns the main thread** (the
  macOS GUI loop's requirement); the hotkey listener and recognizer run underneath on their existing
  background threads, and a watcher mirrors the real `app.running` state onto the icon so the hotkey
  and the menu never disagree.
- **auto-start** (`autostart.py`, `--install-autostart`) — one `install`/`uninstall`/`status`
  interface over three backends, all start-at-login + relaunch-on-crash (honoring a clean Quit): a
  macOS launchd LaunchAgent (`RunAtLoad` + `KeepAlive={SuccessfulExit:false}`), a Windows Task
  Scheduler task (`LogonTrigger` + `RestartOnFailure`), and a Linux `systemd --user` unit
  (`WantedBy=default.target` + `Restart=on-failure`, `After=graphical-session.target`). All run the
  platform launcher (`dum` / `dum.ps1`) with `--tray`. (A launchd-spawned `python` is a different
  binary than your terminal, so macOS re-asks for the three permissions the first time — inherent to
  non-bundled login items; Windows/Linux have no such re-prompt.)
