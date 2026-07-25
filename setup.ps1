# setup.ps1 - one command to make a freshly-cloned checkout runnable on Windows.
#
#   .\setup.ps1
#
# Creates .venv, installs pinned deps (the Mac-only MLX/pyobjc wheels are skipped via
# environment markers; pywin32 is installed), downloads the Parakeet speech model, then
# prints the one permission to grant. After it finishes, run .\dum.ps1.
#
# Windows 10/11. Works with any CPython in $PySupported below; if the machine has none,
# setup fetches its own into .\.python rather than dead-ending on a version mismatch.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = ".venv\Scripts\python.exe"
$ParakeetDir = "models\sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
$Tarball = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
$Url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$Tarball"

Write-Host "==> 1/4  Python venv + pinned dependencies"
# Which CPython minors we support on Windows. NOTE this is deliberately one SHORT of the
# mac/Linux list in `setup` (which allows 3.14): pywin32==308 - a Windows-only pin - ships
# no cp314 wheel, so 3.14 would die at `pip install -r`. Every other pin has cp314 wheels,
# so bumping pywin32 to >=311 is all that's needed to add '3.14' here.
$PySupported = @('3.12', '3.13')
# The interpreter we vendor when the machine has no supported Python, so setup.ps1 can never
# dead-end on "your python is the wrong version". python-build-standalone is the same
# relocatable CPython uv ships; pinned by release tag + sha256 so the download is verified.
$PbsTag = '20260718'
$PbsPy  = '3.12.13'
$VendorRoot = '.python'
$VendorPy = ".python\python\python.exe"

function Get-PyMinor($exe, $extraArgs) {
    # Existence check first, so a missing 'python' doesn't throw CommandNotFoundException under
    # ErrorActionPreference=Stop. Then probe the version with EAP relaxed locally, because on
    # stock PowerShell 5.1 a native command writing to stderr (e.g. the Microsoft Store python
    # stub) turns into a terminating error under Stop and would crash this guard.
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue) -and -not (Test-Path $exe)) { return $null }
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $probe = @($extraArgs) + @('-c', "import sys; print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))")
        # DISCARD stderr (2>$null), never merge it (2>&1). With a merge, `Select-Object -Last 1`
        # returns whatever the interpreter printed to stderr LAST - a ResourceWarning, an
        # "Exception ignored in:" at shutdown, a chatty sitecustomize - instead of the version,
        # and a perfectly good Python gets judged unsupported. Then match the version shape
        # explicitly rather than trusting position. (The bash `setup` discards stderr too.)
        $global:LASTEXITCODE = 0
        $out = @(& $exe @probe 2>$null)
        if ($LASTEXITCODE -ne 0) { return $null }
        $v = $out | Where-Object { "$_" -match '^\s*\d+\.\d+\s*$' } | Select-Object -Last 1
        if ($null -eq $v) { return $null }
        return ("$v").Trim()
    } catch { return $null } finally { $ErrorActionPreference = $prev }
}
function Test-PySupported($exe, $extraArgs) {
    $m = Get-PyMinor $exe $extraArgs
    if ($null -eq $m -or $PySupported -notcontains $m) { return $false }
    # Right version isn't enough - it must also be able to BUILD a venv. Rejecting a
    # venv-incapable Python here makes the caller fall through to vendoring instead of
    # half-building a pip-less .venv.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $probe = @($extraArgs) + @('-c', 'import ensurepip, venv')
        # Reset first: if $exe resolves to a profile-defined function/alias rather than a real
        # executable, $LASTEXITCODE is never updated and we'd read a stale 0 as success.
        $global:LASTEXITCODE = 0
        & $exe @probe 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false } finally { $ErrorActionPreference = $prev }
}

