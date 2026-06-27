# dum.ps1 - Windows launcher for the dum dictation daily driver (the mirror of ./dum).
#
#   .\dum.ps1                 # run the daily driver (double-tap RIGHT Ctrl to start/stop)
#   .\dum.ps1 --tray          # menu-bar/tray icon instead of a console window
#   .\dum.ps1 --config        # re-run the first-run mic/hotkey wizard
#   .\dum.ps1 --install-autostart   # start at logon (Task Scheduler); --uninstall-autostart
#
# --llm is ON by default (mirrors ./dum): the homophone LLM now runs on Windows via the
# portable llama.cpp backend, so Windows gets the same dictation as Mac. It degrades
# gracefully if the backend can't load. Pass --no-llm-equivalent by editing here if too slow.
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Rest)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# DUM_* env defaults - same knobs the bash launcher sets; each overridable per-run.
if (-not $env:DUM_EVENTS)        { $env:DUM_EVENTS = "dogfood\events.jsonl" }
if (-not $env:DUM_DOGFOOD_FULL)  { $env:DUM_DOGFOOD_FULL = "1" }
if (-not $env:DUM_VSCODE_BRIDGE) { $env:DUM_VSCODE_BRIDGE = "1" }
if (-not $env:DUM_STRIP_FILLERS) { $env:DUM_STRIP_FILLERS = "1" }
if (-not $env:DUM_DECAP_CAPS)    { $env:DUM_DECAP_CAPS = "1" }
New-Item -ItemType Directory -Force -Path (Split-Path $env:DUM_EVENTS) | Out-Null

# --tray => no console window (use pythonw); a plain run uses python.exe so logs show here.
if ($Rest -contains "--tray") {
    $exe = ".venv\Scripts\pythonw.exe"
} else {
    $exe = ".venv\Scripts\python.exe"
}
if (-not (Test-Path $exe)) {
    Write-Error "$exe not found - run .\setup.ps1 first."
    exit 1
}

& $exe "src\live.py" "--double-cmd" "--overlay" "--llm" @Rest
