#!/usr/bin/env python3
"""User configuration for the dum dictation daily-driver.

Persists the user's chosen microphone and dictation start/stop hotkey to
``~/.dum/config.json`` (the ``~/.dum/`` dir is already used for the VS Code
bridge - reused here). On the very first run (no config file yet) an interactive
CLI wizard lets the user pick; every subsequent run loads silently and launches
straight in (this is a daily driver - no nagging). ``./dum --config`` re-runs the
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
driven with mocked streams in tests - no real TTY needed.
"""
import json
import os
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".dum"
CONFIG_PATH = CONFIG_DIR / "config.json"

# --- Curated, SAFE hotkey catalog ------------------------------------------------
# v1 deliberately offers a small curated list - NOT arbitrary "press any key to
# bind" capture. Each entry maps a stable config token -> how the listener detects
# it. "double" gestures fire on a double-tap of the named pynput modifier key;
# "single" gestures (fn) fire on a single press/hold of that key.
#
# The catalog is platform-aware: macOS offers the Command/fn keys (default: double-tap
# LEFT ⌘ - today's behavior, unchanged); Windows + Linux have no Command key, so they
# offer the Ctrl keys (default: double-tap RIGHT Ctrl - rarely pressed alone, the natural
# analog of the Mac right-Command choice). `_ALL_KEYS` is the full union; `CURATED_KEYS`
# is the subset offered on THIS OS (what the wizard shows).
# `modes` is which dictation modes the key can actually drive, and it is NOT cosmetic:
#   "double" gesture -> toggle ONLY. "Double-tap and then hold" is not a coherent gesture, and
#            offering push-to-dictate on a double-tap key produced a trigger that simply never
#            fired - the original reason this catalog grew a modes field.
#   "single" gesture -> both. A bare press toggles; a bare hold is push-to-dictate.
#
# Only right ⌥ offers push-to-dictate, and it offers ONLY that. Holding ⌘/⌃/⇧ bare would
# swallow every shortcut using them for as long as you talk, so those are toggle-only.
#
# Right ⌥ is push-only because toggle mode is hard-wired to a DOUBLE tap in the listener
# (live.run_double_tap_toggle) - the `gesture` field shapes the label, never the behaviour.
# A "Press right ⌥" entry therefore promised a single press and silently required a double
# one. Making it genuinely single-press would be worse: right ⌥ is AltGr on most non-US
# layouts, so it fires constantly while typing.
#
# THERE IS NO fn ENTRY, deliberately. pynput exposes no Key.function on macOS, so a trigger
# bound to it raised AttributeError while the listener was being built and killed the app on
# the spot. test_config asserts every pynput name here resolves, so it cannot come back.
_ALL_KEYS = [
    {"key": "cmd_l",     "label": "left ⌘ (Command)",  "gesture": "double", "pynput": "cmd_l",     "platforms": ("darwin",),          "modes": ("toggle",)},
    {"key": "cmd_r",     "label": "right ⌘ (Command)", "gesture": "double", "pynput": "cmd_r",     "platforms": ("darwin",),          "modes": ("toggle",)},
    {"key": "ctrl_l",    "label": "left ⌃ (Control)",  "gesture": "double", "pynput": "ctrl_l",    "platforms": ("darwin", "win32", "linux"), "modes": ("toggle",)},
    {"key": "ctrl_r",    "label": "right ⌃ (Control)", "gesture": "double", "pynput": "ctrl_r",    "platforms": ("darwin", "win32", "linux"), "modes": ("toggle",)},
    {"key": "shift_r",   "label": "right ⇧ (Shift)",   "gesture": "double", "pynput": "shift_r",   "platforms": ("darwin", "win32", "linux"), "modes": ("toggle",)},
    {"key": "alt_r",     "label": "right ⌥ (Option)",  "gesture": "single", "pynput": "alt_r",     "platforms": ("darwin", "win32", "linux"), "modes": ("push",)},
]


def _platform_tag():
    """Normalize sys.platform into one of our catalog tags: darwin / win32 / linux."""
    return sys.platform if sys.platform in ("darwin", "win32") else "linux"


def curated_keys(platform=None):
    """The trigger keys offered on this OS (Command/fn on macOS; Ctrl on Windows/Linux)."""
    tag = platform or _platform_tag()
    return [k for k in _ALL_KEYS if tag in k["platforms"]]


CURATED_KEYS = curated_keys()


