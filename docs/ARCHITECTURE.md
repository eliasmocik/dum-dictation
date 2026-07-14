# Architecture

Mic audio => corrected text in the focused app, all on-device.

Flow: capture => VAD => recognize => correct => insert. Optional local telemetry on the side.

- Audio callback only enqueues frames.
- One consumer thread owns the recognizer, pipeline, and insertion (`live.py`).
- If transcription falls behind, previews are dropped. Audio and the final commit never are.

## Engine

- Parakeet TDT 0.6b v3 (int8) via [`sherpa-onnx`](https://github.com/k2-fsa/sherpa-onnx), offline transducer (`model_type="nemo_transducer"`, greedy decoding).
- Model dir: `models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/` (3 `.onnx` files + `tokens.txt`), found at runtime by glob.
- Per sentence: growing audio window gives word-by-word previews; VAD pause triggers the final transcript.
- Lock-and-trim bounds the decode window so per-preview latency stays roughly constant.

## Correction pipeline

`pipeline.py`: ordered stages, each `text => (text, events)`. Built and ordered in `live.py`:

1. Punctuation cleanup: drops spurious sentence-final marks at micro-pauses (`See? this` => `See this`).
2. Phonetic / phrase-alias correction: tech-vocab layer. Shipped global pack `packs/*.aliases` (always on) maps misheard forms to canonical terms (`engine x` => `nginx`, `cube control` => `kubectl`). Extra user/repo packs via `DUM_VOCAB_DIR`. Aliases are additive and word-bounded.
3. External corrector seam: out-of-process corrector over stdio. Inert unless `DUM_EXTERNAL_CORRECTOR` points at an executable.
4. Personal-correction seam: future per-user learned corrections. Gated by `DUM_PERSONAL_CORRECTIONS`, no-op by default.
5. Fuzzy-symbol recovery: best-effort recovery of distinctive identifiers (gated).
6. Protected words: guards canonical forms from being re-mangled.
7. Sentence capitalization: last, because alias/LLM stages may lowercase a leading word.

LLM stage (`llm_stage.py`): 4-bit Llama-3.2-1B via [`mlx_lm`](https://github.com/ml-explore/mlx), Apple Silicon only. Fixes homophone classes (`grep`/`grab`, `git`/`get`). Only edits when confident; built lazily on the consumer thread. Default model `mlx-community/Llama-3.2-1B-Instruct-4bit`, override with `DUM_LLM_MODEL`.

## Insertion: overlay vs paste

`insertion.py` defines the `InsertionBackend` seam - the only place text reaches the screen. Backends insert only, nothing else.

- Overlay (`overlay.py`): synthetic keystrokes word-by-word as you speak; reconciles (backspace + retype) at the pause if the corrected sentence differs from the preview. Used in editors and terminals.
- Paste: corrected sentence via clipboard at commit (clipboard saved/restored). Used where live keystrokes would mangle rich text.

`live.py` picks per focused app: overlay by default, paste-at-commit for a small block list of surfaces that scramble under synthetic keystrokes.

## OS seam: `platform_io.py`

One class per platform behind `get_platform()`:

- `MacPlatform`: Quartz CGEvent keystrokes, AppKit `NSPasteboard`, Accessibility reads.
- `WindowsPlatform`: ctypes `SendInput` Unicode typing, `win32clipboard` save/restore, `winsound`, `GetForegroundWindow`.
- `LinuxPlatform`: session-aware (X11 vs Wayland, detected once at construction).
  - **X11**: `xdotool type` (typing + Ctrl+V paste), `xclip`, `xdotool` app-detect.
  - **Wayland**: `ydotool` (typing, Backspace, arrows via uinput keycodes; the daemon socket is auto-resolved across `/tmp` and `$XDG_RUNTIME_DIR` and exported as `YDOTOOL_SOCKET`), `wl-clipboard` paste. `xdotool` is never used on Wayland (it's an X11-only no-op that silently drops text). No app-detection (so the focus guard is off).
  - Both fall back to `pynput` when the native tool is missing or its call fails.
- `FallbackPlatform`: pynput typing, last resort.

Native backends post raw Unicode for typing (not pynput), so dead-key layouts (e.g. Slovak) don't mangle output. Overlay backspaces are pynput on macOS/Windows and X11, but `ydotool` on Wayland (via `backspace`/`move_cursor` on the platform seam). `ydotool key` uses `<code>:1`/`<code>:0` for down/up - a non-`:0` release leaves the key stuck.

The **global hotkey** listener is pynput's X11 backend on macOS/Windows/Linux-X11, but on Linux-Wayland (where compositors hide global keys from X clients) it reads `/dev/input` directly via **evdev** (`live._build_evdev_hotkey`), which needs the user in the `input` group. Reading is passive (never `grab`), so keystrokes still reach other apps.

## Telemetry / dogfood seam (opt-in)

`dogfood_log.py` + `events.py` + `activity_monitor.py`. Measures how often dictated text gets manually corrected (the vocab-gap signal).

- Off by default at the engine level; the `./dum` launcher turns it on for development.
- Writes only to the gitignored `dogfood/` tree. No network calls.
- Optional VS Code extension (`vscode-dum-telemetry/`) reports post-commit edits from the document model. Observes only, never inserts.
- Details and privacy controls: [`DOGFOOD.md`](DOGFOOD.md).

## Launch & lifecycle

Three modules, each behind a thin OS seam (macOS, Windows, Linux all implemented):

- Single instance (`single_instance.py`): exclusive lock on `~/.dum/dum.lock` (`flock` on macOS/Linux, `msvcrt.locking` on Windows); a second copy exits cleanly. Mic, global double-tap hotkey, and overlay are single-owner (two hotkey listeners can get the process OS-aborted). Lock taken only for live modes, never for `--replay`/bench, so the test gate is unaffected.
- Tray (`tray.py`, `--tray`): `pystray` menu-bar/tray icon (Start/Stop/Quit + listening state), same code on macOS and Windows. Owns the main thread (macOS GUI loop requirement); hotkey listener and recognizer run on background threads. A watcher mirrors the real `app.running` state onto the icon so hotkey and menu never disagree.
- Auto-start (`autostart.py`, `--install-autostart`): one `install`/`uninstall`/`status` interface, three backends, all start-at-login + relaunch-on-crash (honoring a clean Quit):
  - macOS launchd LaunchAgent: `RunAtLoad` + `KeepAlive={SuccessfulExit:false}`
  - Windows Task Scheduler: `LogonTrigger` + `RestartOnFailure`
  - Linux `systemd --user`: `WantedBy=default.target` + `Restart=on-failure`, `After=graphical-session.target`

  All run the platform launcher (`dum` / `dum.ps1`) with `--tray`. Gotcha: a launchd-spawned `python` is a different binary than your terminal's, so macOS re-asks for the three permissions on first launch (inherent to non-bundled login items). Windows/Linux don't re-prompt.
