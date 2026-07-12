#!/usr/bin/env python3
"""
Always-on (opt-in) dogfood logger for the USER CORRECTION RATE metric. Local-only, privacy-conscious.

Default OFF. Enable with DUM_DOGFOOD_LOG=1. Writes one JSONL file per session under
dogfood/sessions/ (the whole dogfood/ tree is gitignored - it's your dictation history; delete with
`rm -rf dogfood/sessions`). Two event types, joinable by commit_id:

  commit     every committed dictation: session, ts, cwd, repo_root, app, surface (real bucket via
             classify_surface - terminal/editor/browser/rich-text/unknown), window_title
             (best-effort AX, may be null), mode, raw, fixed, model,
             flags{global_vocab,repo_vocab,fuzzy_symbols,llm}, latency_ms,
             stages_fired[{stage,before,after,ms}] (the embedded correction trace - which pipeline
             stage changed the text and how; the dogfood-analysis source), audio_ref{path,sha256,
             seconds} (Layer-1: pointer to the saved utterance WAV under dogfood/audio/, or null if
             audio retention is off). Derivable fields (changed=raw!=fixed, committed_len, n_words)
             are NOT stored - analyzers compute them from `fixed`.
  user.refix the post-commit behaviour signal (best-effort, BACKGROUND) - answers "did the user FIX
             the output, or move on?". capture_method (ax|keystroke|unavailable) + the AX edit signal
             where readable (edit_distance/normalized/accepted_unchanged + correction_pair, the
             minimal changed-token diff committed->corrected - the learning signal, opt-out via
             DUM_KEEP_CORRECTIONS), PLUS (every app) the activity timeline: commit_app,
             app_switches[{t_rel,app}], final_app,
             stayed_in_commit_app, switched_away_s, and a CONTENT-FREE keystroke_summary
             {backspaces,deletes,nav_keys,other_keys} gated to the commit app. AX whole-field reads
             that find an empty field (send-and-clear) are marked unavailable, not a giant edit.

PRIVACY: never logs the whole surrounding document - only the dictated text and a truncated window of
the edited region (REDACT_MAX chars). Keystroke proxy is COUNTS ONLY, never characters. App-switch
timeline is app NAMES only. No network, ever. See DOGFOOD.md.
"""
import os
import re
import json
import time
import difflib
import threading
import subprocess
import collections
from pathlib import Path

SCHEMA_VERSION = 5          # bumped per shape change: 2=stages_fired embedded; 3=real surface +
                            # window_title, derivables dropped; 4=audio_ref (Layer-1 audio retention);
                            # 5=surface buckets (terminals->"shell", VS Code family->coarse "vscode"
                            #   refined post-hoc to editor/vscode-terminal/agent-cli by the analyzer)
REDACT_MAX = 200            # max chars of any captured text span stored
OBSERVE_WINDOW_S = float(os.environ.get("DUM_DOGFOOD_WINDOW", "20"))
MODEL_NAME = "parakeet-tdt-0.6b-v3-int8"
# Audio retention (dogfood profile): save each utterance so any failure can be replayed/re-run
# offline in EVERY app (Layer-1 ground truth). Pruned oldest-first at session start by BOTH caps,
# whichever hits first. Local-only (dogfood/ is gitignored). See DOGFOOD.md.
AUDIO_MAX_DAYS = float(os.environ.get("DUM_AUDIO_MAX_DAYS", "30"))
AUDIO_MAX_GB = float(os.environ.get("DUM_AUDIO_MAX_GB", "2"))


def _env_on(name, default):
    """Boolean env flag: an explicit value wins; otherwise fall back to `default` (the profile)."""
    v = os.environ.get(name)
    return default if v is None else v not in ("0", "", "false")


