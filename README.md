# dum dictation — the smartest dictating tool.

**Type with your words. Everywhere. Accurately. Privately.**

Apple Dictation, but better for technical work — accurate **technical vocabulary**,
**capitalization** and **punctuation**, and it runs **fully on-device**. No cloud, no account,
no network. You speak, it types into whatever app is focused.

Built **for vibecoders**: dictating code, commands, and tech prose, it gets `git`, `kubectl`,
`nginx`, `PostgreSQL`, `TanStack Query`, and friends right where a general-purpose dictation
engine hears "get hub" or "engine x". The bar is **"I forgot I was using it"** — no garbage, so
you can think clearly.

## Requirements

- macOS on **Apple Silicon** (M-series).
- **Python 3.12.**

## Install

```sh
gh repo clone eliasmocik/dum-dictation
cd dum-dictation
./setup
```

`./setup` creates a virtualenv, installs pinned dependencies, downloads the speech model and
the on-device correction LLM, and prints the permissions you need to grant. It's the one
command a new contributor runs after cloning.

### macOS permissions (one-time)

Grant these to **your terminal app** in **System Settings > Privacy & Security**, then quit and
reopen the terminal so they take effect:

1. **Microphone** — to capture your voice.
2. **Accessibility** — to type/paste text into the focused app.
3. **Input Monitoring** — to detect the double-tap left Command hotkey.

## Usage

```sh
./dum
```

- **Double-tap the LEFT Command (⌘) key** to start dictating; double-tap again to stop.
- Words appear live as you speak; on a pause the sentence is corrected and finalized.
- **Ctrl+C** in the terminal quits.

Pick a microphone:

```sh
DUM_MIC="MacBook Air" ./dum     # by name (robust to device-index shifts)
./dum --mic 1                     # by index (list: .venv/bin/python live.py --list-devices)
```

## Privacy

Everything runs locally. Optional dogfood logging (off by default in the engine) keeps a
local-only record of what you dictated so mis-transcriptions can be improved — it is never
uploaded and the `dogfood/` directory is gitignored. See [`DOGFOOD.md`](DOGFOOD.md) for exactly
what can be captured and how to control it.

## Contributing

The most valuable contribution is a vocabulary fix. Read [`CONTRIBUTING.md`](CONTRIBUTING.md)
first — the General-vs-Personal rule is the core discipline. See [`ARCHITECTURE.md`](ARCHITECTURE.md)
for how the pipeline fits together and [`DEV-NOTES.md`](DEV-NOTES.md) for the local dev loop.
