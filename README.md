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

## Permissions (one time — Mac makes you do this)

Dictation literally can't work without these, so don't skip it. The app you need to grant them to
is **whatever app you ran `./dum` from** — Terminal, iTerm, or the VS Code terminal. (If you run it
in the VS Code terminal, you grant them to **Visual Studio Code**.)

The first time you run `./dum`, macOS will pop these up on its own — just click **Allow** / **Open
System Settings**. If it doesn't, set them by hand: open **System Settings → Privacy & Security**,
then for each of the three, find your terminal app in the list and flip the switch **on**:

1. **Microphone** => so it can hear you
2. **Accessibility** => so it can type into whatever app you're focused on
3. **Input Monitoring** => so it can catch the double-tap-Command hotkey

⚠️ **Then fully quit your terminal app and reopen it.** macOS only applies the new permissions to a
fresh launch — this is the step everyone forgets, and dictation stays silent until you do it.

<!-- Optional but recommended for non-technical friends: add 3 small screenshots of the toggles.
Drop them in docs/ as docs/perm-mic.png, docs/perm-accessibility.png, docs/perm-input.png and
reference them here. The grant step is where most people get stuck. -->

Stuck? The most common cause of "it runs but types nothing" is forgetting to **quit and reopen**
the terminal after granting Accessibility.

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

### Run it like a real app (menu bar + auto-start)

Don't want to babysit a terminal? Add `--tray` and dum lives in your **menu bar** — a
little dot (green = listening, grey = idle) with **Start/Stop** and **Quit**. The hotkey
still works the same.

```sh
./dum --tray
```

To have it **start by itself at login** (and quietly relaunch if it ever crashes):

```sh
./dum --install-autostart      # set it up   (also: --autostart-status)
./dum --uninstall-autostart    # undo it
```

After the first auto-start, macOS re-asks for Microphone / Accessibility / Input
Monitoring — this time for the venv's `python` (a login item isn't your terminal). Grant
those three once and log out/in. Running a second copy is refused automatically — one
robot owns the mic and hotkey.

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
