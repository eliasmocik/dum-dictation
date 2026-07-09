#!/usr/bin/env python3
"""Windows (Task Scheduler) auto-start backend (split out of autostart.py).
Owner: Rado (@radozaprazny). Public install/uninstall/status dispatch lives in autostart.py."""
import subprocess
from pathlib import Path

from autostart_base import DEFAULT_ARGS, REPO_ROOT

TASK_NAME = "dum-dictation"      # Windows Task Scheduler task name


def windows_launcher_command(args):
    """(command, arguments) to run the dum.ps1 launcher HIDDEN (no console flash) via
    PowerShell - single source of truth for flags + env, mirroring the macOS `dum` launcher."""
    launcher = REPO_ROOT / "dum.ps1"
    arguments = " ".join(["-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                          "-File", f'"{launcher}"', *args])
    return "powershell.exe", arguments


def build_task_xml(command, arguments, workdir):
    """Task Scheduler XML: start at logon, relaunch on failure (the KeepAlive analog), run in
    the interactive GUI session. Pure - unit-testable without schtasks. (schtasks /Create /XML
    wants the file as UTF-16; _win_install encodes it so.)"""
    from xml.sax.saxutils import escape
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>dum dictation - start at logon, relaunch on crash</Description>\n"
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
            f"{venv_python} not found - run setup.ps1 first so the venv exists before installing auto-start.")
    command, arguments = windows_launcher_command(args)
    xml = build_task_xml(command, arguments, REPO_ROOT)
    xml_path = Path(tempfile.gettempdir()) / "dum-dictation-task.xml"
    xml_path.write_bytes(xml.encode("utf-16"))   # schtasks /XML expects UTF-16
    r = _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F")
    ok = r.returncode == 0
    if ok:
        print(f"[autostart] registered Task Scheduler task '{TASK_NAME}' - dum starts at logon "
              "and relaunches on crash.")
    else:
        print(f"[autostart] schtasks reported: {r.stderr.strip() or r.stdout.strip()}")
    return ok


def _win_uninstall():
    r = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    existed = r.returncode == 0
    if existed:
        print(f"[autostart] removed task '{TASK_NAME}' - dum will no longer start at logon.")
    else:
        print(f"[autostart] nothing to remove ({r.stderr.strip() or 'no such task'}).")
    return existed


def _win_status():
    r = _schtasks("/Query", "/TN", TASK_NAME)
    installed = r.returncode == 0
    print(f"[autostart] task '{TASK_NAME}': {'registered' if installed else 'not registered'}")
    return installed, installed
