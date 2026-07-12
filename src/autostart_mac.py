#!/usr/bin/env python3
"""macOS (launchd LaunchAgent) auto-start backend (split out of autostart.py).
Owner: Elias (@eliasmocik). Public install/uninstall/status dispatch lives in autostart.py."""
import os
import plistlib
import subprocess
from pathlib import Path

from autostart_base import DEFAULT_ARGS, REPO_ROOT

LABEL = "sk.zaprazny.dum"        # macOS launchd label


def agent_plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist_dict(program_args, workdir, out_log, err_log):
    """The launchd job description, as a plain dict (pure - unit-testable without launchctl).
    `program_args` is the full argv launchd should exec, e.g. ["/repo/dum", "--tray"]."""
    return {
        "Label": LABEL,
        "ProgramArguments": [str(a) for a in program_args],
        "WorkingDirectory": str(workdir),
        "RunAtLoad": True,
        # relaunch on crash, but NOT after a clean Quit from the menu bar (exit 0)
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": str(out_log),
        "StandardErrorPath": str(err_log),
        # launchd hands jobs a bare PATH; the app shells out to pbcopy/osascript/afplay.
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"},
    }


def build_plist(program_args, workdir, out_log, err_log):
    """Serialize build_plist_dict to the launchd XML plist bytes."""
    return plistlib.dumps(build_plist_dict(program_args, workdir, out_log, err_log))


def _mac_job_paths():
    launcher = REPO_ROOT / "dum"
    logdir = REPO_ROOT / "dogfood"
    return launcher, REPO_ROOT, logdir / "dum.out.log", logdir / "dum.err.log"


def _launchctl(*argv):
    return subprocess.run(["launchctl", *argv], capture_output=True, text=True)


def _bootstrap(plist):
    """Load the agent into the user's GUI session. Prefer the modern `bootstrap`;
    fall back to the older `load -w` on macOS versions where bootstrap is unavailable."""
    uid = os.getuid()
    r = _launchctl("bootstrap", f"gui/{uid}", str(plist))
    if r.returncode == 0:
        return r
    return _launchctl("load", "-w", str(plist))


def _bootout():
    uid = os.getuid()
    r = _launchctl("bootout", f"gui/{uid}/{LABEL}")
    if r.returncode == 0:
        return r
    return _launchctl("unload", "-w", str(agent_plist_path()))


def _mac_install(args=None):
    args = list(args) if args is not None else DEFAULT_ARGS
    launcher, workdir, out_log, err_log = _mac_job_paths()
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        raise FileNotFoundError(
            f"{venv_python} not found - run ./setup first so the venv exists before installing auto-start.")
    out_log.parent.mkdir(parents=True, exist_ok=True)
    plist = agent_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(build_plist([launcher, *args], workdir, out_log, err_log))
    _bootout()                                  # reload cleanly if already present
    r = _bootstrap(plist)
    ok = r.returncode == 0
    print(f"[autostart] wrote {plist}")
    if ok:
        print("[autostart] loaded - dum will start at login and relaunch on crash.")
        print("             macOS will re-ask for Mic/Accessibility/Input-Monitoring for the")
        print(f"            venv python ({venv_python}); grant them once, then log out/in.")
    else:
        print(f"[autostart] launchctl reported: {r.stderr.strip() or r.stdout.strip()}")
    return ok


def _mac_uninstall():
    _bootout()
    plist = agent_plist_path()
    existed = plist.exists()
    if existed:
        plist.unlink()
        print(f"[autostart] removed {plist} - dum will no longer start at login.")
    else:
        print("[autostart] nothing to remove (no LaunchAgent installed).")
    return existed


def _mac_status():
    plist = agent_plist_path()
    installed = plist.exists()
    loaded = _launchctl("list", LABEL).returncode == 0
    print(f"[autostart] plist:  {'present' if installed else 'absent'} ({plist})")
    print(f"[autostart] loaded: {'yes' if loaded else 'no'}")
    return installed, loaded
