# setup.ps1 - one command to make a freshly-cloned checkout runnable on Windows.
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
# Hard requirement: CPython 3.12.x. Several pins (numpy, sherpa-onnx) ship wheels only for 3.12,
# so any other Python dies mid-install with a cryptic compile error.
function Test-Py312($exe) {
    # Existence check first, so a missing 'python' doesn't throw CommandNotFoundException under
    # ErrorActionPreference=Stop. Then probe the version with EAP relaxed locally, because on
    # stock PowerShell 5.1 a native command writing to stderr (e.g. the Microsoft Store python
    # stub) turns into a terminating error under Stop and would crash this guard.
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue) -and -not (Test-Path $exe)) { return $false }
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $v = (& $exe -c "import sys; print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))" 2>&1 | Select-Object -Last 1)
        return ($LASTEXITCODE -eq 0 -and ("$v").Trim() -eq '3.12')
    } finally { $ErrorActionPreference = $prev }
}
if (-not (Test-Path $py)) {
    if (-not (Test-Py312 "python")) {
        Write-Host "    [!] Python 3.12 required, but 'python' isn't 3.12 (or isn't on PATH)."
        Write-Host "        Install 3.12 from https://www.python.org/downloads/ and make sure 'python' points to it."
        Write-Host "        Then re-run .\setup.ps1."
        exit 1
    }
    Write-Host "    creating .venv (python -m venv)"
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "    [!] venv creation failed - see the error above."; exit 1 }
} elseif (-not (Test-Py312 $py)) {
    Write-Host "    [!] .venv exists but is not Python 3.12 - rebuild it:"
    Write-Host "        Remove-Item -Recurse -Force .venv ; .\setup.ps1"
    exit 1
}
& $py -m pip install --upgrade pip | Out-Null
# llama-cpp-python (the portable homophone-LLM backend) ships NO prebuilt wheel on PyPI, so a
# plain `-r requirements.txt` would try to COMPILE it from source and fail on Windows (needs MSVC
# + CMake). Install it FIRST from the maintainer's prebuilt index, so the `-r` step below finds it
# already satisfied. CPU wheels are right for the tiny 1B-4bit model; for an NVIDIA GPU swap
# whl/cpu -> whl/cu124 (set $LlamaIndex below). See requirements.txt for the full note.
$LlamaIndex = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
Write-Host "    installing llama-cpp-python==0.3.30 from prebuilt index ($LlamaIndex)"
# --only-binary bars any fallback to the PyPI source build (needs MSVC + CMake); if no wheel
# matches, pip fails fast with a clear message instead of a doomed compile.
& $py -m pip install "llama-cpp-python==0.3.30" --only-binary=llama-cpp-python --extra-index-url $LlamaIndex
if ($LASTEXITCODE -ne 0) {
    Write-Host "    [!] llama-cpp-python wheel install failed - check your network, then re-run .\setup.ps1"
    Write-Host "        manual retry:  $py -m pip install 'llama-cpp-python==0.3.30' --extra-index-url $LlamaIndex"
    exit 1
}
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "    [!] dependency install failed - scroll up for pip's actual error, fix it, then re-run .\setup.ps1"
    exit 1
}

Write-Host ""
Write-Host "==> 2/4  Parakeet speech model"
if ((Test-Path "$ParakeetDir\encoder.int8.onnx") -and (Test-Path "$ParakeetDir\tokens.txt")) {
    Write-Host "    already present at $ParakeetDir - skipping download"
} else {
    New-Item -ItemType Directory -Force -Path models | Out-Null
    Write-Host "    downloading + extracting $Tarball (~480 MB) ..."
    # Download + extract via the venv Python (urllib + tarfile handle .tar.bz2 natively),
    # so this needs no curl/tar - works on any Windows.
    & $py -c "import urllib.request,tarfile,tempfile,os,sys; url=sys.argv[1]; tmp=os.path.join(tempfile.gettempdir(),'parakeet.tar.bz2'); print('    fetching...'); urllib.request.urlretrieve(url,tmp); print('    extracting...'); tarfile.open(tmp,'r:bz2').extractall('models'); os.remove(tmp)" $Url
    if ($LASTEXITCODE -ne 0) { Write-Host "    [!] model download/extract failed - check your network, then re-run .\setup.ps1"; exit 1 }
}
$missing = $false
foreach ($f in @("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")) {
    if (-not (Test-Path "$ParakeetDir\$f")) { Write-Host "    [!] missing $ParakeetDir\$f"; $missing = $true }
}
if ($missing) { Write-Host "    [!] Parakeet model is incomplete - re-run .\setup.ps1"; exit 1 }
Write-Host "    ok: 3 .onnx files + tokens.txt in $ParakeetDir"

Write-Host ""
Write-Host "==> 3/4  Microphone permission"
Write-Host "    Settings -> Privacy and security -> Microphone: turn ON 'Let desktop apps access your microphone'."
Write-Host "    (No Accessibility / Input-Monitoring step like macOS. SendInput typing and the global"
Write-Host "     double-tap hotkey work without extra grants.)"

Write-Host ""
Write-Host "==> 4/4  Import sanity check (dependencies + the engine itself)"
& $py -c "import sherpa_onnx, sounddevice, pynput, pystray, llama_cpp; print('    ok: dependencies import (incl. llama_cpp)')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "    [!] dependency import failed (see error above) - re-run .\setup.ps1;"
    Write-Host "        if it keeps failing:  Remove-Item -Recurse -Force .venv ; .\setup.ps1"
    exit 1
}
$env:PYTHONPATH = (Join-Path $PSScriptRoot "src")
& $py -c "import live, pipeline, overlay, config, platform_io; print('    ok: engine imports')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "    [!] engine import failed (see error above) - your checkout may be incomplete;"
    Write-Host "        make sure src\ is intact, then re-run .\setup.ps1"
    exit 1
}

Write-Host ""
Write-Host "Done. Now run:  .\dum.ps1"
Write-Host "(double-tap RIGHT Ctrl to start/stop dictation)"
