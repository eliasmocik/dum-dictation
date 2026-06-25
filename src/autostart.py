#!/usr/bin/env python3
"""
Auto-start dum at login, and self-heal it if it crashes.

This is the "robust launch" half of the daily driver: instead of babysitting a
terminal with `./dum` running, the robot starts itself when you log in and the OS
puts it back if it dies — paired with the menu-bar icon (tray.py) and the
single-instance guard (single_instance.py), it's a real always-there app.

macOS (this phase): a launchd **LaunchAgent** at
  ~/Library/LaunchAgents/sk.zaprazny.dum.plist
with:
  * RunAtLoad        — start at login
  * KeepAlive={SuccessfulExit:false} — relaunch ONLY on a crash (non-zero exit), so
                        picking Quit in the menu bar (clean exit 0) actually quits and
                        stays quit until the next login.
  * ProcessType=Interactive — runs inside the GUI session (it types into focused apps).

The plist runs the SAME daily-driver command as ./dum, plus --tray (menu bar, no
terminal needed). stdout/stderr go to dogfood/ (gitignored).

Windows (phase 2) = Task Scheduler at-logon; Linux (phase 3) = a `systemd --user`
service with Restart=on-failure — both behind this same install()/uninstall()/status()
interface so live.py's call sites don't change.

⚠️ macOS permissions caveat: a launchd-spawned python is a DIFFERENT executable than
your terminal, so the Microphone / Accessibility / Input Monitoring grants you gave the
terminal do NOT carry over. After the first auto-start, macOS will re-prompt (or you grant
them by hand) for ".../.venv/bin/python". This is inherent to login items that aren't a
signed .app bundle; documented so it isn't a mystery.
"""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "sk.zaprazny.dum"
# We launch the `dum` SHELL LAUNCHER (not live.py directly), with --tray appended, so the
# login-started copy is byte-for-byte the same daily driver as a manual `./dum`: same flags
# (--double-cmd --overlay --llm) AND same DUM_* env (strip-fillers, decap, dogfood, …), which
# all live inside `dum`. --tray swaps the babysat terminal for a menu-bar icon. Single source
# of truth: change `dum` and the login item follows.
DEFAULT_ARGS = ["--tray"]

# repo root = parent of this file's dir (src/) — same anchor the engine uses for resources.
REPO_ROOT = Path(__file__).resolve().parent.parent


def agent_plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist_dict(program_args, workdir, out_log, err_log):
    """The launchd job description, as a plain dict (pure — unit-testable without launchctl).
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


def _default_job_paths():
    launcher = REPO_ROOT / "dum"
    logdir = REPO_ROOT / "dogfood"
    return launcher, REPO_ROOT, logdir / "dum.out.log", logdir / "dum.err.log"


def _require_macos(action):
    if sys.platform != "darwin":
        raise NotImplementedError(
            f"auto-start {action} is implemented for macOS (launchd) in this phase; "
            f"the Windows (Task Scheduler) and Linux (systemd --user) variants land in "
            f"the later port phases. Current platform: {sys.platform}.")


def _launchctl(*argv):
    return subprocess.run(["launchctl", *argv], capture_output=True, text=True)


def _bootstrap(plist):
    """Load the agent into the user's GUI session. Prefer the modern `bootstrap`;
    fall back to the older `load -w` on macOS versions where bootstrap is unavailable."""
    uid = os.getuid()
    r = _launchctl("bootstrap", f"gui/{uid}", str(plist))
    if r.returncode == 0:
        return r
    # already loaded, or older launchctl — try the legacy verb
    return _launchctl("load", "-w", str(plist))


def _bootout():
    uid = os.getuid()
    r = _launchctl("bootout", f"gui/{uid}/{LABEL}")
    if r.returncode == 0:
        return r
    return _launchctl("unload", "-w", str(agent_plist_path()))


def install(args=None):
    """Write the LaunchAgent and load it. Idempotent: reloads if already present."""
    _require_macos("install")
    args = list(args) if args is not None else DEFAULT_ARGS
    launcher, workdir, out_log, err_log = _default_job_paths()
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        raise FileNotFoundError(
            f"{venv_python} not found — run ./setup first so the venv exists before installing auto-start.")
    out_log.parent.mkdir(parents=True, exist_ok=True)
    plist = agent_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(build_plist([launcher, *args], workdir, out_log, err_log))
    # If a previous copy is loaded, bootout first so the new plist takes effect.
    _bootout()
    r = _bootstrap(plist)
    ok = r.returncode == 0
    print(f"[autostart] wrote {plist}")
    if ok:
        print("[autostart] loaded — dum will start at login and relaunch on crash.")
        print("            ⚠️  macOS will re-ask for Mic/Accessibility/Input-Monitoring for the")
        print(f"            venv python ({venv_python}); grant them once, then log out/in.")
    else:
        print(f"[autostart] launchctl reported: {r.stderr.strip() or r.stdout.strip()}")
    return ok


def uninstall():
    """Unload and remove the LaunchAgent. Idempotent."""
    _require_macos("uninstall")
    _bootout()
    plist = agent_plist_path()
    existed = plist.exists()
    if existed:
        plist.unlink()
        print(f"[autostart] removed {plist} — dum will no longer start at login.")
    else:
        print("[autostart] nothing to remove (no LaunchAgent installed).")
    return existed


def status():
    """Print whether the agent is installed (plist present) and loaded (in launchctl)."""
    _require_macos("status")
    plist = agent_plist_path()
    installed = plist.exists()
    loaded = _launchctl("list", LABEL).returncode == 0
    print(f"[autostart] plist:  {'present' if installed else 'absent'} ({plist})")
    print(f"[autostart] loaded: {'yes' if loaded else 'no'}")
    return installed, loaded