# DUM_DOGFOOD_FULL = the dogfood MASTER switch. One flag turns the whole capture stack on (log,
# audio, keystroke proxy, correction pairs, fuzzy-symbol recovery). Each piece still has its own
# env override. Shipped builds leave it off => every piece defaults OFF (privacy-first); the
# ./dum launcher sets it for dogfood sessions.
DOGFOOD_FULL = os.environ.get("DUM_DOGFOOD_FULL", "0") not in ("0", "", "false")
# correction_pair = the verbatim changed-token diff committed->corrected (the core learning signal).
# Default = the profile (ON in dogfood, OFF/opt-in shipped); DUM_KEEP_CORRECTIONS overrides.
KEEP_CORRECTIONS = _env_on("DUM_KEEP_CORRECTIONS", DOGFOOD_FULL)

_WS = re.compile(r"\s+")


def _ws(s):
    return _WS.sub(" ", s or "").strip()


def _truncate(s):
    s = s or ""
    return s if len(s) <= REDACT_MAX else s[:REDACT_MAX] + "…"


def repo_root(cwd):
    try:
        return subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, check=True).stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def stages_fired(events):
    """Normalize the pipeline's correction events (one per stage that changed the text) into the
    embedded stage trace. Strips the bus-only `type` key; keeps stage/before/after/ms. Tolerates
    None (no stages passed) -> []. This is the per-commit superset of the old correction.applied
    stream: ordered, with before/after AND timing, joinable to the commit by commit_id."""
    return [{"stage": e.get("stage"), "before": e.get("before"),
             "after": e.get("after"), "ms": e.get("ms")} for e in (events or [])]


# app (macOS frontmost-process name, lowercased) -> insertion-surface bucket. Lets the analyzer
# answer "where does dictation quality break down" (shell vs editor vs browser vs rich-text).
# Unknown apps map to "unknown" - NEVER silently to a real bucket (the old hardcoded lie).
#
# NOTE on "vscode": the VS Code family is an Electron app where macOS Accessibility is blind AND a
# real editor doc, an integrated terminal, and a TUI (a coding-agent CLI) all share one app name. We CANNOT
# tell those apart at commit time, so live we emit the coarse parent "vscode"; the analyzer refines
# each "vscode" commit to editor | vscode-terminal | agent-cli using post-hoc evidence (a
# vscode-ext refix => editor; a agent-transcript join => agent-cli; else vscode-terminal).
_SURFACE = {
    # standalone terminal apps (NOT the VS Code integrated terminal - that's inside "vscode")
    "terminal": "shell", "iterm2": "shell", "iterm": "shell", "ghostty": "shell",
    "alacritty": "shell", "warp": "shell", "kitty": "shell", "wezterm": "shell", "tabby": "shell",
    # VS Code family - coarse parent; analyzer splits into editor / vscode-terminal / agent-cli
    "code": "vscode", "code - insiders": "vscode", "cursor": "vscode", "vscodium": "vscode",
    # other code editors / IDEs (AX-readable, single-surface) -> real editor docs
    "sublime text": "editor", "zed": "editor", "xcode": "editor", "pycharm": "editor", "nova": "editor",
    "intellij idea": "editor", "webstorm": "editor", "goland": "editor", "android studio": "editor", "atom": "editor",
    # browsers
    "safari": "browser", "safari technology preview": "browser", "google chrome": "browser", "chrome": "browser",
    "firefox": "browser", "arc": "browser", "brave browser": "browser", "microsoft edge": "browser",
    "chromium": "browser", "opera": "browser",
    # rich-text / messaging / notes / docs
    "notes": "rich-text", "mail": "rich-text", "slack": "rich-text", "discord": "rich-text",
    "messages": "rich-text", "textedit": "rich-text", "pages": "rich-text", "microsoft word": "rich-text",
    "notion": "rich-text", "obsidian": "rich-text", "bear": "rich-text", "craft": "rich-text",
    "whatsapp": "rich-text", "telegram": "rich-text", "microsoft outlook": "rich-text",
    "chatgpt": "rich-text", "claude": "rich-text",
}


