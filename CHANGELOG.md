# Changelog

Notable changes to dum dictation. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
versions follow [SemVer](https://semver.org).

## [Unreleased]

## [0.2.1] - 2026-08-17

Everything here was found by wiping a Mac to a genuine first-run state and installing the
shipped DMG. None of it was visible on a machine that had run dum before, which is exactly
why it shipped.

### Fixed
- **The permission menu items were a dead end on the machines that needed them most.** They
  only deep-linked to System Settings - but macOS lists an app under Microphone /
  Accessibility / Input Monitoring only once the app has *asked* for that permission. Never
  having asked, dum was in none of those lists, so the menu opened a pane with nothing to
  toggle. They now ASK when a permission has never been decided (the prompt is what creates
  the row) and open Settings only once it has, since a denial can only be reversed there.
- **The microphone request could never have worked.** `objc.loadBundle` brings a framework's
  classes but none of its BridgeSupport metadata, so pyobjc had no signature for the
  completion-handler block and every call raised "Argument 3 is a block, but no signature
  available". The caller caught it and quietly opened System Settings - the same dead end,
  one layer down. The block signature is now declared explicitly, with opening a real input
  stream as a fallback, since the prompt is a side effect of access.
- **No Input Monitoring item existed at all**, though it is one of the three grants the app
  cannot work without - and the one whose absence is least obvious, because pynput's event
  tap is born dead without it and the hotkey then does nothing at all, silently.
- **The app crash-looped on a cold machine** (EXC_BREAKPOINT / SIGTRAP). pynput asks Carbon
  for the keyboard layout from its listener thread; answering can require rebuilding the
  input-source list, which asserts it is on the main queue. The layout is now resolved once
  on the main thread and served from cache, so that call never happens off-main again.
  Reproducing it in isolation failed three ways, so the call was removed rather than made
  conditionally safe.

### Added
- **Auto-start is on by default for a downloaded app** - it now comes back after a reboot
  without anyone needing to know a login item exists. Exactly once, latched in the config:
  switching it off in the menu sticks, because "no login item" and "never offered" would
  otherwise look identical and every launch would put it back. Never applied to a git
  checkout - a LaunchAgent pointing into a working copy breaks the moment it moves.
- Each permission menu item shows a tick with its live grant state, so the menu answers
  "why isn't it working" without leaving it.
- The correction model now downloads **without asking**. A modal used to interrupt first
  launch to request permission to fetch it; the question was worse than useless, since it
  arrived in front of someone who had just installed the app and had not yet dictated a
  word, about a component they had no way to evaluate - and answering "no" only produced a
  worse product, because that model is the layer that fixes git/get and grep/grab. It has
  always downloaded on a daemon thread with dictation fully usable throughout, so there was
  never anything to consent to except bandwidth. Metered connections can still refuse it
  with `DUM_FETCH_LLM=0`, without a dialog in everyone else's way.
- `dum --permissions [--request]`, a diagnostic the signed bundle can run. The request path
  cannot be tested from a plain `python`: macOS SIGKILLs any process touching the microphone
  without `NSMicrophoneUsageDescription`, and an interpreter has no Info.plist.

## [0.2.0] - 2026-08-17

### Added
- **A downloadable macOS app.** `dum.dmg` (45 MB, Apple Silicon, macOS 11+) - drag to Applications
  and open. No terminal, no Python, no `./setup`. The bundle ships the engine and the vocabulary;
  the speech model (~490 MB) downloads on first launch with a progress window, resumably.
  Signed with a project self-signed certificate, so macOS offers **Open Anyway** once (rather than
  the flat refusal an unsigned app gets) and - because the signing identity is stable across builds
  - the Microphone / Accessibility / Input Monitoring grants survive updates instead of being
  revoked on every new version.
- **Settings in the menu bar.** Microphone, trigger (push-to-talk or double-tap toggle), the two
  permission panes, autostart at login and Quit. The first-run wizard needs a TTY that a bundled
  app does not have, so without this a downloaded copy was stuck on defaults permanently. Changes
  apply live: the hotkey listener is rebuilt in place rather than relaunching the app.
- `packs/terms.txt` and the vocabulary packs now ship inside the bundle, so a downloaded copy gets
  the same IT-term recall as a checkout.

### Changed
- Repository layout: build, install and packaging files moved into `packaging/` and `scripts/`
  (`dum.spec`, `requirements-build.txt`, `dum_tray.pyw`, `install.sh`, `make-shortcut.ps1`), docs
  into `docs/`, and `terms.txt` into `packs/`. The root now holds only what you type (`dum`,
  `setup`, and their `.ps1` mirrors) or what GitHub renders. **`install.sh` moved**, so the old
  one-line install URL is now
  `.../main/scripts/install.sh`; the DMG is the recommended path on macOS either way.
- `./setup` no longer dead-ends when the machine has no supported Python: as a last resort (after an
  existing `.venv`, an already-vendored copy, and any supported system Python) it downloads a pinned,
  sha256-verified CPython (python-build-standalone 20260718, CPython 3.12.13) into a gitignored
  `./.python/`. No sudo, nothing written outside the repo folder, no PATH/shell/registry changes.
- `./setup` now vendors its fallback CPython with **uv** instead of a hand-rolled downloader. Same
  python-build-standalone builds as before, but the per-triple sha256 table, the URL/triple/variant
  matrix, the musl guard and the tar extraction are gone - Astral maintains that matrix now, so a
  version bump is a one-line change instead of re-verifying four checksums. uv is reused from `PATH`
  when present, otherwise fetched into the gitignored `./.uv/`. The "nothing outside the repo folder"
  guarantee is preserved and tested: `UV_PYTHON_INSTALL_DIR` + `UV_PYTHON_BIN_DIR` keep the
  interpreter and its shims in `./.python/`, and `UV_UNMANAGED_INSTALL` stops uv's installer editing
  PATH or shell rc files. Note this is not a size win (`setup` is ~10 lines longer) - the win is that
  the part needing hand-maintenance on every bump, a 4-triple sha256 table, is now a version string.
- Python support widened from "3.12 exactly" to **3.12, 3.13 or 3.14** on macOS/Linux, and **3.12 or
  3.13** on Windows (the Windows-only `pywin32==308` pin publishes no cp314 wheel; every other pin
  does). The full gate + bench ran on all three minors with identical WER and IT-term recall.
- CI runs the unit gate as a matrix over 3.12/3.13/3.14 (`fail-fast: false`) so the widened range
  can't silently regress.

### Fixed
- The `fn` hotkey option could never have worked. pynput exposes no `Key.function` on macOS, so
  choosing it raised `AttributeError` while building the listener and killed the app on the spot.
  Removed from the catalog; a test now fails on any entry the backend cannot resolve.
- Key and mode were offered independently, so "double-tap + push-to-dictate" was selectable and
  simply never fired, and a saved config could hold a pair the catalog no longer offers. They are
  one atomic trigger now, and `load_config` heals stale pairs.
- "Press right option" was a lie in the menu: toggle mode is hard-wired to a *double* tap in the
  listener, and the label was the only thing that ever changed. Right option is push-to-talk only.
- Auto-start silently did nothing from a bundled app: the LaunchAgent pointed at `<repo>/dum` and
  refused without `<repo>/.venv`, neither of which exists next to a downloaded app. It launches the
  app itself now, logs outside the bundle (writing inside would invalidate the code signature, and
  with it the user's permission grants), and registers under the app's name in Login Items.
- The menu-bar glyph looked upscaled on Retina displays. pystray sizes artwork in *pixels* and hands
  it to AppKit, which draws in *points* - a 2x stretch no source resolution can fix. The icon is now
  rasterised at device resolution and declared at logical size.
- `./setup` pre-pulled the **wrong** correction LLM. The default backend moved to llama.cpp/GGUF on
  every OS, but setup still fetched the MLX weights - so a fresh install downloaded ~680MB that
  nothing ever loads, while the ~770MB GGUF the loader actually wants arrived silently on the
  consumer thread during the first dictation (the first hotkey press looked like a hang). Setup now
  asks `llm_backend.default_model_ref()` what the default backend will load, so the pre-pull and the
  loader share one source of truth and cannot drift again; `tests/test_llm_backend.py` guards it.
  The pre-pull also now runs on every OS, matching the backend it fetches for.
- A failed LLM pre-pull no longer aborts `./setup`. The step printed "or skip for now: it also
  downloads on first --llm dictation" and then ran `exit 1` on the very next line, so one flaky
  HuggingFace connection threw away an otherwise finished install and the advice was unreachable.
  It now warns and continues - the model is genuinely optional, since `_build_llm()` already
  degrades to the phonetic + alias layers when it can't load.
- `./setup` invoked `.venv/bin/hf`, a console script whose shebang bakes an absolute interpreter
  path at install time, so renaming or moving the checkout broke the LLM pre-pull with
  "bad interpreter". Calls `snapshot_download()` through the venv Python instead.
- Both `curl` downloads now bound connect time and stall (`--connect-timeout 20 --retry 2
  --speed-limit 1024 --speed-time 60`); curl's 300s default made setup look hung for five
  minutes behind a captive portal.

### Security
- pillow 12.2.0 -> 12.3.0, clearing 13 advisories (10 high, 3 moderate). Pillow is only used to
  render the tray icon, so exposure was low, but the bump is free.

## [0.1.1] - 2026-07-13

Maintenance: make the project easier to trust and contribute to. No behaviour change.

### Added
- Continuous integration: a `tests` workflow runs the deterministic unit suite on every push and
  PR, via a new `scripts/test --unit` mode that skips the bench so a clean clone with no models or
  voice corpus goes green.
- Issue forms (bug / vocab-miss / feature) and a pull-request template under `.github/`.
- This changelog.

## [0.1.0] - 2026-07-10

First public release. Local, live dictation that gets your tech vocab right.

### Added
- Live dictation that types into whatever app you're in - words appear as you speak, a pause
  locks the sentence in. Double-tap a modifier to start/stop.
- Tech-vocab correction: `git`, `kubectl`, `nginx`, `PostgreSQL`, `TanStack Query` and friends
  land right where normal dictation hears "get hub" or "engine x". Phonetic + alias layers plus
  an on-device homophone LLM (MLX on Apple Silicon, portable llama.cpp elsewhere).
- Everything runs on your machine. Optional local-only history (off by default) that learns your
  misheard words over time.
- Menu-bar / tray mode and start-at-login autostart.
- **macOS (Apple Silicon)** - flagship, fully supported, one-line install.
- **Windows 10/11** - supported (beta), built and tested by a contributor.
- **Linux (X11)** - experimental scaffold; contributors wanted.
- Offline test gate (`scripts/test`): unit suite over the deterministic pipeline + a bench that
  replays a golden corpus through the real loop and scores WER / term recall against a baseline.

[Unreleased]: https://github.com/eliasmocik/dum-dictation/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/eliasmocik/dum-dictation/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/eliasmocik/dum-dictation/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/eliasmocik/dum-dictation/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/eliasmocik/dum-dictation/releases/tag/v0.1.0
