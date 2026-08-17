# dum dictation

[![tests](https://github.com/eliasmocik/dum-dictation/actions/workflows/tests.yml/badge.svg)](https://github.com/eliasmocik/dum-dictation/actions/workflows/tests.yml)

Dum dictation is a local, open-source dictation tool primarily for Mac. It gets technical
vocabulary right and offers a responsive word-by-word feel directly at your cursor.

https://github.com/user-attachments/assets/20cf0b37-7b8b-4586-abd8-e8bac6663766

## Installation for Mac

**macOS** (Apple Silicon, M-series)

Download the app from the DMG, following **[→ Download dum for Mac](https://github.com/eliasmocik/dum-dictation/releases/latest)**

## Other platforms

<details>
<summary><b>Windows 10/11</b> - beta</summary>

In **PowerShell** (Python 3.12 or 3.13 on your PATH - 3.14 isn't supported on Windows yet; with no
Python at all, `setup.ps1` fetches its own):

```powershell
git clone https://github.com/eliasmocik/dum-dictation.git
cd dum-dictation
.\setup.ps1
.\dum.ps1
```

- Double-tap **RIGHT Ctrl** to start/stop (change it: `.\dum.ps1 --config`).
- Only permission: **microphone** (Settings => Privacy & security => Microphone).
- Tray + logon: `.\dum.ps1 --tray`, `.\dum.ps1 --install-autostart`.

</details>

<details>
<summary><b>Linux</b> (X11) - beta</summary>

> Looking for a Linux contributor. Reach out on
> [Discussions](https://github.com/eliasmocik/dum-dictation/discussions) or [@eliasmocik](https://github.com/eliasmocik).

```sh
sudo apt install xdotool xclip      # or wl-clipboard on Wayland
git clone https://github.com/eliasmocik/dum-dictation.git
cd dum-dictation
./setup                              # skips the Apple-only LLM
./dum                                # double-tap RIGHT Ctrl to start/stop
./dum --tray
./dum --install-autostart            # systemd --user service
```

</details>

## Privacy

Data is only saved locally. The tool keeps an optional log, off by default, that remembers
dictations so misheard words get fixed over time. Details: [`docs/DOGFOOD.md`](docs/DOGFOOD.md).

## Want to help?

- Feedback or bugs: [Discussions](https://github.com/eliasmocik/dum-dictation/discussions) or [open an issue](https://github.com/eliasmocik/dum-dictation/issues/new)
- Vocab fix: [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)
- How it works: [`docs/how-it-works.md`](docs/how-it-works.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/DEV-NOTES.md`](docs/DEV-NOTES.md)

## License

MIT (see [`LICENSE`](LICENSE)).