def classify_surface(app):
    """Map a frontmost-app name to its insertion-surface bucket for telemetry slicing.
    The VS Code family -> coarse "vscode" (analyzer refines to editor/vscode-terminal/agent-cli).
    Unknown -> 'unknown' (so coverage gaps are visible, not masked as a real surface)."""
    return _SURFACE.get((app or "").strip().lower(), "unknown")


def feature_flags():
    def on(name):
        return os.environ.get(name, "0") not in ("0", "", "false")
    return {
        "global_vocab": True,                              # shipped pack is always loaded
        "repo_vocab": on("DUM_REPO_VOCAB"),
        "fuzzy_symbols": on("DUM_FUZZY_SYMBOLS"),
        "llm": "--llm" in " ".join(__import__("sys").argv),
    }


# ---- best-effort Accessibility read (cross-app focused text value) -----------
def _ax_focused_value():
    """Focused text-field value via Accessibility, or None if unreadable (untrusted process,
    app doesn't expose AXValue, etc.). Fully guarded - never raises."""
    try:
        import ApplicationServices as AS
        if hasattr(AS, "AXIsProcessTrusted") and not AS.AXIsProcessTrusted():
            return None
        sysel = AS.AXUIElementCreateSystemWide()
        err, focused = AS.AXUIElementCopyAttributeValue(sysel, AS.kAXFocusedUIElementAttribute, None)
        if err != 0 or focused is None:
            return None
        err2, val = AS.AXUIElementCopyAttributeValue(focused, AS.kAXValueAttribute, None)
        if err2 != 0 or not isinstance(val, str):
            return None
        return val
    except Exception:
        return None


def _ax_window_title():
    """Best-effort title of the focused window via Accessibility, or None. Fully guarded - never
    raises. Helps locate/explain a failure (which file/page/doc). Read AFTER the commit is applied,
    so it's off the perceived-latency path. Truncated like every captured span."""
    try:
        import ApplicationServices as AS
        if hasattr(AS, "AXIsProcessTrusted") and not AS.AXIsProcessTrusted():
            return None
        sysel = AS.AXUIElementCreateSystemWide()
        err, focused = AS.AXUIElementCopyAttributeValue(sysel, AS.kAXFocusedUIElementAttribute, None)
        if err != 0 or focused is None:
            return None
        errw, win = AS.AXUIElementCopyAttributeValue(focused, AS.kAXWindowAttribute, None)
        if errw != 0 or win is None:
            return None
        errt, title = AS.AXUIElementCopyAttributeValue(win, AS.kAXTitleAttribute, None)
        if errt != 0 or not isinstance(title, str) or not title.strip():
            return None
        return _truncate(title)
    except Exception:
        return None


def _trim_field_bleed(committed, region):
    """Anchor the aligned field region to the COMMITTED text's word-extent. When a short commit is
    aligned inside a long field (the full submitted message / a multi-commit editor buffer), the
    partial-match + word-snap can grab a NEIGHBOUR commit's words on either side ("...you know?" +
    bled "Should"; bled "anymore." + "So nothing changed"). Those words are not part of THIS commit -
    a correction is a change WITHIN the dictated text, not content appended/prepended beyond it. Drop
    leading/trailing region words that fall outside the committed span; internal changes are untouched.
    Both the edit_distance and the changed-span are then computed on the clean region."""
    import difflib
    cw, rw = committed.split(), region.split()
    if not cw or not rw:
        return region
    ops = difflib.SequenceMatcher(a=cw, b=rw).get_opcodes()
    lo = next((j1 for tag, i1, i2, j1, j2 in ops if i2 > i1), 0)                    # first region word
    hi = next((j2 for tag, i1, i2, j1, j2 in reversed(ops) if i2 > i1), len(rw))    # ...anchored to committed
    return " ".join(rw[lo:hi])


