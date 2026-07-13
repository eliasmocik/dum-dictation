#!/usr/bin/env python3
"""Linux (systemd --user) auto-start backend (split out of autostart.py).

Public install/uninstall/status dispatch lives in autostart.py. The service unit
starts after the graphical session so DISPLAY/WAYLAND_DISPLAY is available.
Relaunches on crash (Restart=on-failure)."""
import subprocess
from pathlib import Path

from autostart_base import DEFAULT_ARGS, REPO_ROOT

SERVICE_NAME = "dum.service"     # Linux systemd --user unit name


def service_unit_path():
    return Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME


def build_unit(exec_start, workdir):
    """The systemd --user unit text (pure - unit-testable without systemctl). Starts after the
    graphical session (so DISPLAY/clipboard are up), relaunches on crash (Restart=on-failure =
    the KeepAlive analog), and is pulled in at login by default.target."""
    return (
        "[Unit]\n"
        "Description=dum dictation - start at login, relaunch on crash\n"
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
            f"{venv_python} not found - run ./setup first so the venv exists before installing auto-start.")
    exec_start = " ".join([str(launcher), *args])
    path = service_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_unit(exec_start, REPO_ROOT))
    _systemctl("daemon-reload")
    r = _systemctl("enable", "--now", SERVICE_NAME)
    ok = r.returncode == 0
    print(f"[autostart] wrote {path}")
    if ok:
        print(f"[autostart] enabled {SERVICE_NAME} - dum starts at login and relaunches on crash.")
        print("            X11: needs xdotool, xclip.  Wayland: needs ydotool, wl-clipboard.")
        print("            If the tray doesn't appear at login, check `systemctl --user status dum`.")
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
        print(f"[autostart] removed {path} - dum will no longer start at login.")
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
