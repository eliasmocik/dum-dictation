#!/usr/bin/env python3
"""Auto-start dispatcher - install()/uninstall()/status(), selected by sys.platform.

The "robust launch" so the robot starts at login and self-heals on crash (paired with the
tray icon + single-instance guard). Each OS backend lives one-per-file
(autostart_{mac,windows,linux}.py, one owner each); this module only dispatches and re-exports
the pure builders + path helpers (tests reference autostart.build_plist etc.). All backends
launch the SAME dum/dum.ps1 launcher + --tray, so the login copy equals a manual launch.

macOS permissions caveat: a launchd-spawned python is a different executable than your
terminal, so Mic/Accessibility/Input-Monitoring grants don't carry - macOS re-asks once.
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


def should_enable_by_default(cfg, frozen, already_installed):
    """Should a launch turn auto-start ON without being asked? Pure, so it is testable.

    True exactly once, on a downloaded app's first run. Three guards, each earning its keep:

      cfg["autostart_offered"]  - the one-time latch. Without it a user who switches
                                  auto-start off would find it back on at the next launch,
                                  because "no login item installed" looks identical to
                                  "never offered". The menu toggle has to mean something.
      frozen                    - a .app is something a person installed and expects to keep
                                  running; a git checkout is a dev tree, and silently adding
                                  a LaunchAgent pointing into somebody's working copy (which
                                  they may move, rename or delete) is not ours to do.
      already_installed         - never fight an existing login item, however it got there.

    Deliberately NOT consulted: whether any permission is granted. Auto-start is about the
    app coming back after a reboot; it is orthogonal to whether it can hear you yet.
    """
    return bool(frozen) and not cfg.get("autostart_offered", False) and not already_installed


def enable_by_default(cfg, frozen=None, log=print):
    """Apply should_enable_by_default(), then LATCH regardless of the outcome.

    The latch is set even when the install fails, on purpose: a launchd that refuses is not
    going to start agreeing on the next launch, and retrying forever would mean a failing
    install nags at every single start. One attempt, then the menu toggle owns it.

    Returns True only if a login item was actually installed.
    """
    import sys as _sys
    if frozen is None:
        frozen = bool(getattr(_sys, "frozen", False))
    try:
        installed, _loaded = status_quiet()
    except Exception:
        installed = False
    if not should_enable_by_default(cfg, frozen, installed):
        return False
    ok = False
    try:
        ok = bool(install())
        if ok:
            log("[autostart] enabled by default for a new install - "
                "turn it off any time from the menu bar.")
    except Exception as e:
        log(f"[autostart] could not enable by default: {type(e).__name__}: {e}")
    cfg["autostart_offered"] = True
    try:
        import config
        config.save_config(cfg)
    except Exception:
        pass
    return ok


def status_quiet():
    """(installed, loaded) without printing - status() is chatty by design."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return status()


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
