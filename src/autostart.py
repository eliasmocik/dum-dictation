#!/usr/bin/env python3
"""
Auto-start dum at login, and self-heal it if it crashes.

This is the "robust launch" half of the daily driver: instead of babysitting a
terminal with `./dum` running, the robot starts itself when you log in and the OS
puts it back if it dies — paired with the menu-bar/tray icon (tray.py) and the
single-instance guard (single_instance.py), it's a real always-there app.

Implemented per OS behind one install()/uninstall()/status() interface:

  * macOS — a launchd **LaunchAgent** (~/Library/LaunchAgents/sk.zaprazny.dum.plist):
      RunAtLoad (start at login) + KeepAlive={SuccessfulExit:false} (relaunch ONLY on a
      crash, so a clean Quit stays quit) + ProcessType=Interactive (GUI session). Runs
      the `dum` shell launcher + --tray.
  * Windows — a **Task Scheduler** task ("dum-dictation"): a LogonTrigger (start at
      logon) + RestartOnFailure (the KeepAlive analog) + InteractiveToken (GUI session).
      Runs the `dum.ps1` launcher hidden via PowerShell.
  * Linux — a **systemd --user** unit (~/.config/systemd/user/dum.service): WantedBy
      default.target (start at login) + Restart=on-failure (the KeepAlive analog), After
      graphical-session.target (so DISPLAY/clipboard are up). Runs the `dum` launcher + --tray.

All launch the SAME daily-driver launcher (the `dum`/`dum.ps1` script), plus --tray, so the
login copy is byte-for-byte a manual launch — same flags AND same DUM_* env.

⚠️ macOS permissions caveat: a launchd-spawned python is a DIFFERENT executable than your
terminal, so the Mic/Accessibility/Input-Monitoring grants don't carry over — macOS re-asks
for ".../.venv/bin/python" the first time. (Windows has no equivalent re-prompt.)
"""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "sk.zaprazny.dum"        # macOS launchd label
TASK_NAME = "dum-dictation"      # Windows Task Scheduler task name
SERVICE_NAME = "dum.service"     # Linux systemd --user unit name
# We launch the dum SHELL LAUNCHER (not live.py directly), with --tray appended, so the
# login-started copy is byte-for-byte the same daily driver as a manual launch: same flags
# AND same DUM_* env, which all live inside the launcher. --tray swaps the babysat terminal
# for a tray icon. Single source of truth: change the launcher and the login item follows.
DEFAULT_ARGS = ["--tray"]

# repo root = parent of this file's dir (src/) — same anchor the engine uses for resources.
REPO_ROOT = Path(__file__).resolve().parent.parent


# ============================ public, platform-dispatching ============================

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


# ================================== macOS (launchd) ==================================

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
            f"{venv_python} not found — run ./setup first so the venv exists before installing auto-start.")
    out_log.parent.mkdir(parents=True, exist_ok=True)
    plist = agent_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(build_plist([launcher, *args], workdir, out_log, err_log))
    _bootout()                                  # reload cleanly if already present
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


def _mac_uninstall():
    _bootout()
    plist = agent_plist_path()
    existed = plist.exists()
    if existed:
        plist.unlink()
        print(f"[autostart] removed {plist} — dum will no longer start at login.")
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


# ============================== Windows (Task Scheduler) ==============================