# Download a known-good CPython into the repo. No admin rights, nothing written outside this
# folder, no PATH or registry changes.
function Install-VendoredPython {
    # ARM64 is deliberately allowed: Windows-on-ARM runs x86_64 under emulation, and x86_64 is
    # what this project needs anyway - sherpa-onnx publishes win32/win_amd64 wheels only, so a
    # NATIVE arm64 Python would die at `pip install -r` regardless. Emulated x86_64 works.
    if (-not ((Test-Path env:PROCESSOR_ARCHITECTURE) -and
              ($env:PROCESSOR_ARCHITECTURE -in @('AMD64', 'ARM64') -or $env:PROCESSOR_ARCHITEW6432 -eq 'AMD64'))) {
        Write-Host "    [!] no prebuilt CPython for this CPU architecture ($env:PROCESSOR_ARCHITECTURE)."
        return $false
    }
    if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) {
        Write-Host "    [!] tar.exe not found (needs Windows 10 1803+) - can't extract the download."
        return $false
    }
    $sha = '0d422a1439ec308e03f47df551bc30f5994727c456e414b026d202bcda9b7c1c'
    $tarball = "cpython-$PbsPy+$PbsTag-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    $url = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsTag/$tarball"
    New-Item -ItemType Directory -Force -Path $VendorRoot | Out-Null
    $dest = Join-Path $VendorRoot $tarball
    Write-Host "    downloading CPython $PbsPy (~22 MB) ..."
    # $ProgressPreference: PS 5.1's Invoke-WebRequest is ~10x slower with the progress bar on.
    # TLS 1.2: 5.1 inherits the process-wide default, which on older builds excludes it and
    # makes every GitHub download fail with an unhelpful "could not create SSL/TLS channel".
    $prevProgress = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
    try {
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 }
        catch { $null = $_ }  # best-effort: newer hosts manage TLS themselves and may block this
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    } catch {
        Write-Host "    [!] download failed - check your network. URL was:"
        Write-Host "        $url"
        return $false
    } finally { $ProgressPreference = $prevProgress }
    $got = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $sha) {
        Write-Host "    [!] checksum mismatch on $tarball - refusing to use it."
        Write-Host "        expected $sha"
        Write-Host "        got      $got"
        Remove-Item $dest -Force -ErrorAction SilentlyContinue
        return $false
    }
    # Wipe any earlier vendored tree first: tar MERGES over an existing dir, so a leftover
    # Lib\python3.X\ from a different pin would linger beside the new one (same reason the
    # bash `setup` does this). Reachable whenever a stale/rejected .python is re-vendored.
    Remove-Item -Recurse -Force (Join-Path $VendorRoot 'python') -ErrorAction SilentlyContinue
    # `| Out-Null` is load-bearing: a PowerShell function returns ALL uncaptured output, so any
    # stdout from tar.exe would be appended to the return value and make `if (Install-
    # VendoredPython)` truthy even on `return $false`. EAP is relaxed so native stderr under
    # PS 5.1 can't turn into a terminating NativeCommandError before the check below.
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    try { & tar.exe -xzf $dest -C $VendorRoot 2>$null | Out-Null }
    finally { $ErrorActionPreference = $prevEap }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    [!] extract failed (corrupt download?) - clean up and re-run:"
        Write-Host "        Remove-Item -Recurse -Force $VendorRoot ; .\setup.ps1"
        return $false
    }
    Remove-Item $dest -Force -ErrorAction SilentlyContinue
    return (Test-Path $VendorPy)
}

# A .venv whose interpreter no longer runs (repo moved/renamed, or the Python it was built
# from was uninstalled) is worse than no venv - every later step fails obscurely.
# Test RUNNABILITY, not just existence (the bash `setup` does the same): the common Windows
# failure is python.exe being present but dead - the base Python it was built from was
# upgraded away, leaving pyvenv.cfg's `home =` dangling. Existence-only would fall through
# and report a blank version with a misleading "isn't supported" reason.
if ((Test-Path .venv) -and ((-not (Test-Path $py)) -or ($null -eq (Get-PyMinor $py @())))) {
    Write-Host "    [!] .venv exists but its Python doesn't run (repo moved, or the Python it was"
    Write-Host "        built from was removed/upgraded). Rebuild it:"
    Write-Host "        Remove-Item -Recurse -Force .venv ; .\setup.ps1"
    exit 1
}

