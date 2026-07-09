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

> **Tried it? Tell me how it went - good or bad.** One sentence in
> [Discussions](https://github.com/eliasmocik/dum-dictation/discussions) or an
> [issue](https://github.com/eliasmocik/dum-dictation/issues/new) is the single most useful
> thing you can send. I'm building this in the open and I read everything.

## What you need

- A **Mac** with Apple Silicon (M-series) - the full experience, including the homophone LLM
- …or **Windows 10/11** - same dictation + tech-vocab, minus the (Apple-only) homophone LLM. See
  [On Windows](#on-windows) below.
- …or **Linux** (X11) - same as Windows, via `xdotool` + `xclip`/`wl-clipboard`. See
  [On Linux](#on-linux) below.
- Python 3.12

## Install

One command (macOS / Linux):

```sh
curl -fsSL https://raw.githubusercontent.com/eliasmocik/dum-dictation/main/install.sh | bash
```

(On Windows, follow [On Windows](#on-windows) below instead - the one-liner is bash-only.)

That clones the repo into `./dum-dictation` and runs `./setup`, which makes a virtualenv,
installs the deps, downloads the speech model + the on-device correction model, and then tells
you which permissions to grant. That's the whole setup. (On a Mac you'll still have to click
through the permission prompts afterwards - see [Permissions](#permissions-one-time---mac-makes-you-do-this)
right below.)

Prefer not to pipe curl into bash? Same thing in three commands:

```sh
git clone https://github.com/eliasmocik/dum-dictation.git
cd dum-dictation
./setup
```

## Permissions (one time - Mac makes you do this)

Dictation literally can't work without these, so don't skip it. The app you need to grant them to
is **whatever app you ran `./dum` from** - Terminal, iTerm, or the VS Code terminal. (If you run it
in the VS Code terminal, you grant them to **Visual Studio Code**.)

The first time you run `./dum`, macOS will pop these up on its own - just click **Allow** / **Open
System Settings**. If it doesn't, set them by hand: open **System Settings → Privacy & Security**,
then for each of the three, find your terminal app in the list and flip the switch **on**:

1. **Microphone** => so it can hear you
2. **Accessibility** => so it can type into whatever app you're focused on
3. **Input Monitoring** => so it can catch the double-tap-Command hotkey

⚠️ **Then fully quit your terminal app and reopen it.** macOS only applies the new permissions to a
fresh launch - this is the step everyone forgets, and dictation stays silent until you do it.

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

Don't want to babysit a terminal? Add `--tray` and dum lives in your **menu bar** - a
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
Monitoring - this time for the venv's `python` (a login item isn't your terminal). Grant
those three once and log out/in. Running a second copy is refused automatically - one
robot owns the mic and hotkey.

## On Windows

> ⚠️ **Experimental - not yet tested on real Windows hardware.** Treat it as a preview.

Same idea, same tech-vocab smarts - it types into any focused Windows app (VS Code, the
Claude Code box, Chrome, Slack, a WSL terminal). The homophone LLM (`grep`/`grab`,
`git`/`get`) now runs on Windows too via the portable llama.cpp backend (the same model as
Mac), so you get the full phonetic + alias + LLM stack - though that path is still unverified
on real hardware.

In **PowerShell** (Python 3.12 from python.org on your PATH):

```powershell
git clone https://github.com/eliasmocik/dum-dictation.git
cd dum-dictation
.\setup.ps1
.\dum.ps1
```

`.\setup.ps1` makes the venv, installs the deps (the Mac-only wheels are skipped; `pywin32`
is added) and downloads the speech model. The only permission is the **microphone**:
Settings → Privacy & security → Microphone → let desktop apps use it. No Accessibility /
Input-Monitoring step like macOS.

Double-tap the **RIGHT Ctrl** key to start/stop (change it with `.\dum.ps1 --config`).
Want the tray icon and start-at-logon?

```powershell
.\dum.ps1 --tray               # tray icon, no console window
.\dum.ps1 --install-autostart  # start at logon + relaunch on crash (Task Scheduler)
.\dum.ps1 --uninstall-autostart
```

> Running in WSL? Dictation needs the real keyboard, mic and screen - which Windows owns -
> so install and run the **Windows** version above. It still types straight into your WSL
> terminal (and through it, into anything you've SSH'd to). You don't install dum inside WSL
> or on a remote server; it lives on the machine in front of you.

## On Linux

> ⚠️ **Experimental / parked - no maintainer testing this yet.** Code is present but unverified.

Same again, for a Linux **desktop** you sit in front of (not a headless server - there's no mic
or screen to dictate into there). It uses the standard X11 tools:

```sh
sudo apt install xdotool xclip      # (or your distro's equivalent; wl-clipboard for Wayland)
git clone https://github.com/eliasmocik/dum-dictation.git
cd dum-dictation
./setup                              # skips the Apple-only LLM automatically
./dum                                # double-tap RIGHT Ctrl to start/stop
./dum --tray                         # tray icon
./dum --install-autostart            # systemd --user service (start at login + relaunch on crash)
```

Typing is layout-independent via `xdotool type` (so a Slovak/dead-key layout isn't mangled);
clipboard paste uses `xclip` or `wl-clipboard`. If those tools aren't installed it still runs,
just degraded (types via a generic backend, no focus guard).

> **Wayland:** the typing/clipboard tools above are X11. Under a pure Wayland session, run under
> XWayland or install `ydotool` + `wl-clipboard`. This is the least-tested path - X11 is the
> smooth one for now.

## Privacy

Everything stays on your machine. No cloud, nothing uploaded, ever. There's an
optional local-only log (off by default) that remembers what you dictated so the misheard words
can get fixed over time, but it never leaves your computer and `dogfood/` is gitignored. The full
breakdown is in [`docs/DOGFOOD.md`](docs/DOGFOOD.md).

## Want to help?

**Easiest thing (10 seconds):** tell me if it stuck or if you bounced. Did setup work? Did it get
your words right? Did you still have it on a week later? Drop it in
[Discussions](https://github.com/eliasmocik/dum-dictation/discussions) or
[open an issue](https://github.com/eliasmocik/dum-dictation/issues/new). Blunt is fine - "the setup
broke here" or "it kept hearing X as Y" is exactly what I need. This is a side project I'm building
in the open, so real feedback is what moves it.

**Next most useful:** a vocab fix (a word it keeps getting wrong). Ideally read
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md), but the general-vs-personal rule is the whole deal.
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) shows how the pipeline fits together and
[`docs/DEV-NOTES.md`](docs/DEV-NOTES.md) has the dev loop.

## License

MIT (see [`LICENSE`](LICENSE)). Free to use, fork, and build on - no strings attached. If you ship
a vocab fix back, even better, but you never have to.

---

Built by Elias, a student in Dublin, because Apple Dictation kept turning "git push" into "get push".