def _changed_span(committed, corrected):
    """The minimal CHANGED token span on each side - the actual `committed -> corrected` learning
    signal (e.g. "postgress"->"PostgreSQL"), not the whole field. Bounding run from the first
    differing word to the last. Returns (committed_span, corrected_span) or None if nothing differs."""
    import difflib
    cw, fw = committed.split(), corrected.split()
    a_lo = a_hi = b_lo = b_hi = None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=cw, b=fw).get_opcodes():
        if tag != "equal":
            if a_lo is None:
                a_lo, b_lo = i1, j1
            a_hi, b_hi = i2, j2
    if a_lo is None:
        return None
    return (" ".join(cw[a_lo:a_hi]), " ".join(fw[b_lo:b_hi]))


_ALNUM = re.compile(r"[^a-z0-9]")


def _alnum(s):
    return _ALNUM.sub("", (s or "").lower())


def _is_subsequence(a, b):
    """True if every character of `a` appears in `b` in order (b may have extra chars interspersed)."""
    it = iter(b)
    return all(ch in it for ch in a)


def _dice_chars(a, b):
    """Sørensen–Dice over the two strings' CHARACTER multisets - 1.0 == identical letter content,
    regardless of order. High Dice means 'no genuinely new content, the same letters rearranged'."""
    ca, cb = collections.Counter(a), collections.Counter(b)
    inter = sum((ca & cb).values())
    n = len(a) + len(b)
    return (2 * inter / n) if n else 0.0


def _nontrivial_changed_tokens(a, b):
    """# of word positions whose ALNUM content actually changed. Pure case/punctuation diffs
    (`Code`->`code`, `well.`->`well,`) do NOT count - only real letter/digit changes - so a genuine
    one-word fix wrapped in trailing-punctuation churn still reads as a single change, not many."""
    aw, bw = a.split(), b.split()
    n = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=aw, b=bw).get_opcodes():
        if tag == "equal":
            continue
        if _alnum(" ".join(aw[i1:i2])) != _alnum(" ".join(bw[j1:j2])):
            n += max(i2 - i1, j2 - j1)
    return n