def windows_launcher_command(args):
    """(command, arguments) to run the dum.ps1 launcher HIDDEN (no console flash) via
    PowerShell — single source of truth for flags + env, mirroring the macOS `dum` launcher."""
    launcher = REPO_ROOT / "dum.ps1"
    arguments = " ".join(["-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                          "-File", f'"{launcher}"', *args])
    return "powershell.exe", arguments


def build_task_xml(command, arguments, workdir):
    """Task Scheduler XML: start at logon, relaunch on failure (the KeepAlive analog), run in
    the interactive GUI session. Pure — unit-testable without schtasks. (schtasks /Create /XML
    wants the file as UTF-16; _win_install encodes it so.)"""
    from xml.sax.saxutils import escape
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>dum dictation — start at logon, relaunch on crash</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>\n"
        "  </Triggers>\n"
        '  <Principals>\n'
        '    <Principal id="Author">\n'
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>3</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{escape(str(command))}</Command>\n"
        f"      <Arguments>{escape(arguments)}</Arguments>\n"
        f"      <WorkingDirectory>{escape(str(workdir))}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def _schtasks(*argv):
    return subprocess.run(["schtasks", *argv], capture_output=True, text=True)


def _win_install(args=None):
    import tempfile
    args = list(args) if args is not None else DEFAULT_ARGS
    venv_python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        raise FileNotFoundError(
            f"{venv_python} not found — run setup.ps1 first so the venv exists before installing auto-start.")
    command, arguments = windows_launcher_command(args)
    xml = build_task_xml(command, arguments, REPO_ROOT)
    xml_path = Path(tempfile.gettempdir()) / "dum-dictation-task.xml"
    xml_path.write_bytes(xml.encode("utf-16"))   # schtasks /XML expects UTF-16
    r = _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F")
    ok = r.returncode == 0
    if ok:
        print(f"[autostart] registered Task Scheduler task '{TASK_NAME}' — dum starts at logon "
              "and relaunches on crash.")
    else:
        print(f"[autostart] schtasks reported: {r.stderr.strip() or r.stdout.strip()}")
    return ok


def _win_uninstall():
    r = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    existed = r.returncode == 0
    if existed:
        print(f"[autostart] removed task '{TASK_NAME}' — dum will no longer start at logon.")
    else:
        print(f"[autostart] nothing to remove ({r.stderr.strip() or 'no such task'}).")
    return existed


def _win_status():
    r = _schtasks("/Query", "/TN", TASK_NAME)
    installed = r.returncode == 0
    print(f"[autostart] task '{TASK_NAME}': {'registered' if installed else 'not registered'}")
    return installed, installed


# ============================== Linux (systemd --user) ==============================

def service_unit_path():
    return Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME


def build_unit(exec_start, workdir):
    """The systemd --user unit text (pure — unit-testable without systemctl). Starts after the
    graphical session (so DISPLAY/clipboard are up), relaunches on crash (Restart=on-failure =
    the KeepAlive analog), and is pulled in at login by default.target."""
    return (
        "[Unit]\n"
        "Description=dum dictation — start at login, relaunch on crash\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        f"WorkingDirectory={workdir}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemctl(*argv):
    return subprocess.run(["systemctl", "--user", *argv], capture_output=True, text=True)


def _linux_install(args=None):
    args = list(args) if args is not None else DEFAULT_ARGS
    launcher = REPO_ROOT / "dum"
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        raise FileNotFoundError(
            f"{venv_python} not found — run ./setup first so the venv exists before installing auto-start.")
    exec_start = " ".join([str(launcher), *args])
    path = service_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_unit(exec_start, REPO_ROOT))
    _systemctl("daemon-reload")
    r = _systemctl("enable", "--now", SERVICE_NAME)
    ok = r.returncode == 0
    print(f"[autostart] wrote {path}")
    if ok:
        print(f"[autostart] enabled {SERVICE_NAME} — dum starts at login and relaunches on crash.")
        print("            (needs an X11 session for xdotool/xclip; on Wayland install ydotool + "
              "wl-clipboard. If the tray doesn't appear at login, check `systemctl --user status dum`.)")
    else:
        print(f"[autostart] systemctl reported: {r.stderr.strip() or r.stdout.strip()}")
    return ok


def _linux_uninstall():
    _systemctl("disable", "--now", SERVICE_NAME)
    path = service_unit_path()
    existed = path.exists()
    if existed:
        path.unlink()
        _systemctl("daemon-reload")
        print(f"[autostart] removed {path} — dum will no longer start at login.")
    else:
        print("[autostart] nothing to remove (no systemd unit installed).")
    return existed


def _linux_status():
    path = service_unit_path()
    installed = path.exists()
    enabled = _systemctl("is-enabled", SERVICE_NAME).returncode == 0
    print(f"[autostart] unit:    {'present' if installed else 'absent'} ({path})")
    print(f"[autostart] enabled: {'yes' if enabled else 'no'}")
    return installed, enabled
