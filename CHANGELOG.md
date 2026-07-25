# Changelog

Notable changes to dum dictation. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
versions follow [SemVer](https://semver.org).

## [Unreleased]

### Added
- `./setup` no longer dead-ends when the machine has no supported Python: as a last resort (after an
  existing `.venv`, an already-vendored copy, and any supported system Python) it downloads a pinned,
  sha256-verified CPython (python-build-standalone 20260718, CPython 3.12.13) into a gitignored
  `./.python/`. No sudo, nothing written outside the repo folder, no PATH/shell/registry changes.

### Changed
- Python support widened from "3.12 exactly" to **3.12, 3.13 or 3.14** on macOS/Linux, and **3.12 or
  3.13** on Windows (the Windows-only `pywin32==308` pin publishes no cp314 wheel; every other pin
  does). The full gate + bench ran on all three minors with identical WER and IT-term recall.
- CI runs the unit gate as a matrix over 3.12/3.13/3.14 (`fail-fast: false`) so the widened range
  can't silently regress.

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

[Unreleased]: https://github.com/eliasmocik/dum-dictation/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/eliasmocik/dum-dictation/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/eliasmocik/dum-dictation/releases/tag/v0.1.0