def classify_correction(committed_span, corrected_span):
    """Classify a committed->corrected diff so the telemetry can be TRUSTED. The capture layer (AX
    read-back, agent-transcript join, vscode-ext) faithfully records what landed in the field - but
    that field can hold three very different things, which earlier code lumped together as one
    'correction', inflating the user-correction rate and polluting vocab candidates:

      * 'clean'    - a genuine correction: word(s) changed to DIFFERENT word(s) (`cloud code`->`Claude
                     Code`, `joint`->`join`, `Jetson`->`Rado's`), OR a meaningful join/split of a
                     technical term that keeps the letters but moves word boundaries (`git hub`->
                     `GitHub`, `web socket`->`WebSocket`). THE learning signal.
      * 'trivial'  - same letters, same word boundaries: an in-place punctuation/case-only diff
                     (`Check.`->`Check`, `Global`->`global`). A real but low-value edit - counts toward
                     the correction rate (the user did change a char) but is NOT a vocab candidate. Also
                     where letter-identical overlay punctuation-shuffles land (`two. However,`->`two.,
                     however`): indistinguishable from a legit trim by letters alone, so reported as
                     low-signal rather than guessed either way.
      * 'scramble' - the overlay/AX captured a CHARACTER-SHUFFLE of the same text: letters preserved,
                     sequence/spacing churned (`service SH SSHD`->`Sesvice s SHSHD`, `tool. Uh`->
                     `too.l Uhhh`). This is dum's own terminal/TUI insertion-corruption bug (Part C),
                     or an AX read taken mid-edit - NEVER a user correction.
      * 'bleed'    - a neighbour commit merged in at an edge (accumulation): the committed text
                     survives intact inside a notably longer corrected text (`Everything else...`->
                     `jumper.Everything else...`). Not a correction of THIS text.

    Conservative: returns 'clean' whenever unsure, so a real correction is never silently dropped."""
    a, b = (committed_span or "").strip(), (corrected_span or "").strip()
    la, lb = _alnum(a), _alnum(b)
    if not la or not lb:
        return "clean"
    if la == lb:
        # same letter sequence: a join/split that changes how many ALNUM-bearing tokens there are is a
        # real fix (`git hub`->`GitHub` 2->1, `web socket`->`WebSocket`). A diff that only moves
        # case/punctuation - including a bad-space split that spawns a pure-punctuation token
        # (`weird.`->`weird .`) - leaves the alnum-token count unchanged and is trivial low-signal noise.
        def _alnum_tokens(s):
            return sum(1 for w in s.split() if _alnum(w))
        return "clean" if _alnum_tokens(a) != _alnum_tokens(b) else "trivial"
    # bleed / accumulation: the committed text survives as an ordered SUBSEQUENCE of the corrected one
    # (its letters still appear, in order - possibly with words inserted/appended) while the corrected
    # is meaningfully longer => content was added (a neighbour commit merged, or the user just kept
    # writing), NOT a scramble of THIS text. The order-preservation is the key tell that separates this
    # from a true scramble (`Tool that I use.`->`tool that I use for` survives; `of brew`->`ofbBre`
    # does not). The reverse (corrected is a subsequence of committed) catches trailing-content trims.
    if len(lb) - len(la) >= 3 and _is_subsequence(la, lb):
        return "bleed"
    if len(la) - len(lb) >= 3 and _is_subsequence(lb, la):
        return "bleed"
    # scramble: the same characters, rearranged. The signature is HIGH char-multiset overlap AND
    # broad disruption - either the word count changed (spaces mangled) or >=3 word positions changed
    # at once (a localized real fix touches one, maybe two). High Dice rules out genuine word swaps,
    # which introduce new letters and so score low.
    if _dice_chars(la, lb) >= 0.82 and (
            len(a.split()) != len(b.split()) or _nontrivial_changed_tokens(a, b) >= 3):
        return "scramble"
    return "clean"


def edit_signal(inserted, final_value):
    """Compare the dictated text to the focused field after the observation window. Returns
    edit_distance / normalized / accepted_unchanged, and (when KEEP_CORRECTIONS) a correction_pair -
    the minimal changed-token diff committed->corrected, the core V2/V3 learning signal. Bounded:
    aligns the dictated text within the field (best partial match), edit-distances only that region,
    and stores only the changed span - never the whole field."""
    from rapidfuzz import fuzz
    from rapidfuzz.distance import Levenshtein
    ins, fv = _ws(inserted), _ws(final_value)
    if not ins:
        return {"edit_capture": "ok", "accepted_unchanged": True, "edit_distance": 0, "normalized": 0.0}
    if not fv:
        # field is EMPTY after the window - almost always send-and-clear / navigation (ChatGPT,
        # Safari, chat inputs), NOT a real correction. Don't fabricate a giant edit; mark it
        # unobservable so it can't inflate the correction rate. (Surfaced by live dogfood data.)
        return {"edit_capture": "unavailable", "reason": "field_empty"}
    if ins in fv:
        return {"edit_capture": "ok", "accepted_unchanged": True, "edit_distance": 0, "normalized": 0.0}
    al = fuzz.partial_ratio_alignment(ins, fv)
    ds, de = (al.dest_start, al.dest_end) if al else (max(0, len(fv) - 3 * len(ins)), len(fv))
    while ds > 0 and fv[ds - 1] != " ":      # snap to whole words so the changed-span diff isn't
        ds -= 1                               # polluted by a mid-word cut (e.g. "now" -> "no")
    while de < len(fv) and fv[de] != " ":
        de += 1
    region = _trim_field_bleed(ins, fv[ds:de])     # drop neighbour-commit bleed before scoring
    dist = Levenshtein.distance(ins, region)
    out = {"edit_capture": "ok", "accepted_unchanged": dist == 0, "edit_distance": dist,
           "normalized": round(dist / max(1, len(ins)), 3)}
    if dist > 0 and KEEP_CORRECTIONS:
        pair = _changed_span(ins, region)
        if pair:
            out["correction_pair"] = {"committed_span": _truncate(pair[0]),
                                      "corrected_span": _truncate(pair[1])}
            # tag what KIND of diff this is so downstream metrics can trust it: a genuine correction
            # vs dum's own insertion scramble vs neighbour-commit bleed. Re-derivable from the pair
            # (the analyzer recomputes for historical logs), stored here so live logs self-describe.
            out["pair_kind"] = classify_correction(pair[0], pair[1])
    return out


