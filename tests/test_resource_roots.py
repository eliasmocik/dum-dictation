#!/usr/bin/env python3
"""
Resource-root tests - the guard for shipping dum as a bundled .app.

Two roots, and conflating them is the bug this file exists to prevent:

  HERE       shipped, READ-ONLY (packs/, terms.txt, tests/). Frozen, that's sys._MEIPASS.
  USER_DATA  WRITABLE (models, later logs/telemetry). Frozen, that's ~/.dum - NEVER inside
             the .app, because writing into a signed bundle invalidates its signature, and
             macOS keys TCC permission grants to that signature. A user who loses it re-grants
             Microphone + Accessibility + Input Monitoring by hand.

The load-bearing property is PARITY: identical behaviour from a git checkout (where ./setup,
scripts/test, the bench and --replay all live) and frozen into a .app. So every check below
runs BOTH ways - the frozen side by re-importing the module in a subprocess with sys.frozen
and sys._MEIPASS faked, which is exactly what PyInstaller sets at runtime.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import model_utils

SRC = Path(__file__).resolve().parent.parent / "src"
fail = 0


def check(name, cond):
    global fail
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fail = 1


def probe(meipass=None, env=None):
    """Import model_utils in a fresh interpreter and report its roots.

    meipass != None fakes a PyInstaller onedir bundle: sys.frozen = True and sys._MEIPASS set,
    which is precisely what the bootloader does. We can't just monkeypatch in-process because
    the roots are module-level constants evaluated at import.
    """
    pre = ""
    if meipass is not None:
        pre = f"import sys; sys.frozen = True; sys._MEIPASS = {str(meipass)!r}\n"
    code = (pre + f"import sys; sys.path.insert(0, {str(SRC)!r})\n"
            "import model_utils as m\n"
            "print(m.HERE); print(m.USER_DATA); print(m.MODELS); print(m.FROZEN)\n")
    e = dict(os.environ)
    # Never let the developer's own overrides leak into the assertions.
    for k in ("DUM_DATA_DIR", "DUM_MODELS_DIR"):
        e.pop(k, None)
    e.update(env or {})
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=e)
    if out.returncode != 0:
        raise AssertionError(f"probe failed: {out.stderr.strip()}")
    here, user, models, frozen = out.stdout.strip().splitlines()
    return Path(here), Path(user), Path(models), frozen == "True"


REPO = SRC.parent

# --- 1) from a checkout: everything resolves to the repo, exactly as before ------------
here, user, models, frozen = probe()
check("checkout: not frozen", frozen is False)
check("checkout: HERE is the repo root", here == REPO)
check("checkout: USER_DATA is the repo root", user == REPO)
check("checkout: MODELS is <repo>/models", models == REPO / "models")
check("checkout: shipped resources resolve", (here / "terms.txt").exists() and (here / "packs").is_dir())

# --- 2) frozen: shipped resources follow _MEIPASS, writable state does NOT -------------
with tempfile.TemporaryDirectory() as td:
    mei = Path(td) / "Contents" / "Frameworks"
    mei.mkdir(parents=True)
    mei = mei.resolve()
    here, user, models, frozen = probe(meipass=mei)
    check("frozen: reports frozen", frozen is True)
    check("frozen: HERE follows sys._MEIPASS", here == mei)
    check("frozen: USER_DATA is ~/.dum", user == Path.home() / ".dum")
    check("frozen: MODELS is ~/.dum/models", models == Path.home() / ".dum" / "models")

    # THE bug this file exists to catch: anything writable landing inside the bundle.
    bundle = Path(td).resolve()
    check("frozen: USER_DATA is OUTSIDE the bundle", not str(user).startswith(str(bundle)))
    check("frozen: MODELS is OUTSIDE the bundle", not str(models).startswith(str(bundle)))

# --- 3) overrides work in BOTH modes --------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    d = (Path(td) / "state").resolve()
    here, user, models, _ = probe(env={"DUM_DATA_DIR": str(d)})
    check("checkout: DUM_DATA_DIR overrides USER_DATA", user == d)
    check("checkout: DUM_DATA_DIR moves MODELS with it", models == d / "models")

    mei = Path(td) / "mei"
    mei.mkdir(parents=True, exist_ok=True)
    mei = mei.resolve()
    _, user, models, _ = probe(meipass=mei, env={"DUM_DATA_DIR": str(d)})
    check("frozen: DUM_DATA_DIR overrides USER_DATA", user == d)

    m = (Path(td) / "just-models").resolve()
    _, user, models, _ = probe(env={"DUM_MODELS_DIR": str(m)})
    check("DUM_MODELS_DIR overrides MODELS alone", models == m)
    check("DUM_MODELS_DIR leaves USER_DATA alone", user == REPO)

# --- 4) HERE must NOT be used for writable state --------------------------------------
# A regression here means someone reintroduced `MODELS = HERE / "models"`, which silently
# works from a checkout and breaks only once frozen - the worst kind of bug to ship.
with tempfile.TemporaryDirectory() as td:
    mei = Path(td) / "Contents" / "Frameworks"
    mei.mkdir(parents=True)
    mei = mei.resolve()
    here, _, models, _ = probe(meipass=mei)
    check("frozen: MODELS is not derived from HERE", not str(models).startswith(str(here)))

# --- 5) the real repo still works today -----------------------------------------------
check("live module: HERE has terms.txt", (model_utils.HERE / "terms.txt").exists())
check("live module: MODELS points at the real model dir",
      model_utils.MODELS == REPO / "models")

print("\n" + ("ALL CHECKS PASSED" if not fail else "SOME CHECKS FAILED"))
sys.exit(fail)
