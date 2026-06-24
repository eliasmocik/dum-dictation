# dum dictation

**The smartest dictating tool.** Type with your words. Everywhere. Accurately. Privately.

An open, local alternative to Wispr Flow.

<!-- DEMO GIF GOES HERE => record yourself dictating "git push, then run kubectl on nginx and check the PostgreSQL logs on localhost", show every term land right, offline. Save as docs/demo.gif and uncomment the line below. This is the #1 thing that makes people actually try it.
![dum dictation demo](docs/demo.gif)
-->

Ok real talk: this is Apple Dictation, except it doesn't butcher your tech words. It gets `git`,
`kubectl`, `nginx`, `PostgreSQL`, `TanStack Query` and friends right, where normal dictation hears
"get hub" or "engine x". It runs fully on your machine. No cloud, no account, no network. You talk,
it types into whatever app you're in.

Built for vibecoders. The bar I'm going for is **"I forgot I was using it."** No garbage, so you
can think clearly.

> This repo looks unpolished on purpose. I'd rather spend the time on the thing that matters => the
> tool working out of the box. If you're a lazy IT guy who just wants to talk and have the right
> text show up, this is for you.

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
./dum --mic 1                    # by index (list them: .venv/bin/python live.py --list-devices)
```

## Privacy

Everything stays on your machine. For real => no cloud, nothing uploaded, ever. There's an
optional local-only log (off by default) that remembers what you dictated so the misheard words
can get fixed over time, but it never leaves your computer and `dogfood/` is gitignored. The full
breakdown of what can be captured and how to turn it off is in [`DOGFOOD.md`](DOGFOOD.md).

## Want to help?

The most useful thing you can send me is a vocab fix => a word it keeps getting wrong. Read
[`CONTRIBUTING.md`](CONTRIBUTING.md) first, the general-vs-personal rule is the whole discipline.
[`ARCHITECTURE.md`](ARCHITECTURE.md) shows how the pipeline fits together and
[`DEV-NOTES.md`](DEV-NOTES.md) has the dev loop.

## License

GPLv3 (see [`LICENSE`](LICENSE)). Contributions need the CLA (see [`CLA.md`](CLA.md)) so the tool
can stay free and open while I keep building it.

---

Built by Elias, a student in Dublin, because Apple Dictation kept turning "git push" into "get push".
