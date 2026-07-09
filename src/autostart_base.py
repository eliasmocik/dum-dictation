#!/usr/bin/env python3
"""Shared constants for the auto-start backends (autostart_{mac,windows,linux}.py).
The OS dispatcher (install/uninstall/status) is autostart.py."""
from pathlib import Path

# Launch the dum SHELL LAUNCHER (not live.py directly), with --tray appended, so the
# login-started copy is byte-for-byte the same daily driver as a manual launch: same flags
# AND same DUM_* env (which live inside the launcher). Single source of truth.
DEFAULT_ARGS = ["--tray"]

# repo root = parent of this file's dir (src/) - same anchor the engine uses for resources.
REPO_ROOT = Path(__file__).resolve().parent.parent
