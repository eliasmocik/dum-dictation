#!/usr/bin/env python3
"""Auto-start dispatcher — install()/uninstall()/status(), selected by sys.platform.

The "robust launch" so the robot starts at login and self-heals on crash (paired with the
tray icon + single-instance guard). Each OS backend lives one-per-file
(autostart_{mac,windows,linux}.py, one owner each); this module only dispatches and re-exports
the pure builders + path helpers (tests reference autostart.build_plist etc.). All backends
launch the SAME dum/dum.ps1 launcher + --tray, so the login copy equals a manual launch.

macOS permissions caveat: a launchd-spawned python is a different executable than your
terminal, so Mic/Accessibility/Input-Monitoring grants don't carry — macOS re-asks once.
"""
import sys

from autostart_base import DEFAULT_ARGS, REPO_ROOT
from autostart_mac import (
    LABEL, agent_plist_path, build_plist, build_plist_dict,
    _mac_install, _mac_uninstall, _mac_status)
from autostart_windows import (
    TASK_NAME, windows_launcher_command, build_task_xml,
    _win_install, _win_uninstall, _win_status)
from autostart_linux import (
    SERVICE_NAME, service_unit_path, build_unit,
    _linux_install, _linux_uninstall, _linux_status)


def install(args=None):
    if sys.platform == "darwin":
        return _mac_install(args)
    if sys.platform == "win32":
        return _win_install(args)
    if sys.platform.startswith("linux"):
        return _linux_install(args)
    raise NotImplementedError(f"auto-start install: unsupported platform {sys.platform!r}.")


def uninstall():
    if sys.platform == "darwin":
        return _mac_uninstall()
    if sys.platform == "win32":
        return _win_uninstall()
    if sys.platform.startswith("linux"):
        return _linux_uninstall()
    raise NotImplementedError(f"auto-start uninstall: unsupported platform {sys.platform!r}.")


def status():
    if sys.platform == "darwin":
        return _mac_status()
    if sys.platform == "win32":
        return _win_status()
    if sys.platform.startswith("linux"):
        return _linux_status()
    raise NotImplementedError(f"auto-start status: unsupported platform {sys.platform!r}.")