class _EditObserver:
    """Spawns one daemon thread per commit. Over the observation window it gathers the answer to
    'did the user FIX the output, or move on?' from three signals, best-effort, never blocking:
      * AX text edit (native apps only) -> exact edit_distance / spans
      * keystroke proxy (most apps)     -> did they actually edit (backspaces), gated to commit app
      * app-switch timeline (all apps)  -> did they stay and edit, or switch away to another task
    capture_method records which EDIT signal was available (ax | keystroke | unavailable); the
    app-switch timeline is recorded regardless."""
    def __init__(self, window_s=OBSERVE_WINDOW_S, monitor=None, shutdown=None):
        self.window_s = window_s
        self.monitor = monitor
        self.shutdown = shutdown        # Event set on process exit -> wake early and flush

    def observe(self, commit_id, inserted, commit_app, t0, write):
        t = threading.Thread(target=self._run, args=(commit_id, inserted, commit_app, t0, write),
                             daemon=True)
        t.start()
        return t

    def _run(self, commit_id, inserted, commit_app, t0, write):
        v0 = _ax_focused_value()
        if self.shutdown is not None:
            self.shutdown.wait(self.window_s)       # returns early on exit -> flush the partial window
        else:
            time.sleep(self.window_s)
        v1 = _ax_focused_value() if v0 is not None else None
        evt = {"type": "user.refix", "commit_id": commit_id, "commit_app": commit_app}
        if self.monitor is not None:
            try:
                evt.update(self.monitor.window(t0, time.time(), commit_app))
            except Exception:
                pass
        sig = edit_signal(inserted, v1) if (v0 is not None and v1 is not None) \
            else {"edit_capture": "unavailable"}
        evt.update(sig)
        had_keys = any((evt.get("keystroke_summary") or {}).values())
        evt["capture_method"] = ("ax" if sig.get("edit_capture") == "ok"
                                 else "keystroke" if had_keys else "unavailable")
        write(evt)


