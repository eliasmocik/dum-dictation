#!/usr/bin/env python3
"""
First-run model download - the bundled app's replacement for `./setup` steps 2 and 3.

A git checkout gets its models from `./setup`. A downloaded .app has no setup script, so it
fetches them itself on first launch, into USER_DATA (never inside the bundle - writing there
would invalidate the code signature and, because macOS keys TCC grants to that signature, cost
the user their Microphone / Accessibility / Input Monitoring permissions).

Two models, deliberately different in urgency:

  Parakeet ASR   487 MB, REQUIRED. Nothing can be dictated until it lands, so this is what the
                 first-run window shows progress for.
  Homophone LLM  808 MB, OPTIONAL. Dictation already works without it (the phonetic + alias
                 layers still fix nginx/kubectl); it only adds homophone judgement like
                 git/get and grep/grab. Downloads in the background, afterwards.

Progress is reported in THREE phases because two of them are not downloads and a bar parked at
100% while the app is busy is where bug reports come from:

    downloading (determinate)  ->  verifying (indeterminate)  ->  extracting (indeterminate)

The network is behind `_urlopen` so tests can exercise resume, truncation, stalls and 416
without touching the wire.
"""
import os
import shutil
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PARAKEET_TARBALL = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
PARAKEET_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                + PARAKEET_TARBALL)
PARAKEET_DIRNAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
# The four files find_model_dir()/pick() need. Presence of all four is what "installed" means -
# a half-extracted tarball must never read as success.
PARAKEET_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")

# Refuse to start a download that cannot finish. 487 MB compressed + 671 MB extracted, plus the
# LLM's 808 MB and headroom - running the disk to zero mid-write is a far worse failure than a
# clear message up front.
MIN_FREE_BYTES = 3 * 1024**3
# No bytes at all for this long = dead connection. A socket timeout alone does not catch a
# trickle, which is the failure people actually hit on hotel wifi.
STALL_S = 60.0
CONNECT_TIMEOUT_S = 20.0
# Progress callbacks cross a thread boundary into the UI; emitting per-chunk would swamp it.
PROGRESS_MIN_INTERVAL_S = 0.1


class DownloadError(RuntimeError):
    pass


def _urlopen(req, timeout):                     # seam: tests patch this
    return urllib.request.urlopen(req, timeout=timeout)


def free_bytes(path):
    path = Path(path)
    while not path.exists():                    # the target dir may not exist yet
        path = path.parent
    return shutil.disk_usage(path).free


def is_installed(models_dir):
    """True only when every file the recognizer needs is present."""
    d = Path(models_dir) / PARAKEET_DIRNAME
    return d.is_dir() and all((d / f).exists() for f in PARAKEET_FILES)


def download_file(url, dest, progress=None, expected_size=None):
    """Download `url` to `dest`, resuming a previous partial attempt.

    Resume is worth the complexity here: this is ~487 MB and the app may be quit mid-download.
    We keep a `<dest>.part` and continue with a Range request.

    NOTE we never prune stale .part files on launch. That is Ollama's OLLAMA_NOPRUNE trap - it
    turns "resume where you left off" into "start the 487 MB again".
    """
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "dum-dictation"})
    if have:
        req.add_header("Range", f"bytes={have}-")

    try:
        resp = _urlopen(req, CONNECT_TIMEOUT_S)
    except urllib.error.HTTPError as e:
        # 416 = the server says our offset is past the end, i.e. the .part is stale or the
        # asset changed under us. Discard it and start clean rather than looping forever.
        if e.code == 416 and have:
            part.unlink(missing_ok=True)
            return download_file(url, dest, progress, expected_size)
        raise DownloadError(f"HTTP {e.code} fetching {url}") from e
    except Exception as e:
        raise DownloadError(f"cannot reach {url}: {e}") from e

    status = getattr(resp, "status", 200) or 200
    # Trust a resume ONLY on a real 206 whose Content-Range starts where we actually are. A
    # server that ignores Range answers 200 with the WHOLE file; appending that to our .part
    # would silently produce a corrupt archive that only fails much later, at extraction.
    mode = "ab"
    if have:
        cr = resp.headers.get("Content-Range", "") if hasattr(resp, "headers") else ""
        ok = status == 206 and cr.replace("bytes ", "").split("-")[0] == str(have)
        if not ok:
            have, mode = 0, "wb"

    total = expected_size
    if hasattr(resp, "headers"):
        try:
            total = int(resp.headers.get("Content-Length", 0)) + have or expected_size
        except (TypeError, ValueError):
            pass

    last_emit = 0.0
    last_data = time.monotonic()
    with open(part, mode) as fh:
        while True:
            chunk = resp.read(1024 * 256)
            now = time.monotonic()
            if not chunk:
                break
            fh.write(chunk)
            have += len(chunk)
            last_data = now
            if progress and (now - last_emit) >= PROGRESS_MIN_INTERVAL_S:
                progress("downloading", have, total)
                last_emit = now
            if now - last_data > STALL_S:        # defensive; read() usually raises first
                raise DownloadError("download stalled")
    if progress:
        progress("downloading", have, total)

    if total and have < total:
        raise DownloadError(f"incomplete download: got {have} of {total} bytes")
    part.replace(dest)                           # atomic: no half file ever appears at `dest`
    return dest


def extract_parakeet(tarball, models_dir, progress=None):
    """Extract the .tar.bz2 into models_dir, then assert the layout is complete."""
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("extracting", 0, None)
    try:
        with tarfile.open(tarball, "r:bz2") as tf:
            # filter="data" refuses absolute paths and ../ escapes; it is the default from
            # Python 3.14 and a deprecation warning before that, so set it explicitly.
            tf.extractall(models_dir, filter="data")
    # tarfile.ReadError ("not a bzip2 file") subclasses TarError, so a corrupt or truncated
    # archive lands here; OSError covers the bz2 decompressor's own invalid-data path.
    except (tarfile.TarError, EOFError, OSError) as e:
        raise DownloadError(f"extract failed (corrupt download?): {e}") from e
    if not is_installed(models_dir):
        raise DownloadError("extracted archive is missing expected model files")
    return models_dir / PARAKEET_DIRNAME


def ensure_parakeet(models_dir, progress=None):
    """Make the ASR model available. Returns its directory. Idempotent and resumable."""
    models_dir = Path(models_dir)
    if is_installed(models_dir):
        return models_dir / PARAKEET_DIRNAME

    if free_bytes(models_dir) < MIN_FREE_BYTES:
        raise DownloadError(
            f"not enough free disk space - dum needs about "
            f"{MIN_FREE_BYTES // 1024**3} GB free to install its speech model")

    tarball = models_dir / PARAKEET_TARBALL
    if not tarball.exists():
        download_file(PARAKEET_URL, tarball, progress=progress)
    if progress:
        progress("verifying", 0, None)
    d = extract_parakeet(tarball, models_dir, progress=progress)
    tarball.unlink(missing_ok=True)              # 487 MB we no longer need
    if progress:
        progress("done", 1, 1)
    return d


def ensure_llm(progress=None):
    """Fetch the homophone LLM. Optional by design: on failure we log and carry on, exactly as
    live._build_llm() does, because dictation is fully usable without it."""
    if progress:
        progress("downloading-llm", 0, None)
    import llm_backend
    path = llm_backend.prefetch_default_model()
    if progress:
        progress("done-llm", 1, 1)
    return path
