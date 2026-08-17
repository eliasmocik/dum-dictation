#!/usr/bin/env python3
"""
First-run tests - the GUI-free half.

first_run.py splits deliberately: the decisions (what to download, what to say, whether to
block) are pure logic and tested here on any machine; the AppKit window is a thin shell injected
as `window_factory`, so these run headless in CI with no display.

What matters most and is easy to get wrong:
  * the OPTIONAL model must never block the user from dictating,
  * a failed required download must NOT report success,
  * progress must be indeterminate for the phases that genuinely are,
  * an already-installed machine must not be asked anything at all.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import first_run as fr
import model_download as md

fail = 0


def check(name, cond):
    global fail
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fail = 1


class FakeWindow:
    def __init__(self):
        self.updates, self.closed, self.notes = [], False, []
    def update(self, text, frac): self.updates.append((text, frac))
    def note(self, t): self.notes.append(t)
    def close(self): self.closed = True


def installed_dir(root):
    d = Path(root) / md.PARAKEET_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    for f in md.PARAKEET_FILES:
        (d / f).write_bytes(b"x")
    return d


# --- describe(): determinate only where it should be --------------------------------------
t, f = fr.describe("downloading", 50 * 1024**2, 100 * 1024**2)
check("download progress is determinate", f == 0.5)
check("download progress names the phase and sizes", "Downloading speech model" in t and "MB" in t)

t, f = fr.describe("verifying", 0, None)
check("verifying is INDETERMINATE (no bar parked at 100%)", f is None)
t, f = fr.describe("extracting", 0, None)
check("extracting is INDETERMINATE", f is None)
check("extracting says so in words", "Extracting" in t)
t, f = fr.describe("done", 1, 1)
check("done is complete", f == 1.0)
t, _ = fr.describe("downloading-llm", 0, None)
check("the optional model is labelled optional", "optional" in t.lower())

# --- FirstRunPlan -------------------------------------------------------------------------
p = fr.FirstRunPlan("/tmp/x", asr_installed=False)
check("plan: missing ASR needs a download", p.needs_asr)
check("plan: missing ASR justifies a window", p.needs_window)
p2 = fr.FirstRunPlan("/tmp/x", asr_installed=True)
check("plan: installed ASR needs nothing", not p2.needs_asr)
check("plan: installed ASR shows NO window", not p2.needs_window)
check("plan: LLM alone never blocks with a window",
      not fr.FirstRunPlan("/tmp/x", llm_wanted=True, asr_installed=True).needs_window)

# --- the correction model downloads WITHOUT asking -------------------------------------------
# It used to open a modal on first launch. That question interrupted someone who had just
# installed the app, about a component they had no way to evaluate, and "no" only made the
# product worse - the model is what fixes git/get and grep/grab. It fetches on a daemon
# thread while dictation already works, so there is nothing to consent to but bandwidth.
import os as _os
_saved = _os.environ.pop("DUM_FETCH_LLM", None)
check("no prompt: the optional model is fetched by default", fr.llm_wanted_by_default())
_os.environ["DUM_FETCH_LLM"] = "0"
check("metered connections can still refuse it (DUM_FETCH_LLM=0)", not fr.llm_wanted_by_default())
for _v in ("false", "no", "0"):
    _os.environ["DUM_FETCH_LLM"] = _v
    check(f"DUM_FETCH_LLM={_v} refuses too", not fr.llm_wanted_by_default())
_os.environ["DUM_FETCH_LLM"] = "1"
check("DUM_FETCH_LLM=1 fetches", fr.llm_wanted_by_default())
_os.environ.pop("DUM_FETCH_LLM", None)
if _saved is not None:
    _os.environ["DUM_FETCH_LLM"] = _saved

# --- already installed: no prompt, no window, straight through ------------------------------
with tempfile.TemporaryDirectory() as td:
    installed_dir(td)
    asked = {"n": 0}
    win = FakeWindow()
    ok = fr.run_first_run(td, log=lambda *a: None,
                          ask=lambda: (asked.__setitem__("n", asked["n"] + 1), True)[1],
                          window_factory=lambda: win)
    check("installed: returns ready", ok)
    check("installed: does NOT re-ask about the optional model", asked["n"] == 0)
    check("installed: shows no window", not win.updates)

# --- genuine first run: asks once, shows progress, ends ready --------------------------------
with tempfile.TemporaryDirectory() as td:
    calls = {"asr": 0}
    def fake_ensure(models_dir, progress=None):
        calls["asr"] += 1
        for ph, cur, tot in (("downloading", 10, 100), ("downloading", 100, 100),
                             ("verifying", 0, None), ("extracting", 0, None), ("done", 1, 1)):
            progress(ph, cur, tot)
        installed_dir(models_dir)
        return Path(models_dir) / md.PARAKEET_DIRNAME
    orig_ensure, orig_llm = md.ensure_parakeet, md.ensure_llm
    md.ensure_parakeet = fake_ensure
    md.ensure_llm = lambda progress=None: "/fake/model.gguf"
    win = FakeWindow()
    ok = fr.run_first_run(td, log=lambda *a: None, ask=lambda: True,
                          window_factory=lambda: win)
    check("first run: returns ready", ok)
    check("first run: downloaded the ASR model", calls["asr"] == 1)
    check("first run: window saw progress", len(win.updates) >= 4)
    check("first run: window was closed", win.closed)
    phases = [t for t, _ in win.updates]
    check("first run: showed a verifying phase", any("Verifying" in t for t in phases))
    check("first run: showed an extracting phase", any("Extracting" in t for t in phases))
    md.ensure_parakeet, md.ensure_llm = orig_ensure, orig_llm

# --- declining the optional model still lets you dictate -------------------------------------
with tempfile.TemporaryDirectory() as td:
    llm_called = {"n": 0}
    orig_ensure, orig_llm = md.ensure_parakeet, md.ensure_llm
    md.ensure_parakeet = lambda d, progress=None: (installed_dir(d),
                                                   Path(d) / md.PARAKEET_DIRNAME)[1]
    md.ensure_llm = lambda progress=None: llm_called.__setitem__("n", llm_called["n"] + 1)
    ok = fr.run_first_run(td, log=lambda *a: None, ask=lambda: False,
                          window_factory=FakeWindow)
    time.sleep(0.2)
    check("declining the optional model still returns ready", ok)
    check("declining means it is never fetched", llm_called["n"] == 0)
    md.ensure_parakeet, md.ensure_llm = orig_ensure, orig_llm

# --- a FAILED required download must not report success ---------------------------------------
with tempfile.TemporaryDirectory() as td:
    orig_ensure = md.ensure_parakeet
    def boom(d, progress=None):
        raise md.DownloadError("network unreachable")
    md.ensure_parakeet = boom
    orig_alert = fr._alert_failure
    fr._alert_failure = lambda m: None            # don't pop a dialog in the test run
    win = FakeWindow()
    ok = fr.run_first_run(td, log=lambda *a: None, ask=lambda: False,
                          window_factory=lambda: win)
    check("a failed required download returns NOT ready", ok is False)
    check("the window is closed even on failure", win.closed)
    md.ensure_parakeet, fr._alert_failure = orig_ensure, orig_alert

# --- a GUI failure must never block setup -------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    orig_ensure, orig_llm = md.ensure_parakeet, md.ensure_llm
    md.ensure_parakeet = lambda d, progress=None: (installed_dir(d),
                                                   Path(d) / md.PARAKEET_DIRNAME)[1]
    md.ensure_llm = lambda progress=None: None
    def broken_window():
        raise RuntimeError("no display")
    ok = fr.run_first_run(td, log=lambda *a: None, ask=lambda: False,
                          window_factory=broken_window)
    check("a window that cannot open falls back to headless, still ready", ok)
    md.ensure_parakeet, md.ensure_llm = orig_ensure, orig_llm

# --- human() formatting --------------------------------------------------------------------
check("human() formats MB", fr.human(487 * 1024**2).endswith("MB"))
check("human() formats GB", fr.human(2 * 1024**3).endswith("GB"))
check("human() tolerates None", fr.human(None) == "?")

print("\n" + ("ALL CHECKS PASSED" if not fail else "SOME CHECKS FAILED"))
sys.exit(fail)
