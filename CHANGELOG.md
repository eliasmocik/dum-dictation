# Changelog

Notable changes to dum dictation. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
versions follow [SemVer](https://semver.org).

## [Unreleased]

## [0.1.0] - 2026-07-13

First public release. Local, live dictation that gets your tech vocab right.

### Added
- Live dictation that types into whatever app you're in - words appear as you speak, a pause
  locks the sentence in. Double-tap a modifier to start/stop.
- Tech-vocab correction: `git`, `kubectl`, `nginx`, `PostgreSQL`, `TanStack Query` and friends
  land right where normal dictation hears "get hub" or "engine x". Phonetic + alias layers plus
  an optional on-device LLM stage for homophones.
- Everything runs on your machine. Optional local-only history (off by default) that learns your
  misheard words over time.
- Menu-bar / tray mode and start-at-login autostart.
- **macOS (Apple Silicon)** - flagship, fully supported, one-line install.
- **Windows 10/11** - supported (beta), built and tested by a contributor.
- **Linux (X11)** - experimental scaffold; contributors wanted.
- Offline test gate (`scripts/test`): unit suite over the deterministic pipeline + a bench that
  replays a golden corpus through the real loop and scores WER / term recall against a baseline.

[Unreleased]: https://github.com/eliasmocik/dum-dictation/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eliasmocik/dum-dictation/releases/tag/v0.1.0