# --- Triggers: key + mode as ONE choice ------------------------------------------
# Presenting key and mode as two independent menus let the user build a combination that
# cannot work (double-tap + push-to-dictate), and the app silently never triggered. A trigger
# is therefore atomic: pick "Double-tap left ⌘" or "Hold right ⌥", never a key and a mode
# separately. Invalid states stop being something to validate and start being unrepresentable.

def _trigger_label(entry, mode):
    if entry["gesture"] == "double":
        return f"Double-tap {entry['label']}"
    return f"Hold {entry['label']}" if mode == "push" else f"Press {entry['label']}"


def triggers(platform=None):
    """Every valid (key, mode) pairing on this OS, in menu order.

    Returns dicts: key, mode, label, group ("tap" = start/stop, "hold" = push-to-dictate).
    """
    out = []
    for entry in curated_keys(platform):
        for mode in entry.get("modes", ("toggle",)):
            out.append({"key": entry["key"], "mode": mode,
                        "label": _trigger_label(entry, mode),
                        "group": "hold" if mode == "push" else "tap"})
    # HOLDS FIRST: push-to-dictate is the headline trigger, so it leads the menu.
    return sorted(out, key=lambda t: (t["group"] != "hold",))


_DEFAULT_KEY_BY_PLATFORM = {"darwin": "cmd_l", "win32": "ctrl_r", "linux": "ctrl_r"}
DEFAULT_KEY = _DEFAULT_KEY_BY_PLATFORM[_platform_tag()]

CURATED_MODES = [
    {"mode": "toggle", "label": "toggle (tap to start, tap to stop)"},
    {"mode": "push",   "label": "push-to-dictate (hold to talk, release to stop)"},
]
DEFAULT_MODE = "toggle"

# Validate against the FULL union, not just this OS's subset: a config written on one OS
# should round-trip on another (e.g. a synced ~/.dum) instead of silently healing away.
_VALID_KEYS = {k["key"] for k in _ALL_KEYS}
_VALID_MODES = {m["mode"] for m in CURATED_MODES}

# Substrings that identify a Mac's built-in microphone across models. The wizard
# recommends the built-in mic as the daily-driver default for EVERY user: a
# Continuity iPhone mic frequently grabs the macOS *system default* slot, but it's a
# poor dictation base (not always present, added latency, drops on handoff). So we
# locate the built-in mic and recommend THAT, falling back to the system default,
# then the first device.
BUILTIN_MIC_HINTS = ("macbook", "built-in", "built in", "imac", "mac studio", "studio display")


def default_config():
    """The built-in defaults: the platform's default trigger (double-tap left ⌘ on macOS,
    double-tap right Ctrl on Windows/Linux), toggle mode, system-default mic. Used when no
    config file exists and as the fallback for any missing/invalid field."""
    return {"mic": None, "hotkey_key": DEFAULT_KEY, "hotkey_mode": DEFAULT_MODE}


def config_exists(path=CONFIG_PATH):
    return Path(path).exists()


def load_config(path=CONFIG_PATH):
    """Load config from disk, healing missing/invalid fields against the defaults.
    Returns the defaults (does NOT write anything) if the file is absent or
    unreadable - callers decide when to run the wizard."""
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
    # Key and mode are each valid on their own but the PAIR may not be - e.g. alt_r with
    # "toggle" after right ⌥ became push-only. Left alone that config still drives the app
    # (toggle is hard-wired to a double tap) while matching nothing in the menu, so no option
    # appears selected and the user cannot tell what they are on. Heal to a mode the key
    # actually supports.
    supported = key_descriptor(cfg["hotkey_key"]).get("modes", ("toggle",))
    if cfg["hotkey_mode"] not in supported:
        cfg["hotkey_mode"] = supported[0]
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
    """Return the catalog entry for a config token (searching the full union, so a key
    saved on another OS still resolves), or this OS's default entry if unknown."""
    for k in _ALL_KEYS:
        if k["key"] == key_token:
            return k
    for k in _ALL_KEYS:
        if k["key"] == DEFAULT_KEY:
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
        out.write("No input devices found - using system default.\n")
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
            out.write(f"  '{raw}' is not 1-{len(devices)} or Enter - try again.\n")
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
            out.write(f"  '{raw}' is not 1-{len(CURATED_MODES)} or Enter - try again.\n")
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
            out.write(f"  '{raw}' is not 1-{len(CURATED_KEYS)} or Enter - try again.\n")
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
