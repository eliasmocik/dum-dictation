#!/usr/bin/env python3
"""User configuration for the dum dictation daily-driver.

Persists the user's chosen microphone and dictation start/stop hotkey to
``~/.dum/config.json`` (the ``~/.dum/`` dir is already used for the VS Code
bridge — reused here). On the very first run (no config file yet) an interactive
CLI wizard lets the user pick; every subsequent run loads silently and launches
straight in (this is a daily driver — no nagging). ``./dum --config`` re-runs the
wizard and overwrites the saved config.

Scope (v1): ONLY the dictation start/stop hotkey (key + toggle/push mode) and the
mic are configurable. The ⌥ "flag a problem" gesture stays hardcoded.

Schema (config.json):
    {
      "mic": <str|int|null>,        # device name substring or index; null = system default
      "hotkey_key": <str>,          # a key from CURATED_KEYS (e.g. "cmd_l")
      "hotkey_mode": "toggle"|"push"
    }

Everything that touches stdin/stdout in the wizard is parameterised so it can be
driven with mocked streams in tests — no real TTY needed.
"""
import json
import os
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".dum"
CONFIG_PATH = CONFIG_DIR / "config.json"

# --- Curated, SAFE hotkey catalog ------------------------------------------------
# v1 deliberately offers a small curated list — NOT arbitrary "press any key to
# bind" capture. Each entry maps a stable config token -> how the listener detects
# it. "double" gestures fire on a double-tap of the named pynput modifier key;
# "single" gestures (fn) fire on a single press/hold of that key. The recommended
# default reproduces today's hardcoded behavior exactly: double-tap LEFT ⌘.
CURATED_KEYS = [
    {"key": "cmd_l",  "label": "double-tap left ⌘ (Command)",  "gesture": "double", "pynput": "cmd_l"},
    {"key": "cmd_r",  "label": "double-tap right ⌘ (Command)", "gesture": "double", "pynput": "cmd_r"},
    {"key": "fn",     "label": "fn key",                            "gesture": "single", "pynput": "function"},
]
DEFAULT_KEY = "cmd_l"

CURATED_MODES = [
    {"mode": "toggle", "label": "toggle (tap to start, tap to stop)"},
    {"mode": "push",   "label": "push-to-dictate (hold to talk, release to stop)"},
]
DEFAULT_MODE = "toggle"

_VALID_KEYS = {k["key"] for k in CURATED_KEYS}
_VALID_MODES = {m["mode"] for m in CURATED_MODES}

# Substrings that identify a Mac's built-in microphone across models. The wizard
# recommends the built-in mic as the daily-driver default for EVERY user: a
# Continuity iPhone mic frequently grabs the macOS *system default* slot, but it's a
# poor dictation base (not always present, added latency, drops on handoff). So we
# locate the built-in mic and recommend THAT, falling back to the system default,
# then the first device.
BUILTIN_MIC_HINTS = ("macbook", "built-in", "built in", "imac", "mac studio", "studio display")


def default_config():
    """The built-in defaults = today's behavior (double-tap left ⌘ toggle, system
    default mic). Used when no config file exists and as the fallback for any
    missing/invalid field."""
    return {"mic": None, "hotkey_key": DEFAULT_KEY, "hotkey_mode": DEFAULT_MODE}


def config_exists(path=CONFIG_PATH):
    return Path(path).exists()


