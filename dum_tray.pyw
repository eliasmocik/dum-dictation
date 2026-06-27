#!/usr/bin/env pythonw
"""Windowless tray launcher for Windows (the Desktop shortcut points the venv pythonw here).

pythonw.exe has NO console, so sys.stdout/sys.stderr are None and live.py's log() writes crash
under it (which is why double-clicking a pythonw shortcut that runs live.py directly silently does
nothing). We redirect stdout/stderr to dogfood/tray.log first, set the daily-driver argv, then run
live.main(). The log file also turns a silent failure into something debuggable.

Args baked in: --double-cmd (the double-tap hotkey from ~/.dum/config.json) + --tray (tray icon,
no console) + NO --overlay (so it pastes-at-commit, reliable over remote desktop) + --llm (degrades
gracefully if llama.cpp can't load). The mic comes from the saved config.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, os.path.join(HERE, "src"))

logdir = os.path.join(HERE, "dogfood")
os.makedirs(logdir, exist_ok=True)
_log = open(os.path.join(logdir, "tray.log"), "a", buffering=1, encoding="utf-8")
sys.stdout = _log
sys.stderr = _log
# pythonw gives a None stdin too; live.main() calls sys.stdin.isatty(), so hand it a real
# (non-tty) handle -> isatty() is False -> the interactive config wizard is correctly skipped.
sys.stdin = open(os.devnull, "r")

sys.argv = ["live.py", "--double-cmd", "--tray", "--llm"]

import live
live.main()
