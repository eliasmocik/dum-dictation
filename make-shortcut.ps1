# make-shortcut.ps1 - put a "dum dictation" icon on your Desktop (no PowerShell window).
#
# Run once:   .\make-shortcut.ps1
#
# Then double-click "dum dictation" on the Desktop. It starts in the system tray (bottom-right);
# double-tap your hotkey (left Ctrl by default) to start/stop dictation. Quit from the tray icon.
# Uses paste-at-commit (reliable over remote desktop) and the microphone from ~/.dum/config.json.
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Error "$pythonw not found - run .\setup.ps1 first."
    exit 1
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "dum dictation.lnk"

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $pythonw
# No --overlay => paste-at-commit (reliable over RDP). --tray => tray icon, no console window.
# --llm stays on; it degrades gracefully if llama.cpp can't load. Mic comes from the saved config.
$lnk.Arguments = "src\live.py --double-cmd --tray --llm"
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$pythonw,0"
$lnk.Description = "dum dictation - double-tap your hotkey to dictate"
$lnk.Save()

Write-Host "Created: $lnkPath"
Write-Host "Double-click 'dum dictation' on your Desktop."
Write-Host "It runs in the system tray; double-tap left Ctrl to dictate, and Quit from the tray icon."