def load_config(path=CONFIG_PATH):
    """Load config from disk, healing missing/invalid fields against the defaults.
    Returns the defaults (does NOT write anything) if the file is absent or
    unreadable — callers decide when to run the wizard."""
    base = default_config()
    p = Path(path)
    if not p.exists():
        return base
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return base
    if not isinstance(data, dict):
        return base
    cfg = dict(base)
    if "mic" in data and (data["mic"] is None or isinstance(data["mic"], (str, int))):
        cfg["mic"] = data["mic"]
    if data.get("hotkey_key") in _VALID_KEYS:
        cfg["hotkey_key"] = data["hotkey_key"]
    if data.get("hotkey_mode") in _VALID_MODES:
        cfg["hotkey_mode"] = data["hotkey_mode"]
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    """Atomically persist the config (only the three known fields)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "mic": cfg.get("mic"),
        "hotkey_key": cfg.get("hotkey_key", DEFAULT_KEY),
        "hotkey_mode": cfg.get("hotkey_mode", DEFAULT_MODE),
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(out, indent=2) + "\n")
    os.replace(tmp, p)
    return out


def resolve_mic_spec(flag_mic, env_mic, cfg_mic, builtin):
    """Mic precedence (single source of truth, shared by live.py main() and tests):
        explicit --mic (flag_mic) > DUM_MIC/DICTATE_MIC (env_mic) > saved config (cfg_mic) > builtin.
    `flag_mic` is None when --mic absent; `env_mic` is None/"" when unset; `cfg_mic` is
    None/"" when the config has no mic. Returns the chosen spec (str/int/None)."""
    if flag_mic is not None:
        return flag_mic
    if env_mic:
        return env_mic
    if cfg_mic not in (None, ""):
        return cfg_mic
    return builtin


def recommended_mic_index(devices, default_idx):
    """1-based position of the device to mark (recommended) in the wizard, or None if
    `devices` is empty. Prefers the Mac built-in mic (best dictation base for every
    user) over the macOS system default, then falls back to the system default, then
    the first device. `devices` is [(index, name), ...]."""
    if not devices:
        return None
    for pos, (idx, name) in enumerate(devices, start=1):
        if any(h in name.lower() for h in BUILTIN_MIC_HINTS):
            return pos
    if default_idx is not None:
        for pos, (idx, _name) in enumerate(devices, start=1):
            if idx == default_idx:
                return pos
    return 1


def key_descriptor(key_token):
    """Return the CURATED_KEYS entry for a config token (or the default's)."""
    for k in CURATED_KEYS:
        if k["key"] == key_token:
            return k
    return CURATED_KEYS[0]


# --- Device discovery ------------------------------------------------------------

def list_input_devices():
    """Return [(index, name), ...] for every input-capable device, plus the index of
    the current system default input device (or None). Imports sounddevice lazily so
    importing this module stays cheap/headless-safe."""
    import sounddevice as sd
    devices = []
    for i, dv in enumerate(sd.query_devices()):
        if dv.get("max_input_channels", 0) > 0:
            devices.append((i, dv["name"]))
    default_idx = None
    try:
        d = sd.default.device
        # sd.default.device is (input, output); -1 means unset
        if isinstance(d, (list, tuple)) and len(d) >= 1 and d[0] is not None and d[0] >= 0:
            default_idx = d[0]
    except Exception:
        default_idx = None
    return devices, default_idx


# --- Interactive wizard ----------------------------------------------------------
# The pickers are pure functions of (devices/options, default, input_fn, out) so
# tests drive them with mocked stdin (input_fn) and capture prompts via `out`.

def _prompt(input_fn, out, text):
    out.write(text)
    out.flush()
    return input_fn()


def pick_mic(devices, default_idx, input_fn, out):
    """Numbered mic picker. Marks the system default "(recommended)". User types a
    number (1-based) to choose, or presses Enter to accept the recommended default.
    Returns the chosen device name (str) or None for "use system default".
    `devices` is [(index, name), ...]."""
    if not devices:
        out.write("No input devices found — using system default.\n")
        return None
    rec_pos = recommended_mic_index(devices, default_idx)  # built-in mic preferred
    out.write("\nChoose your microphone:\n")
    for pos, (idx, name) in enumerate(devices, start=1):
        tag = "  (recommended)" if pos == rec_pos else ""
        out.write(f"  for mic {pos} press {pos}: {name}{tag}\n")
    hint = (f"Press a number 1-{len(devices)} to choose, or Enter for "
            f"the recommended (mic {rec_pos}): ")
    while True:
        raw = _prompt(input_fn, out, hint).strip()
        if raw == "":
            chosen_pos = rec_pos
        elif raw.isdigit() and 1 <= int(raw) <= len(devices):
            chosen_pos = int(raw)
        else:
            out.write(f"  '{raw}' is not 1-{len(devices)} or Enter — try again.\n")
            continue
        idx, name = devices[chosen_pos - 1]
        # Persist by NAME (robust to index shifts), matching DUM_MIC's name path.
        return name


def pick_mode(input_fn, out):
    """Pick toggle (recommended) vs push. Returns the mode token."""
    out.write("\nHow should the dictation hotkey behave?\n")
    rec_pos = None
    for pos, m in enumerate(CURATED_MODES, start=1):
        tag = "  (recommended)" if m["mode"] == DEFAULT_MODE else ""
        if m["mode"] == DEFAULT_MODE:
            rec_pos = pos
        out.write(f"  press {pos}: {m['label']}{tag}\n")
    rec_pos = rec_pos or 1
    hint = f"Press 1-{len(CURATED_MODES)} or Enter for the recommended: "
    while True:
        raw = _prompt(input_fn, out, hint).strip()
        if raw == "":
            pos = rec_pos
        elif raw.isdigit() and 1 <= int(raw) <= len(CURATED_MODES):
            pos = int(raw)
        else:
            out.write(f"  '{raw}' is not 1-{len(CURATED_MODES)} or Enter — try again.\n")
            continue
        return CURATED_MODES[pos - 1]["mode"]


def pick_key(input_fn, out):
    """Pick the trigger key/chord from the curated list. Returns the key token."""
    out.write("\nWhich key triggers dictation?\n")
    rec_pos = None
    for pos, k in enumerate(CURATED_KEYS, start=1):
        tag = "  (recommended)" if k["key"] == DEFAULT_KEY else ""
        if k["key"] == DEFAULT_KEY:
            rec_pos = pos
        out.write(f"  press {pos}: {k['label']}{tag}\n")
    rec_pos = rec_pos or 1
    hint = f"Press 1-{len(CURATED_KEYS)} or Enter for the recommended: "
    while True:
        raw = _prompt(input_fn, out, hint).strip()
        if raw == "":
            pos = rec_pos
        elif raw.isdigit() and 1 <= int(raw) <= len(CURATED_KEYS):
            pos = int(raw)
        else:
            out.write(f"  '{raw}' is not 1-{len(CURATED_KEYS)} or Enter — try again.\n")
            continue
        return CURATED_KEYS[pos - 1]["key"]


def run_wizard(devices, default_idx, input_fn=None, out=None, path=CONFIG_PATH, save=True):
    """Run the full first-run wizard and (by default) persist the result.
    Returns the chosen config dict. Pure w.r.t. I/O via input_fn/out so it's testable
    with mocked stdin."""
    input_fn = input_fn or (lambda: input())
    out = out or sys.stdout
    out.write("\n=== dum first-run setup ===\n")
    out.write("(re-run any time with: ./dum --config)\n")
    mic = pick_mic(devices, default_idx, input_fn, out)
    mode = pick_mode(input_fn, out)
    key = pick_key(input_fn, out)
    cfg = {"mic": mic, "hotkey_key": key, "hotkey_mode": mode}
    if save:
        save_config(cfg, path)
        out.write(f"\nSaved to {path}. Launching dum...\n")
    desc = key_descriptor(key)
    out.write(f"  mic={mic or 'system default'}  trigger={desc['label']}  mode={mode}\n")
    out.write("  report a bad transcription: double-tap left ⌥ (Option)\n")
    return cfg
