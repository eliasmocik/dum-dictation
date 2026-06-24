# dum dictation

**The smartest dictating tool.** Type with your words. Everywhere. Accurately. Privately.

An open, local alternative to Wispr Flow.

![dum dictation demo](docs/demo.gif)

Ok real talk: this is Apple Dictation, except it doesn't butcher your tech words. It gets `git`,
`kubectl`, `nginx`, `PostgreSQL`, `TanStack Query` and friends right, where normal dictation hears
"get hub" or "engine x". It runs fully on your machine. No cloud, no account, no network. You talk,
it precisely types into whatever app you're in.

Built for vibecoders. The bar I'm going for is **"I forgot I was using it"** so you
can think clearly. If you just want to talk and have the right text show up, this is
for you.

## What you need

- A Mac with Apple Silicon (M-series)
- Python 3.12

## Install

Three commands:

```sh
git clone https://github.com/eliasmocik/dum-dictation.git
cd dum-dictation
./setup
```

`./setup` makes a virtualenv, installs the deps, downloads the speech model + the on-device
correction model, and then tells you which permissions to grant. That's the whole setup.

## Permissions (one time, Mac makes you do this)

Dictation literally can't work without these. Open **System Settings => Privacy & Security** and
grant all three to your terminal app:

1. **Microphone** => so it can hear you
2. **Accessibility** => so it can type into the app you're focused on
3. **Input Monitoring** => so it can catch the double-tap hotkey

Then quit your terminal and open it again so they kick in.

## Using it

```sh
./dum
```

Double-tap the **LEFT Command (⌘)** key to start talking, double-tap again to stop. Words show up
live as you speak, and when you pause it cleans up the sentence and locks it in. Ctrl+C to quit.

Need a different mic?

```sh
DUM_MIC="MacBook Air" ./dum     # by name (survives device-index shuffles)
./dum --mic 1                    # by index (list them: .venv/bin/python src/live.py --list-devices)
```

## Privacy

Everything stays on your machine. No cloud, nothing uploaded, ever. There's an
optional local-only log (off by default) that remembers what you dictated so the misheard words
can get fixed over time, but it never leaves your computer and `dogfood/` is gitignored. The full
breakdown is in [`docs/DOGFOOD.md`](docs/DOGFOOD.md).

## Want to help?

The most useful thing you can send me is a vocab fix (a word it keeps getting wrong). Ideally read
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md), but the general-vs-personal rule is the whole deal.
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) shows how the pipeline fits together and
[`docs/DEV-NOTES.md`](docs/DEV-NOTES.md) has the dev loop.

## License

MIT (see [`LICENSE`](LICENSE)). Free to use, fork, and build on — no strings attached. If you ship
a vocab fix back, even better, but you never have to.

---

Built by Elias, a student in Dublin, because Apple Dictation kept turning "git push" into "get push".
