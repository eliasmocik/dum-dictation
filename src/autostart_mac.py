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


def build_plist_dict(program_args, workdir, out_log, err_log, bundle_id=None):
    """The launchd job description, as a plain dict (pure - unit-testable without launchctl).
    `program_args` is the full argv launchd should exec, e.g. ["/repo/dum", "--tray"] from a
    checkout or ["/usr/bin/open", "-b", "sk.zaprazny.dum"] for the bundled app."""
    d = {
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
    if bundle_id:
        # Ties the job to the app, so macOS lists it under the app's own name in
        # Settings > General > Login Items instead of as an anonymous background item the
        # user cannot identify - and attributes it to the app for TCC purposes. Apple's
        # guidance for a launchd job that belongs to an app.
        d["AssociatedBundleIdentifiers"] = [bundle_id]
    return d


def build_plist(program_args, workdir, out_log, err_log, bundle_id=None):
    """Serialize build_plist_dict to the launchd XML plist bytes."""
    return plistlib.dumps(build_plist_dict(program_args, workdir, out_log, err_log,
                                           bundle_id=bundle_id))


def app_bundle_path():
    """The .app this code is running from, or None when running from a git checkout.

    Frozen, sys.executable is <bundle>/Contents/MacOS/dum, so the bundle is three levels up.
    """
    import sys
    if not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).resolve()
    for _ in range(3):
        p = p.parent
    return p if p.suffix == ".app" else None


def _mac_job_paths():
    """Where launchd should point, and where its logs go.

    Two worlds, and conflating them is why "Open at login" silently did nothing in the
    shipped app:

      git checkout - launch the ./dum shell script from the repo, logs into <repo>/dogfood.
      bundled .app - launch the APP. Apple requires a Mach-O main executable for TCC ("if
                     your product uses a script as its main executable, you're likely to
                     encounter TCC problems"), and the repo's ./dum + .venv simply do not
                     exist next to a downloaded app. Logs go to ~/Library/Logs/dum: writing
                     into the bundle would invalidate its code signature, and macOS keys the
                     user's Microphone / Accessibility / Input Monitoring grants to that
                     signature.
    """
    app = app_bundle_path()
    if app is not None:
        logdir = Path.home() / "Library" / "Logs" / "dum"
        # `open -b` rather than the inner executable: it launches through LaunchServices, so
        # the process is a real app (status item, correct TCC attribution) instead of a bare
        # binary macOS treats as a command-line tool.
        return (["/usr/bin/open", "-b", LABEL], str(Path.home()),
                logdir / "dum.out.log", logdir / "dum.err.log")
    launcher = REPO_ROOT / "dum"
    logdir = REPO_ROOT / "dogfood"
    return [str(launcher)], REPO_ROOT, logdir / "dum.out.log", logdir / "dum.err.log"


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
    program, workdir, out_log, err_log = _mac_job_paths()
    app = app_bundle_path()

    if app is None:
        # Checkout only: the launcher execs .venv/bin/python, so a missing venv would produce
        # a login item that fails silently at every boot.
        venv_python = REPO_ROOT / ".venv" / "bin" / "python"
        if not venv_python.exists():
            raise FileNotFoundError(
                f"{venv_python} not found - run ./setup first so the venv exists before "
                "installing auto-start.")
        program_args = [*program, *args]
    else:
        # `open -b <id>` takes no dictation flags: the frozen entry point supplies its own
        # (--double-cmd --overlay --llm --tray) when launched with no argv. Appending them
        # here would make `open` treat them as FILES to open and the launch would fail.
        program_args = list(program)

    out_log.parent.mkdir(parents=True, exist_ok=True)
    plist = agent_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(build_plist(program_args, workdir, out_log, err_log,
                                  bundle_id=LABEL if app is not None else None))
    _bootout()                                  # reload cleanly if already present
    r = _bootstrap(plist)
    ok = r.returncode == 0
    print(f"[autostart] wrote {plist}")
    if ok:
        print("[autostart] loaded - dum will start at login and relaunch on crash.")
        if app is None:
            print("             macOS will re-ask for Mic/Accessibility/Input-Monitoring for the")
            print(f"            venv python ({REPO_ROOT / '.venv' / 'bin' / 'python'});"
                  " grant them once, then log out/in.")
        else:
            # The whole point of signing every build with the same certificate: the login-item
            # copy is the SAME app identity, so the permissions already granted still apply.
            print(f"             launching {app.name} - existing permissions carry over.")
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