if (-not (Test-Path $py)) {
    # Prefer, in order: an already-vendored CPython, a supported 'python'/'python3' on PATH,
    # the py launcher pinned to a supported minor, then vendor one.
    $pyBin = $null; $pyArgs = @()
    if ((Test-Path $VendorPy) -and (Test-PySupported $VendorPy @())) {
        $pyBin = $VendorPy
    } else {
        foreach ($cand in @('python', 'python3')) {
            if (Test-PySupported $cand @()) { $pyBin = $cand; break }
        }
        if (-not $pyBin -and (Get-Command 'py' -ErrorAction SilentlyContinue)) {
            foreach ($v in $PySupported) {
                if (Test-PySupported 'py' @("-$v")) { $pyBin = 'py'; $pyArgs = @("-$v"); break }
            }
        }
    }
    if (-not $pyBin) {
        Write-Host "    no supported Python found on this machine (need one of: $($PySupported -join ', '))."
        Write-Host "    fetching a private copy into .\$VendorRoot - your system Python is left alone."
        if (Install-VendoredPython) {
            $pyBin = $VendorPy; $pyArgs = @()
        } else {
            Write-Host "    [!] couldn't vendor a Python, and none of $($PySupported -join ', ') is installed."
            Write-Host "        Install one from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'),"
            Write-Host "        then re-run .\setup.ps1."
            exit 1
        }
    }
    Write-Host "    creating .venv with $pyBin $($pyArgs -join ' ') (Python $(Get-PyMinor $pyBin $pyArgs))"
    $global:LASTEXITCODE = 0
    & $pyBin @pyArgs -m venv .venv
    # Same fallback as the bash `setup`: the ensurepip/venv probe catches most bad Pythons, but
    # one can pass it and still fail to BUILD a venv (stripped ensurepip._bundled, broken pip).
    # Don't dead-end - vendor and retry, unless the vendored copy is what just failed.
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
        if ($pyBin -ne $VendorPy) {
            Write-Host "    [!] $pyBin couldn't build a venv (see the error above) - falling back to"
            Write-Host "        a private copy of Python; your system one is left alone."
            if (-not (Install-VendoredPython)) {
                Write-Host "    [!] couldn't fetch a private Python either (see the error above)."
                Write-Host "        Install one from https://www.python.org/downloads/ (tick 'Add python.exe"
                Write-Host "        to PATH'), then re-run .\setup.ps1."
                exit 1
            }
            $global:LASTEXITCODE = 0
            & $VendorPy -m venv .venv
            if ($LASTEXITCODE -ne 0) {
                Write-Host "    [!] the vendored Python couldn't build a venv either. Clean up and retry:"
                Write-Host "        Remove-Item -Recurse -Force .venv,$VendorRoot ; .\setup.ps1"
                exit 1
            }
        } else {
            Write-Host "    [!] the vendored Python couldn't build a venv (see the error above)."
            Write-Host "        Clean up and retry:  Remove-Item -Recurse -Force .venv,$VendorRoot ; .\setup.ps1"
            exit 1
        }
    }
} elseif (-not (Test-PySupported $py @())) {
    Write-Host "    [!] .venv is Python $(Get-PyMinor $py @()), which isn't supported (need one of: $($PySupported -join ', ')) - rebuild it:"
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
    & $py -c "import urllib.request,tarfile,tempfile,os,sys; url=sys.argv[1]; tmp=os.path.join(tempfile.gettempdir(),'parakeet.tar.bz2'); print('    fetching...'); urllib.request.urlretrieve(url,tmp); print('    extracting...'); tarfile.open(tmp,'r:bz2').extractall('models',filter='data'); os.remove(tmp)" $Url
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
