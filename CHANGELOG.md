# Changelog

Notable changes to dum dictation. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
versions follow [SemVer](https://semver.org).

## [Unreleased]

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

[Unreleased]: https://github.com/eliasmocik/dum-dictation/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/eliasmocik/dum-dictation/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/eliasmocik/dum-dictation/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/eliasmocik/dum-dictation/releases/tag/v0.1.0
