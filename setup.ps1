# setup.ps1 — one command to make a freshly-cloned checkout runnable on Windows.
#
#   .\setup.ps1
#
# Creates .venv, installs pinned deps (the Mac-only MLX/pyobjc wheels are skipped via
# environment markers; pywin32 is installed), downloads the Parakeet speech model, then
# prints the one permission to grant. After it finishes, run .\dum.ps1.
#
# Windows 10/11, Python 3.12 (install from python.org; `python` must be on PATH).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = ".venv\Scripts\python.exe"
$ParakeetDir = "models\sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
$Tarball = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
$Url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$Tarball"

Write-Host "==> 1/4  Python venv + pinned dependencies"
if (-not (Test-Path $py)) {
    Write-Host "    creating .venv (python -m venv)"
    python -m venv .venv
}
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r requirements.txt

Write-Host ""
Write-Host "==> 2/4  Parakeet speech model"
if ((Test-Path "$ParakeetDir\encoder.int8.onnx") -and (Test-Path "$ParakeetDir\tokens.txt")) {
    Write-Host "    already present at $ParakeetDir — skipping download"
} else {
    New-Item -ItemType Directory -Force -Path models | Out-Null
    Write-Host "    downloading + extracting $Tarball (~480 MB) ..."
    # Download + extract via the venv Python (urllib + tarfile handle .tar.bz2 natively),
    # so this needs no curl/tar — works on any Windows.
    & $py -c "import urllib.request,tarfile,tempfile,os,sys; url=sys.argv[1]; tmp=os.path.join(tempfile.gettempdir(),'parakeet.tar.bz2'); print('    fetching...'); urllib.request.urlretrieve(url,tmp); print('    extracting...'); tarfile.open(tmp,'r:bz2').extractall('models'); os.remove(tmp)" $Url
}
$missing = $false
foreach ($f in @("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")) {
    if (-not (Test-Path "$ParakeetDir\$f")) { Write-Host "    [!] missing $ParakeetDir\$f"; $missing = $true }
}
if ($missing) { Write-Host "    [!] Parakeet model is incomplete — re-run .\setup.ps1"; exit 1 }
Write-Host "    ok: 3 .onnx files + tokens.txt in $ParakeetDir"

Write-Host ""
Write-Host "==> 3/4  Microphone permission"
Write-Host "    Settings > Privacy & security > Microphone: turn ON 'Let desktop apps access your microphone'."
Write-Host "    (No Accessibility / Input-Monitoring step like macOS — SendInput typing and the global"
Write-Host "     double-tap hotkey work without extra grants.)"

Write-Host ""
Write-Host "==> 4/4  Import sanity check (dependencies + the engine itself)"
& $py -c "import sherpa_onnx, sounddevice, pynput, pystray; print('    ok: dependencies import')"
$env:PYTHONPATH = (Join-Path $PSScriptRoot "src")
& $py -c "import live, pipeline, overlay, config, platform_io; print('    ok: engine imports')"

Write-Host ""
Write-Host "Done. Now run:  .\dum.ps1"
Write-Host "(double-tap RIGHT Ctrl to start/stop dictation)"
