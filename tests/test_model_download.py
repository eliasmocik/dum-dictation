#!/usr/bin/env python3
"""
First-run model download tests - all offline, via the _urlopen seam.

What these guard is not "does urllib work" but the failure modes that only show up on a real
user's connection and are invisible in a happy-path test: a quit mid-download, a server that
ignores Range and replays the whole file, a stale .part the server rejects with 416, and a
truncated transfer. Every one of those, unhandled, produces a CORRUPT model that fails much
later at extraction - or worse, a half-extracted directory that reads as installed.
"""
import io
import os
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import model_download as md

fail = 0


def check(name, cond):
    global fail
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fail = 1


class FakeResp:
    """Minimal urlopen stand-in: serves `body`, honouring Range unless told to ignore it."""
    def __init__(self, body, status=200, content_range=None):
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body))}
        if content_range:
            self.headers["Content-Range"] = content_range

    def read(self, n=-1):
        return self._buf.read(n)


def serve(body, *, ignore_range=False, fail_416=False):
    """Build an _urlopen replacement over `body`."""
    def _open(req, timeout):
        rng = req.headers.get("Range") or req.headers.get("range")
        if rng and fail_416:
            raise __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
                req.full_url, 416, "Range Not Satisfiable", {}, None)
        if rng and not ignore_range:
            start = int(rng.split("=")[1].split("-")[0])
            return FakeResp(body[start:], status=206,
                            content_range=f"bytes {start}-{len(body)-1}/{len(body)}")
        return FakeResp(body, status=200)
    return _open


BODY = bytes(range(256)) * 400          # 102,400 bytes, verifiable content
orig_urlopen = md._urlopen

# --- 1) plain download -------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    md._urlopen = serve(BODY)
    dest = Path(td) / "m.bin"
    md.download_file("http://x/m.bin", dest)
    check("plain download writes the whole file", dest.read_bytes() == BODY)
    check("no .part left behind", not dest.with_suffix(".bin.part").exists())

# --- 2) resume: a partial .part is continued, not restarted -------------------------------
with tempfile.TemporaryDirectory() as td:
    dest = Path(td) / "m.bin"
    part = dest.with_suffix(".bin.part")
    part.write_bytes(BODY[:40_000])                    # simulate a quit mid-download
    md._urlopen = serve(BODY)
    md.download_file("http://x/m.bin", dest)
    check("resume produces the correct complete file", dest.read_bytes() == BODY)

# --- 3) THE trap: a server that ignores Range and replays the WHOLE file -------------------
# Appending that to an existing .part yields a corrupt archive that only fails at extraction.
with tempfile.TemporaryDirectory() as td:
    dest = Path(td) / "m.bin"
    dest.with_suffix(".bin.part").write_bytes(BODY[:40_000])
    md._urlopen = serve(BODY, ignore_range=True)
    md.download_file("http://x/m.bin", dest)
    check("server ignoring Range -> restart, not corrupt append", dest.read_bytes() == BODY)

# --- 4) 416 on a stale .part -> discard and start clean ------------------------------------
with tempfile.TemporaryDirectory() as td:
    dest = Path(td) / "m.bin"
    dest.with_suffix(".bin.part").write_bytes(b"\x00" * 999_999)   # longer than the asset
    calls = {"n": 0}
    inner = serve(BODY)

    def _open(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:                                        # first (ranged) attempt 416s
            raise __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
                req.full_url, 416, "Range Not Satisfiable", {}, None)
        return inner(req, timeout)
    md._urlopen = _open
    md.download_file("http://x/m.bin", dest)
    check("416 on a stale .part -> clean restart", dest.read_bytes() == BODY)

# --- 5) truncated transfer is rejected, not silently accepted ------------------------------
with tempfile.TemporaryDirectory() as td:
    dest = Path(td) / "m.bin"

    def _short(req, timeout):
        r = FakeResp(BODY[:1000])
        r.headers["Content-Length"] = str(len(BODY))               # claims more than it sends
        return r
    md._urlopen = _short
    try:
        md.download_file("http://x/m.bin", dest)
        check("truncated download raises", False)
    except md.DownloadError:
        check("truncated download raises", True)
    check("no partial file left at the destination", not dest.exists())

# --- 6) progress reports the documented phases ---------------------------------------------
with tempfile.TemporaryDirectory() as td:
    md._urlopen = serve(BODY)
    seen = []
    md.download_file("http://x/m.bin", Path(td) / "m.bin",
                     progress=lambda ph, cur, tot: seen.append(ph))
    check("progress emits the 'downloading' phase", "downloading" in seen)

md._urlopen = orig_urlopen

# --- 7) is_installed() demands the FULL layout ---------------------------------------------
# A half-extracted archive reading as "installed" would send a broken app to first dictation.
with tempfile.TemporaryDirectory() as td:
    models = Path(td)
    check("empty dir is not installed", not md.is_installed(models))
    d = models / md.PARAKEET_DIRNAME
    d.mkdir()
    check("empty model dir is not installed", not md.is_installed(models))
    for f in md.PARAKEET_FILES[:-1]:
        (d / f).write_bytes(b"x")
    check("missing one file is not installed", not md.is_installed(models))
    (d / md.PARAKEET_FILES[-1]).write_bytes(b"x")
    check("complete layout IS installed", md.is_installed(models))

# --- 8) extraction validates the result ----------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    models = Path(td) / "models"
    models.mkdir()
    bogus = models / "bogus.tar.bz2"
    with tarfile.open(bogus, "w:bz2") as tf:                        # valid archive, wrong content
        p = Path(td) / "junk.txt"
        p.write_text("nope")
        tf.add(p, arcname="junk.txt")
    try:
        md.extract_parakeet(bogus, models)
        check("extract rejects an archive missing the model files", False)
    except md.DownloadError:
        check("extract rejects an archive missing the model files", True)

    corrupt = models / "corrupt.tar.bz2"
    corrupt.write_bytes(b"not a bzip2 archive at all")
    try:
        md.extract_parakeet(corrupt, models)
        check("extract rejects a corrupt archive", False)
    except md.DownloadError:
        check("extract rejects a corrupt archive", True)

# --- 9) ensure_parakeet is idempotent and precheck-guarded ---------------------------------
with tempfile.TemporaryDirectory() as td:
    models = Path(td)
    d = models / md.PARAKEET_DIRNAME
    d.mkdir()
    for f in md.PARAKEET_FILES:
        (d / f).write_bytes(b"x")
    called = {"n": 0}
    orig = md.download_file
    md.download_file = lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    md.ensure_parakeet(models)
    md.download_file = orig
    check("ensure_parakeet is a no-op when already installed", called["n"] == 0)

with tempfile.TemporaryDirectory() as td:
    orig_free = md.free_bytes
    md.free_bytes = lambda p: 100 * 1024**2          # 100 MB - far too little
    try:
        md.ensure_parakeet(Path(td))
        check("ensure_parakeet refuses on low disk", False)
    except md.DownloadError as e:
        check("ensure_parakeet refuses on low disk", "disk space" in str(e))
    md.free_bytes = orig_free

print("\n" + ("ALL CHECKS PASSED" if not fail else "SOME CHECKS FAILED"))
sys.exit(fail)