class DogfoodLogger:
    def __init__(self, path=None, window_s=OBSERVE_WINDOW_S, frontmost_fn=None):
        # Each flag defaults to the dogfood profile (DUM_DOGFOOD_FULL); its own env var overrides.
        # => one master switch on in dogfood, everything OFF when shipped.
        self.enabled = _env_on("DUM_DOGFOOD_LOG", DOGFOOD_FULL)
        self.keep_audio = _env_on("DUM_KEEP_AUDIO", DOGFOOD_FULL)
        self.keystroke_proxy = _env_on("DUM_KEYSTROKE_PROXY", DOGFOOD_FULL)
        # announce editor commits to a running dum VS Code extension (exact post-commit edit
        # capture from the document model -> closes the AX-blind VS Code telemetry gap). OFF unless
        # the extension is installed and DUM_VSCODE_BRIDGE=1.
        self.vscode_bridge = _env_on("DUM_VSCODE_BRIDGE", False)
        self.session = f"{int(time.time())}-{os.getpid()}"
        self.cwd = os.getcwd()
        self.repo = repo_root(self.cwd)
        self.flags = feature_flags()
        self.path = Path(path or os.environ.get("DUM_DOGFOOD_PATH")
                         or (Path(self.cwd) / "dogfood" / "sessions" / f"dictation-{self.session}.jsonl"))
        self._audio_dir = self.path.parent.parent / "audio"     # dogfood/audio/<session>/
        self._n = 0
        self._last_commit_id = None             # for the "flag last dictation as a problem" hotkey
        self._lock = threading.Lock()
        self._shutdown = threading.Event()      # set by close() -> flush pending observers on exit
        self._threads = []                      # pending observer threads (for exit flush)
        # session-level activity monitor (app-switch timeline + content-free keystroke proxy);
        # only when enabled and we have a frontmost-app source. Fully guarded.
        self._monitor = None
        if self.enabled and frontmost_fn is not None:
            try:
                from activity_monitor import ActivityMonitor
                self._monitor = ActivityMonitor(frontmost_fn, keystrokes=self.keystroke_proxy)
                self._monitor.start()
            except Exception:
                self._monitor = None
        self._observer = _EditObserver(window_s, monitor=self._monitor, shutdown=self._shutdown)
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._prune_audio()                                  # bound disk once per session

    def _write(self, evt):
        if not self.enabled:
            return
        evt.setdefault("ts", time.time())
        try:
            with self._lock, open(self.path, "a") as f:
                f.write(json.dumps(evt) + "\n")
        except Exception:
            pass                                           # logging must never break dictation

    def _save_audio(self, cid, audio, sr):
        """Write the utterance WAV under dogfood/audio/<session>/ and return its audio_ref
        {path, sha256, seconds}, or None. Guarded - audio capture must never break dictation.
        Runs post-apply (off the perceived-latency path)."""
        try:
            import hashlib
            import numpy as np
            import soundfile as sf
            adir = self._audio_dir / self.session
            adir.mkdir(parents=True, exist_ok=True)
            fpath = adir / f"{cid}.wav"
            arr = np.asarray(audio)
            sf.write(str(fpath), arr, sr, subtype="PCM_16")     # 16-bit -> ~32KB/s
            try:
                rel = str(fpath.relative_to(self.cwd))
            except ValueError:
                rel = str(fpath)
            return {"path": rel, "sha256": hashlib.sha256(arr.tobytes()).hexdigest(),
                    "seconds": round(len(arr) / sr, 3)}
        except Exception:
            return None

    def _prune_audio(self, max_days=None, max_gb=None):
        """Bound dogfood/audio/: delete clips older than max_days, then (oldest-first) any beyond
        max_gb total. Both caps, whichever bites. Writes an audio.prune event if anything was
        dropped (coverage honesty). Guarded - runs once at session start, never raises."""
        max_days = AUDIO_MAX_DAYS if max_days is None else max_days
        max_bytes = (AUDIO_MAX_GB if max_gb is None else max_gb) * 1024 ** 3
        try:
            root = self._audio_dir
            if not root.exists():
                return
            dropped_age = dropped_size = 0
            now = time.time()
            for p in list(root.rglob("*.wav")):                 # age cap
                try:
                    if now - p.stat().st_mtime > max_days * 86400:
                        p.unlink(); dropped_age += 1
                except Exception:
                    pass
            files = sorted(root.rglob("*.wav"), key=lambda p: p.stat().st_mtime)  # size cap, oldest first
            total = sum(p.stat().st_size for p in files)
            for p in files:
                if total <= max_bytes:
                    break
                try:
                    total -= p.stat().st_size; p.unlink(); dropped_size += 1
                except Exception:
                    pass
            if dropped_age or dropped_size:
                self._write({"type": "audio.prune", "dropped_age": dropped_age,
                             "dropped_size": dropped_size, "max_days": max_days, "max_gb": max_bytes / 1024 ** 3})
        except Exception:
            pass

    def commit_event(self, raw, fixed, app, mode, latency_ms=None, stages=None):
        """Build the commit record (also returned so callers/tests can inspect it). `surface` is
        derived from `app` and `window_title` is read best-effort (AX) - both here, not passed in.
        `stages` is the pipeline's correction-event list (CorrectionPipeline.run), embedded as
        stages_fired so the stage trace lives on the commit row. `audio_ref` is set later by
        log_commit (file I/O kept out of this pure builder). Derivable fields are NOT stored."""
        cid = f"{self.session}-{self._n}"
        self._n += 1
        return cid, {
            "schema": SCHEMA_VERSION, "type": "commit", "commit_id": cid, "session": self.session,
            "cwd": self.cwd, "repo_root": self.repo, "app": app,
            "surface": classify_surface(app), "window_title": _ax_window_title(), "mode": mode,
            "raw": raw, "fixed": fixed, "model": MODEL_NAME, "flags": self.flags,
            "latency_ms": (round(latency_ms) if latency_ms is not None else None),
            "stages_fired": stages_fired(stages), "audio_ref": None,
        }

    def log_commit(self, raw, fixed, app, mode, latency_ms=None, stages=None, audio=None, sr=16000):
        """Log a commit (+ saved audio + best-effort background edit observation). Non-blocking."""
        if not self.enabled:
            return
        t0 = time.time()                                         # window anchor for app-switch/keystrokes
        cid, evt = self.commit_event(raw, fixed, app, mode, latency_ms, stages)
        self._last_commit_id = cid                               # target for the flag-problem hotkey
        if self.keep_audio and audio is not None:
            evt["audio_ref"] = self._save_audio(cid, audio, sr)
        self._write(evt)
        if self.vscode_bridge and evt.get("surface") == "vscode":
            self._announce_vscode(cid, fixed)
        try:
            t = self._observer.observe(cid, fixed, app, t0, self._write)
            self._threads = [x for x in self._threads if x.is_alive()]   # prune finished
            if t is not None:
                self._threads.append(t)
        except Exception:
            pass

    def _announce_vscode(self, cid, text):
        """Best-effort: tell a running dum VS Code extension we just inserted `text` for commit
        `cid`, so it can measure the EXACT post-commit edit from the document model (the AX-blind VS
        Code gap). One JSON line to ~/.dum/vscode-bridge.jsonl, which the extension tails; it writes
        its own user.refix (capture_method=vscode-ext) into sessions_dir. Fully guarded - the bridge
        must never affect dictation."""
        try:
            d = Path.home() / ".dum"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "vscode-bridge.jsonl", "a") as f:
                f.write(json.dumps({"commit_id": cid, "text": text, "ts": time.time(),
                                    "sessions_dir": str(self.path.parent), "session": self.session}) + "\n")
        except Exception:
            pass

    def flag_problem(self):
        """Flag the LAST committed dictation as a problem to revisit later (double-tap left Option).
        Writes a user.verdict event joined by commit_id; the saved audio + correction context make
        it reviewable offline. Returns the flagged commit_id, or None if there's nothing to flag."""
        if not self.enabled or not self._last_commit_id:
            return None
        self._write({"type": "user.verdict", "commit_id": self._last_commit_id, "verdict": "problem"})
        return self._last_commit_id

    def record_key(self, category):
        """Feed one content-free keystroke category to the activity monitor (from the app's single
        keyboard listener). No-op if no monitor. Guarded - never breaks the listener."""
        if self._monitor is not None:
            try:
                self._monitor.record_key(category)
            except Exception:
                pass

    def mark_self_typing(self, t0, t1):
        """Tell the activity monitor that dum was inserting/reconciling text in [t0, t1] so its
        own synthetic keystrokes aren't counted as user edits. No-op if no monitor. Guarded."""
        if self._monitor is not None:
            try:
                self._monitor.mark_self_typing(t0, t1)
            except Exception:
                pass

    def close(self):
        """Flush pending post-commit observers so quitting doesn't lose the last commits' refix:
        wake them from their wait (they write whatever they have for the partial window) and join
        briefly. Bounded - never hangs the exit. Call once at process exit."""
        self._shutdown.set()
        for t in list(self._threads):
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
