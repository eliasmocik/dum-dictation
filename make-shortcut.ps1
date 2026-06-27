# make-shortcut.ps1 - put a "dum dictation" icon on your Desktop (no PowerShell window).
#
# Run once (re-run to refresh):   .\make-shortcut.ps1
#
# Double-click "dum dictation" on the Desktop -> it starts in the system tray (bottom-right, maybe
# under the "^" arrow). Double-tap your hotkey (left Ctrl by default) to dictate; Quit from the tray.
# It launches dum_tray.pyw via the venv pythonw (no console), pastes-at-commit (reliable over remote
# desktop), and uses the microphone from ~/.dum/config.json.
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
$python = Join-Path $repo ".venv\Scripts\python.exe"
$launcher = Join-Path $repo "dum_tray.pyw"
if (-not (Test-Path $pythonw)) {
    Write-Error "$pythonw not found - run .\setup.ps1 first."
    exit 1
}

# A simple green-dot icon (matches the tray), generated with Pillow so we ship no binary asset.
$icoPath = Join-Path $repo "dum.ico"
& $python -c "from PIL import Image, ImageDraw; img=Image.new('RGBA',(256,256),(0,0,0,0)); d=ImageDraw.Draw(img); d.ellipse((36,36,220,220),fill=(52,199,89,255)); img.save(r'$icoPath',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "dum dictation.lnk"

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $pythonw
$lnk.Arguments = "`"$launcher`""
$lnk.WorkingDirectory = $repo
if (Test-Path $icoPath) { $lnk.IconLocation = $icoPath } else { $lnk.IconLocation = "$pythonw,0" }
$lnk.Description = "dum dictation - double-tap your hotkey to dictate"
$lnk.Save()

Write-Host "Created: $lnkPath"
Write-Host "Double-click 'dum dictation' on your Desktop (tray icon may be under the '^' arrow)."
Write-Host "Double-tap left Ctrl to dictate; Quit from the tray icon."
Write-Host "If nothing appears, check the log: $repo\dogfood\tray.log"
