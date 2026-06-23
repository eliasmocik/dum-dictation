#!/usr/bin/env python3
"""
MVP-0 (Path A) — the LIVE dictation app.

Continuous mic capture -> energy VAD (sentence boundaries at pauses) ->
streaming Parakeet (growing-window, reset per sentence) -> the correction
pipeline (phonetic + optional gated LLM + the inert paid seam) -> paste the
COMMITTED sentence at the cursor.

This is the real-time sibling of prototype.py: the prototype proved the loop on
WAV files (silence-split + growing-window streaming + dictionary correction);
this drives the exact same core from a live microphone and types into whatever
app is focused — made for dictating into the VS Code terminal to drive Claude
Code. It reuses dum's paste-at-cursor + beep trick.

Design notes:
  * The audio callback only ENQUEUES frames; a single consumer thread does all
    transcription, so the recognizer is touched from one thread and capture can
    never block. If transcription falls behind, previews are skipped (never
    audio) — the commit transcription always runs.
  * VAD is an adaptive noise-floor energy gate (zero extra deps, same idea as
    prototype.segment_by_silence). Speech = dBFS > floor + margin. A sentence
    commits after MIN_SIL_S of trailing silence, or at MAX_SEG_S (bounded compute).
  * Preview is logged to the terminal only — it is NOT pasted (typing+deleting a
    flickering preview into a shell is hostile). Only corrected, committed
    sentences are pasted.

Run (immediate, safe — log only, nothing pasted):
    .venv/bin/python live.py --no-paste
Run (paste at cursor, immediate continuous listen until Ctrl+C):
    .venv/bin/python live.py
Run (toggle daemon: tap the hotkey to start/stop continuous dictation):
    .venv/bin/python live.py --hotkey
Run (macOS-style: double-tap LEFT Command to start/stop, globally):
    .venv/bin/python live.py --double-cmd --overlay --llm
Run (word-by-word live overlay — types as you speak, reconciles on pause):
    .venv/bin/python live.py --overlay
Run (overlay DRY — prints the type/backspace ops, types nothing; safe to watch):
    .venv/bin/python live.py --overlay --no-paste
Options: --overlay  --llm  --mic <idx|name>  --list-devices  --margin <dB>

Env (shared with dum where it makes sense):
    DUM_MIC / DICTATE_MIC   mic index or name substring (default: system default)
    DUM_HOTKEY              global toggle key (default <ctrl>+<alt>+d)
    DUM_VAD_MARGIN         dB above noise floor counted as speech (default 12)
    DUM_MIN_SIL            seconds of silence that ends a sentence (default 0.6)
    DUM_VOCAB_DIR          extra *.txt vocab packs       (SEAM 2)
    DUM_EVENTS             append-only JSONL event sink   (SEAM 3)
    DUM_EXTERNAL_CORRECTOR paid corrector command (stdio) (SEAM 1; unset = off)
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sherpa_onnx

from model_utils import find_model_dir, pick, HERE
from correct_phonetic import PhoneticCorrector
from vocab import load_terms, load_phrase_aliases
from pipeline import (CorrectionPipeline, PunctuationStage, PhoneticStage, LLMStage,
                      ExternalCorrectorStage, PersonalCorrectionStage, SentenceCapStage,
                      FuzzySymbolStage, ProtectedWordsStage, clean_punct,
                      strip_fillers, drop_fillers, decap_interior, _ends_sentence)
from events import EventBus
from dogfood_log import DogfoodLogger
from overlay import (OverlayTyper, streaming_prefix, stable_prefix, reconcile_words, age_stable_count,
                     alias_prefix_set, hold_alias_prefix)
from platform_io import get_platform
from trace import Tracer

# --- audio / VAD / streaming parameters ---------------------------------------
SR = 16000
BLOCK_S = 0.10                       # mic callback granularity
# Defaults tuned 2026-06-15 from recorded latency sessions (see sessions/ + LATENCY-FINDINGS):
# STEP 0.30->0.20->0.10 (0.10 finally FELT word-by-word in the A/B test; only affordable
#   because lock-and-trim caps preview proc at ~70ms, so a 100ms cadence isn't compute-bound
#   — the 0.15-was-saturating worry no longer holds with a bounded window),
# MIN_SEG 0.40->0.20 (first preview starts sooner -> first word appears sooner),
# MIN_SIL 0.60->0.45 (shorter pause to commit; never observed clipping mid-sentence).
STEP_S = float(os.environ.get("DUM_STEP", 0.10))  # preview re-transcribe cadence (lower = snappier overlay, more compute)
MIN_SIL_S = float(os.environ.get("DUM_MIN_SIL", 0.45))   # silence that ends a sentence
MIN_SEG_S = float(os.environ.get("DUM_MIN_SEG", 0.20))  # ignore blips shorter than this; also gates first preview
# Max backspaces a LIVE (mid-speech) overlay correction may make. Small edits — the eager
# word-0 flash fix, a 1-word tweak early in the sentence — apply live; a big tail rewrite
# (the model revised an early word once many words are typed) would thrash the whole line,
# so it's deferred to the single commit reconcile that happens anyway. ~2 words of chars.
STREAM_FIX_MAX = int(os.environ.get("DUM_STREAM_FIX_MAX", 12))
# First-word policy: prefer a CONFIRMED word (two previews agree -> no wrong-word flash),
# but if nothing has been shown yet after this many seconds of audio, show the current best
# guess anyway so the first word never stalls. 0.0 = pure eager (instant but flashy);
# higher = wait longer for confidence. --eager sets this to 0.
EAGER_AFTER = float(os.environ.get("DUM_EAGER_AFTER", 0.5))
# Milestone B step 2: run the instant deterministic corrector (phrase/dictionary aliases,
# no LLM) on each PREVIEW too, not just at commit, so known IT mishears (engine x->nginx,
# qctl->kubectl) come out right as words appear instead of being fixed only at the end.
# Conservative — reuses the precision-first PhoneticCorrector, so ordinary words are left
# alone. The LLM homophone layer stays commit-only (too slow per preview). 0 = previews
# raw (old behaviour, corrected only at commit).
PREVIEW_FIX = os.environ.get("DUM_PREVIEW_FIX", "1") != "0"
# Strip standalone filler/disfluency words (uh, um, hmm, ...) from BOTH the live preview and the
# committed text (General cleanup — everyone says "uh"). DEFAULT ON; DUM_STRIP_FILLERS=0 = verbatim.
# Helpers are in pipeline (strip_fillers / drop_fillers); the one-tick "don't eat a real word that
# starts like a filler" gate falls out of the preview's per-tick re-transcription (see drop_fillers).
STRIP_FILLERS = os.environ.get("DUM_STRIP_FILLERS", "1") != "0"
# Decapitalize a stray boundary capital on a closed set of safe words (the/and/it/...) when it is NOT a
# real sentence start — the visible CAP face of the over-eager-boundary bug ("make The switch", or a
# continuation segment typed inline as "The window size"). DEFAULT ON; DUM_DECAP_CAPS=0 = verbatim/off.
# Justified by the measured ~97%+ correct rate over 1,300 real commits (pipeline.decap_interior; the
# closed SAFE_LOWER set is the name protection). Cross-commit state lives in self._prev_ended_sentence.
DECAP_CAPS = os.environ.get("DUM_DECAP_CAPS", "1") != "0"
# Hold an in-progress MULTI-WORD vocab alias off the live overlay until it resolves, so a phrase
# like "V S code" reveals as "VS Code" in ONE shot instead of typing the literal letters and then
# retyping when the alias fires. Pure display gate (overlay.hold_alias_prefix on the revealed
# prefix); the committed text + commit reconcile are UNCHANGED (it's the backstop). The held term
# appears a beat later (when the recognizer finishes the phrase) but correct, never retyped.
# DEFAULT ON; DUM_HOLD_ALIAS_PREFIX=0 = old eager-then-retype behaviour.
HOLD_ALIAS_PREFIX = os.environ.get("DUM_HOLD_ALIAS_PREFIX", "1") != "0"
# Lock-and-trim (incremental decoding): cap the LIVE preview re-transcription window so its
# cost stays ~constant on long sentences — the cause of "words arrive in big chunks". A tail
# word whose audio ended more than LOCK_MARGIN_S before the live edge is locked and its audio
# trimmed out of future previews (Parakeet won't revise a word with that much right-context).
# commit() still transcribes the FULL sentence, so the final text keeps full accuracy — the
# trim only bounds the live draft. 0 in DUM_LOCK_TRIM => old growing-window behaviour.
# A carry-over CONTEXT buffer of audio BEFORE the lock point is still decoded each preview
# (for acoustic left-context, so trimmed-tail words don't garble/recapitalize) but is not
# re-displayed. Live window = context + margin + recent => bounded, ~150ms proc on any length.
LOCK_TRIM = os.environ.get("DUM_LOCK_TRIM", "1") != "0"
LOCK_MARGIN_S = float(os.environ.get("DUM_LOCK_MARGIN", 1.5))
# Phase 1 one-by-one reveal. Reveal a word on screen once its right boundary sits
# DISPLAY_MARGIN_S behind the live edge (age-based, from lock-trim word timestamps), instead of
# waiting for two previews to agree — which is what caused the freeze-then-dump word clumps.
# Must be <= LOCK_MARGIN_S (clamped). 0 = OFF (old two-preview agreement gate). Default 0.7:
# Decision A (2026-06-16) — 0.5/0.7/1.0 all felt the same in the feel-check, so margin isn't the
# perceived-speed lever in this band; 0.7 is the snappier pick at equal feel. 1.0 is marginally
# cleaner on the bench (lower defer) if ever revisited. The real "correct words sooner" lever is
# recognizer biasing (Phase 4/5), which will move this knee — so this is deliberately not over-tuned.
DISPLAY_MARGIN_S = min(float(os.environ.get("DUM_DISPLAY_MARGIN", 0.7)), LOCK_MARGIN_S)
LOCK_CONTEXT_S = float(os.environ.get("DUM_LOCK_CONTEXT", 3.0))
MIN_SPEECH_S = float(os.environ.get("DUM_MIN_SPEECH", 0.25))  # need this much real speech to commit (drops noise blips)
MAX_SEG_S = 12.0                     # force-commit runaway sentences (bounds compute)
PREROLL_S = 0.20                     # keep this much pre-speech audio so onsets aren't clipped
VAD_MARGIN_DB = float(os.environ.get("DUM_VAD_MARGIN", 12.0))  # dB over noise floor = speech

# Built-in fallback mic when neither --mic/DUM_MIC nor saved config picks one. Was baked into
# the `dum` launcher as DUM_MIC:="MacBook Air"; moved here so saved config isn't shadowed.
BUILTIN_DEFAULT_MIC = os.environ.get("DUM_DEFAULT_MIC", "MacBook Air")
HOTKEY = os.environ.get("DUM_HOTKEY", "<ctrl>+<alt>+d")
DOUBLE_TAP_GAP = float(os.environ.get("DUM_DOUBLE_GAP", 0.40))  # max s between the two taps

# Live overlay routing: overlay-by-DEFAULT on every app. It streams cleanly in native text views,
# Electron apps, and browser inputs — feel-checked across TextEdit/Notes/ChatGPT/Mail/Safari/Discord/
# Obsidian (2026-06-22). The old "rich-text apps must use paste" allowlist was a mechanistic assumption
# (autocorrect/contenteditable would drift the reconcile) that was never measured per-app and turned out
# wrong; the one genuinely-measured corruption is the terminal-TUI async-echo scramble (~1.5%, accepted).
# The overlay can't read the screen, so it still drifts on a field that mutates underneath it — the known
# such surfaces are terminal TUIs (accepted) and canvas/non-standard web editors (e.g. Google Docs).
# Force any app to commit-only clipboard paste with DUM_OVERLAY_APPS_OFF=app1,app2 (the kill-switch).
# Names match macOS process names (frontmost_app); routing is by APP, so a whole browser is on or off,
# not per web-page.
DEFAULT_OVERLAY_BLOCK = set()    # apps forced to paste by default — none proven-bad-by-name yet (seam)


def overlay_block_apps():
    """Apps the live overlay must NOT drive (routed to commit-only paste): the default-empty seam
    above plus the DUM_OVERLAY_APPS_OFF kill-switch. This is the inverse of the retired allowlist —
    overlay is now the default everywhere and this names the rare surfaces that scramble."""
    block = set(DEFAULT_OVERLAY_BLOCK)
    off = os.environ.get("DUM_OVERLAY_APPS_OFF")
    if off:
        block |= {a.strip().lower() for a in off.split(",") if a.strip()}
    return block


def log(msg):
    print(msg, flush=True)


def build_parakeet(d):
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=pick(d, "encoder", prefer_int8=True),
        decoder=pick(d, "decoder", prefer_int8=True),
        joiner=pick(d, "joiner", prefer_int8=True),
        tokens=str(d / "tokens.txt"), num_threads=2, sample_rate=SR,
        feature_dim=80, decoding_method="greedy_search", model_type="nemo_transducer")


def transcribe(rec, audio):
    s = rec.create_stream()
    s.accept_waveform(SR, audio)
    rec.decode_streams([s])
    return s.result.text


def transcribe_words(rec, audio):
    """Transcribe + group tokens into words with per-word START times (seconds, relative
    to `audio`). NeMo Parakeet marks a word start with a leading space on the token; sub-word
    pieces and punctuation attach to the current word. Returns (words, starts) with
    len(words)==len(starts). Used by the live lock-and-trim window for timing; commit() still
    uses transcribe() on the full audio for the accurate final text."""
    s = rec.create_stream()
    s.accept_waveform(SR, audio)
    rec.decode_streams([s])
    r = s.result
    words, starts, cur = [], [], ""
    for tok, ts in zip(r.tokens, r.timestamps):
        if tok.startswith(" ") or not cur:
            if cur:
                words.append(cur)
            cur = tok.strip()
            starts.append(ts)
        else:
            cur += tok
    if cur:
        words.append(cur)
    return words, starts


# phrases Parakeet/Whisper-family models hallucinate on near-silence — dropped only
# when they are the ENTIRE commit (never mid-sentence). Normalized: lowercase, no punct.
HALLUCINATIONS = {
    "thank you", "thank you very much", "thanks", "thanks for watching",
    "thank you for watching", "you", "yeah", "bye", "uh", "um", "mm", "mhm",
    "mm hmm", "hmm", "thank you so much",
}


def _norm_phrase(s):
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


_END_PUNCT = re.compile(r"[.?!]+$")   # trailing sentence-final punctuation on a word


def dbfs(block):
    rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
    return 20.0 * np.log10(max(rms, 1e-9))


def resolve_device(spec):
    """spec: None | int-as-str | name substring -> sounddevice device id."""
    if spec is None or spec == "":
        return None
    return int(spec) if str(spec).isdigit() else spec


class LiveDictation:
    """Continuous capture -> VAD-segmented sentences -> correct -> paste."""

    def __init__(self, rec, pipe, bus, do_paste=True, device=None,
                 use_llm=False, terms=None, overlay=False, platform=None,
                 tracer=None, dump_dir=None, eager_first=False):
        self.rec = rec
        self.pipe = pipe
        self.bus = bus
        self.platform = platform or get_platform()   # OS-specific I/O behind one interface
        # opt-in (DUM_DOGFOOD_LOG=1); no-op otherwise. frontmost_app feeds the activity monitor
        # (app-switch timeline) so post-commit "fixed vs moved on" can be told apart.
        self.dogfood = DogfoodLogger(frontmost_fn=self.platform.frontmost_app)
        self.do_paste = do_paste
        self.device = device
        self.use_llm = use_llm          # LLM stage is built lazily ON the consumer
        self.terms = terms or []        # thread — MLX streams are thread-local
        self.llm_stage = None
        self.tr = tracer or Tracer(None)   # no-op tracer unless --trace
        self.dump_dir = dump_dir           # if set, dump each committed segment WAV here
        self._seg_n = 0                    # committed-segment counter (for WAV filenames)
        self.eager_first = eager_first     # lock word 1 from a single preview (snappier start)
        # --eager => show word-0 instantly (eager_after 0); else wait up to EAGER_AFTER s for
        # a confirmed word before falling back to the best guess (fewer wrong-word flashes).
        self.eager_after = 0.0 if eager_first else EAGER_AFTER
        # instant phonetic/dictionary corrector for the PREVIEW path (Milestone B step 2):
        # the same conservative corrector the commit pipeline uses, minus the LLM. None =>
        # previews stay raw (corrected only at commit, the old behaviour).
        self.preview_corrector = (PhoneticCorrector(self.terms,
                                                    extra_phrase_aliases=load_all_aliases())
                                  if PREVIEW_FIX and self.terms else None)
        # Proper-prefix set of multi-word alias spoken-forms, so the preview can hold an in-progress
        # phrase ("V S code") off-screen until it resolves to "VS Code" — no typed-then-retyped letters.
        # Only meaningful when the preview corrector is active (it produces the resolved form).
        # SCOPE (safety): only SHORT-token prefixes (≤2 chars: "v","s","vs") — letters/acronyms that are
        # never a word the user wants on their own, so holding costs nothing. A common word that merely
        # STARTS an alias ("git" in "git hub", "web" in "web socket") is left to reveal immediately, so
        # daily "git push" / "web page" dictation is NOT delayed a word. Letter-split is exactly the
        # retype this fixes (VS Code). Broadening to merge-style aliases would need its own feel-check.
        _pre = (alias_prefix_set([toks for toks, _ in load_all_alias_pairs()])
                if HOLD_ALIAS_PREFIX and self.preview_corrector is not None else frozenset())
        self._alias_prefixes = frozenset(p for p in _pre if all(len(t) <= 2 for t in p))
        self.overlay_block = overlay_block_apps()
        # app-gating only where the OS can name the focused app; elsewhere keep overlay on
        self.app_gating = self.platform.supports_app_detection()
        # overlay = word-by-word live typing; dry (just log ops) when paste is off
        self.overlay = (OverlayTyper(dry=not do_paste, platform=self.platform)
                        if overlay else None)
        self.q = queue.Queue()
        self.stream = None
        self.worker = None
        # Cross-commit decap state: did the LAST committed segment end a sentence? Initialized True so
        # the dictation's true first word is always protected (a fresh start is a sentence start).
        self._prev_ended_sentence = True
        self.running = threading.Event()
        self.lock = threading.Lock()

    # ---- mic callback: ONLY enqueue, never block -----------------------------
    def _on_audio(self, indata, frames, time_info, status):
        if status:
            # overflow/underflow — drop a note but keep going
            log(f"[audio] {status}")
        self.q.put(indata[:, 0].copy())

    def start(self):
        with self.lock:
            if self.running.is_set():
                return
            try:
                import sounddevice as sd
                self.stream = sd.InputStream(
                    samplerate=SR, channels=1, dtype="float32",
                    blocksize=int(BLOCK_S * SR), device=self.device,
                    callback=self._on_audio)
                self.stream.start()
            except Exception as e:
                log(f"[ERR] could not open mic: {e}")
                return
            # drain any stale frames
            while not self.q.empty():
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
            self._prev_ended_sentence = True   # a fresh dictation starts a sentence — protect word 1
            self.running.set()
            self.worker = threading.Thread(target=self._consume, daemon=True)
            self.worker.start()
            self.platform.notify("start")
            if self.overlay is not None:
                mode = "overlay DRY (log ops only)" if self.overlay.dry else "overlay (live typing)"
                # Phase 2 smart cursor-edit is the default; flag only the rare disabled state.
                if not self.overlay.min_edit:
                    mode += " (smart-edit OFF)"
            else:
                mode = "paste ON" if self.do_paste else "paste OFF — log only"
            log(f"[REC]  listening... speak in sentences; pauses commit. ({mode})")

    def stop(self):
        with self.lock:
            if not self.running.is_set():
                return
            self.running.clear()
        if self.stream:
            try:
                self.stream.stop(); self.stream.close()
            except Exception:
                pass
            self.stream = None
        if self.worker:
            self.worker.join(timeout=3)
            self.worker = None
        self.platform.notify("done")     # end-of-dictation cue (toggle off)
        log("[--]   stopped listening")

    def toggle(self):
        if self.running.is_set():
            self.stop()
        else:
            self.start()

    def flag_last_problem(self):
        """Mark the most recent committed dictation as a problem to revisit (double-tap left ⌥).
        The audio + correction context are already saved; this just tags the commit_id."""
        cid = self.dogfood.flag_problem()
        if cid:
            log("[FLAG]  last dictation flagged as a problem — saved for manual review")
            self.platform.notify("flag")
        else:
            log("[flag]  nothing to flag yet (no commit, or dogfood log off)")

    def replay(self, wav_path, realtime=True):
        """Feed a WAV through the REAL consumer loop (VAD -> previews -> lock-trim ->
        corrections -> commit), exactly as the mic would, with the overlay in dry mode.
        Emits the same trace.jsonl / events.jsonl a live session does, so bench.py can
        score the actual pipeline headlessly — no mic, deterministic. realtime=True paces
        blocks at mic cadence (faithful VAD segmentation); False feeds as fast as the
        consumer drains (quicker, minor batching risk)."""
        import soundfile as sf
        audio, sr = sf.read(wav_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]
        if sr != SR:
            raise SystemExit(f"replay needs {SR} Hz mono; {wav_path} is {sr} Hz")
        self.app_gating = False        # force overlay active despite headless focus
        self.running.set()
        self.worker = threading.Thread(target=self._consume, daemon=True)
        self.worker.start()
        bs = int(BLOCK_S * SR)

        def feed(block):
            self.q.put(block.copy())
            if realtime:
                time.sleep(BLOCK_S)
            else:
                while not self.q.empty():
                    time.sleep(0.001)

        for i in range(0, len(audio), bs):
            b = audio[i:i + bs]
            if len(b) < bs:
                b = np.pad(b, (0, bs - len(b)))
            feed(b)
        # trailing digital silence so the last sentence's VAD pause commits it
        sil = np.zeros(bs, dtype="float32")
        for _ in range(int((MIN_SIL_S + 0.6) / BLOCK_S)):
            feed(sil)
        while not self.q.empty():      # let the consumer finish the final commit
            time.sleep(0.02)
        time.sleep(0.4)
        self.running.clear()
        if self.worker:
            self.worker.join(timeout=5)
            self.worker = None

    def _overlay_safe(self, app):
        """Should THIS sentence use the live overlay (vs commit-only paste)? Overlay-by-DEFAULT on
        every app; paste only when the overlay is disabled or the focused app is blocklisted
        (DUM_OVERLAY_APPS_OFF / DEFAULT_OVERLAY_BLOCK). If the platform can't name apps, keep the
        overlay on (can't blocklist what we can't name)."""
        if self.overlay is None:
            return False
        if not self.app_gating:
            return True
        return not (bool(app) and app.strip().lower() in self.overlay_block)

    def _build_llm(self):
        """Build the LLM stage HERE, on the consumer thread, so every MLX op
        (load + inference) shares this thread's GPU stream. Loading it on the
        main thread crashes with 'no Stream(gpu, N) in current thread' because
        MLX streams are thread-local. Inserted before the external (paid) seam."""
        from llm_stage import LLMWorker
        log("loading LLM stage (cached; ~700MB download only if not already present)...")
        # LLMWorker pins the MLX model to its own persistent thread, so it survives
        # the consumer thread being recreated on every start/stop toggle.
        self.llm_stage = LLMStage(LLMWorker(self.terms))      # Layer 3: free, built-in
        # insert right before the external seam, so trailing stages (fuzzysym, sentcap) stay after it
        ext_i = next((k for k, s in enumerate(self.pipe.stages) if getattr(s, "name", "") == "external"),
                     len(self.pipe.stages) - 1)
        self.pipe.stages.insert(ext_i, self.llm_stage)
        log("LLM stage ready")

    # ---- the single consumer thread: VAD + streaming + commit ----------------
    def _consume(self):
        if self.use_llm and self.llm_stage is None:
            self._build_llm()
        cur = []                       # list[np.ndarray] of the current sentence
        preroll = deque(maxlen=max(1, int(PREROLL_S / BLOCK_S)))
        in_sentence = False
        sil_run = 0.0                  # trailing silence (s)
        since_preview = 0.0            # speech audio since last preview (s)
        floor = None                   # adaptive noise floor (dBFS)
        ov_prev = []                   # overlay: previous preview's word list
        ov_focus = None                # overlay: frontmost app when typing began
        ov_eager = None                # overlay: word 1 if it was eager-locked (flicker tracking)
        ov_active = False              # overlay: live-type THIS sentence? (else commit-only paste)
        speech_blocks = 0              # count of speech-classified blocks this sentence
        locked_words = []             # lock-and-trim: words whose audio is trimmed from previews
        locked_samples = 0            # lock-and-trim: window start = front of the unlocked tail

        def seg_seconds():
            return sum(len(b) for b in cur) / SR

        def reset_overlay():
            nonlocal ov_prev, ov_focus, ov_eager, ov_active, locked_words, locked_samples
            if self.overlay is not None:
                self.overlay.reset()
            locked_words, locked_samples = [], 0
            ov_prev, ov_focus, ov_eager, ov_active = [], None, None, False

        def drop(reason):
            """Abandon this segment as noise/hallucination. If the overlay already
            typed some of it live, erase that (focus-permitting) so nothing is left."""
            log(f"[--]   {reason}")
            if self.overlay is not None and self.overlay.typed:
                if ov_focus is None or self.platform.frontmost_app() == ov_focus:
                    self.overlay.reconcile("")
            reset_overlay()

        def commit():
            nonlocal ov_prev, ov_focus
            if speech_blocks * BLOCK_S < MIN_SPEECH_S:
                drop(f"(too little speech: {speech_blocks * BLOCK_S:.2f}s) — ignored")
                return
            # `sil_run` = trailing silence already elapsed = time since you stopped
            # talking. The settle latency the user FEELS is this + everything below.
            mouth_stop_ago_ms = sil_run * 1000.0
            audio = np.concatenate(cur)
            if self.dump_dir:
                self._seg_n += 1
                try:
                    import soundfile as sf
                    sf.write(f"{self.dump_dir}/seg_{self._seg_n:03d}.wav", audio, SR)
                except Exception as e:
                    log(f"[trace] wav dump failed: {e}")
            t0 = time.monotonic()
            raw = transcribe(self.rec, audio)
            transcribe_ms = (time.monotonic() - t0) * 1000.0
            if not raw.strip():
                drop("(no speech in segment)")
                return
            if _norm_phrase(raw) in HALLUCINATIONS:
                drop(f"(dropped likely hallucination: {raw!r})")
                return
            llm_t0 = self.llm_stage.time if self.llm_stage else 0.0
            llm_n0 = self.llm_stage.fired if self.llm_stage else 0
            t0 = time.monotonic()
            fixed, evs = self.pipe.run(raw, {"surface": "terminal"})
            if STRIP_FILLERS:
                stripped = strip_fillers(fixed)
                if not stripped.strip():
                    drop("(filler-only utterance — nothing to insert)")
                    return
                fixed = stripped     # clean text everywhere downstream (overlay / paste / log); raw keeps fillers
            if DECAP_CAPS:
                # MUST run AFTER strip_fillers (it recapitalizes the first word) and the whole pipeline
                # (SentenceCapStage). Undoes a stray boundary capital on a continuation segment; protects
                # the true first word when the previous segment ended a sentence. Ordering is load-bearing.
                fixed = decap_interior(fixed, after_sentence=self._prev_ended_sentence)
                self._prev_ended_sentence = _ends_sentence(fixed)
            pipe_ms = (time.monotonic() - t0) * 1000.0
            llm_ms = ((self.llm_stage.time - llm_t0) * 1000.0) if self.llm_stage else 0.0
            llm_fired = bool(self.llm_stage and self.llm_stage.fired > llm_n0)
            # snapshot eager state + app NOW — reset_overlay() in the overlay block below
            # wipes ov_eager/ov_focus before the trace emit, so capture them here.
            fw_final = fixed.split()[0] if fixed.split() else ""
            eager_used = ov_eager is not None
            eager_revised = eager_used and _norm_phrase(ov_eager) != _norm_phrase(fw_final)
            commit_app = ov_focus
            t_apply0 = time.monotonic()
            apply_wall0 = time.time()        # wall-clock start of dum's own insertion/reconcile
            for e in evs:
                self.bus.emit(e)
            # commit-level record: every committed sentence (corrected or not) with
            # context, so the future personalisation agent has the end-to-end picture
            mode = ("overlay" if (self.overlay is not None and ov_active)
                    else ("paste" if self.do_paste else "log"))
            self.bus.emit({
                "type": "commit", "raw": raw, "fixed": fixed, "changed": raw != fixed,
                "surface": "terminal", "app": ov_focus or self.platform.frontmost_app(),
                "mode": mode, "llm": self.use_llm, "n_words": len(fixed.split()),
            })
            if self.overlay is not None and ov_active:
                # one reconcile applies corrections AND completes the unlocked tail,
                # but only if focus hasn't moved (else we'd backspace the wrong field)
                if ov_focus is not None and self.platform.frontmost_app() != ov_focus:
                    log("[!]    focus changed mid-sentence — overlay reconcile skipped")
                elif not self.overlay.reconcile(fixed, exact=True):
                    log("[!]    overlay edit too large — skipped (left as dictated)")
                else:
                    # exact reconcile already put Parakeet's real punctuation (?, .) and
                    # casing on screen; just add the trailing space between sentences
                    self.overlay.finish(" ")
                reset_overlay()
            elif self.do_paste:
                self.platform.paste(fixed + " ")
            apply_ms = (time.monotonic() - t_apply0) * 1000.0
            # tell the dogfood activity monitor when dum was typing, so its OWN synthetic keystrokes
            # (paste Cmd+V, CGEvent typing, overlay backspace+retype) aren't counted as user edits —
            # incl. when this commit's insertion lands inside an earlier commit's observation window.
            self.dogfood.mark_self_typing(apply_wall0, time.time())
            settle_ms = mouth_stop_ago_ms + transcribe_ms + pipe_ms + apply_ms
            self.tr.ev("commit", n=self._seg_n, audio_s=round(len(audio) / SR, 2),
                       mouth_stop_ms=round(mouth_stop_ago_ms), transcribe_ms=round(transcribe_ms),
                       pipe_ms=round(pipe_ms), llm_ms=round(llm_ms), llm_fired=llm_fired,
                       apply_ms=round(apply_ms), settle_ms=round(settle_ms),
                       changed=(raw != fixed), n_words=len(fixed.split()),
                       eager=eager_used, eager_revised=eager_revised,
                       mode=mode, app=commit_app,
                       raw=raw, fixed=fixed)
            # opt-in dogfood log (DUM_DOGFOOD_LOG=1): rich commit record + best-effort
            # background post-commit edit capture. Non-blocking; never breaks dictation.
            # surface + window_title are derived inside the logger (real bucket from app, AX title);
            # audio = the full committed utterance, saved for offline replay/eval (Layer-1 ground
            # truth). All post-apply, off the perceived-latency path.
            self.dogfood.log_commit(raw, fixed, app=commit_app or self.platform.frontmost_app(),
                                    mode=mode, latency_ms=settle_ms, stages=evs, audio=audio, sr=SR)
            log(f"\r[OK]   {fixed}")
            if raw != fixed:
                log(f"       (raw: {raw})")

        while self.running.is_set():
            try:
                first = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            blocks = [first]
            while True:                # drain everything available this tick
                try:
                    blocks.append(self.q.get_nowait())
                except queue.Empty:
                    break

            for b in blocks:
                d = dbfs(b)
                if floor is None:
                    floor = d
                elif d < floor:
                    floor = 0.9 * floor + 0.1 * d      # track true floor down fast
                else:
                    floor = 0.995 * floor + 0.005 * d  # rise slowly
                speech = d > floor + VAD_MARGIN_DB

                if not in_sentence:
                    preroll.append(b)
                    if speech:
                        in_sentence = True
                        cur = list(preroll)
                        preroll.clear()
                        sil_run = 0.0
                        since_preview = 0.0
                        speech_blocks = 1
                        reset_overlay()
                        # decide overlay-vs-paste for THIS sentence by the focused app
                        if self.overlay is not None:
                            ov_focus = self.platform.frontmost_app() if self.app_gating else None
                            ov_active = self._overlay_safe(ov_focus)
                        self.tr.ev("onset", app=ov_focus,
                                   mode=("overlay" if ov_active else "paste"))
                else:
                    cur.append(b)
                    if speech:
                        sil_run = 0.0
                        speech_blocks += 1
                    else:
                        sil_run += BLOCK_S
                    since_preview += BLOCK_S

            if not in_sentence:
                continue

            secs = seg_seconds()
            if (sil_run >= MIN_SIL_S and secs >= MIN_SEG_S) or secs >= MAX_SEG_S:
                try:
                    commit()
                except Exception as e:                 # never let one bad commit kill dictation
                    log(f"[ERR]  commit failed: {e}")
                    reset_overlay()
                cur = []
                in_sentence = False
            elif since_preview >= STEP_S and secs >= MIN_SEG_S:
                # clean micro-pause dots on previews too, so overlay never types them
                p0 = time.monotonic()
                full = np.concatenate(cur)
                if LOCK_TRIM:
                    # Decode from LOCK_CONTEXT_S before the lock point (left-context, kept so
                    # tail words don't garble) but only display/lock words PAST the lock point.
                    # Lock any such word old enough that more audio won't revise it and advance
                    # the window past it. commit() re-runs the FULL audio, so the final text is
                    # unaffected — this only bounds the live draft to ~context+margin seconds.
                    ctx_start = max(0, locked_samples - int(LOCK_CONTEXT_S * SR))
                    window = full[ctx_start:]
                    tw, ts = transcribe_words(self.rec, window)
                    lock_t = (locked_samples - ctx_start) / SR
                    tail = [(w, s) for w, s in zip(tw, ts) if s >= lock_t - 0.06]
                    cutoff = (len(window) / SR) - LOCK_MARGIN_S
                    n = 0
                    while n + 1 < len(tail) and tail[n + 1][1] <= cutoff:  # keep >=1 in tail
                        n += 1
                    if n:
                        locked_words.extend(w for w, _ in tail[:n])
                        locked_samples = ctx_start + int(tail[n][1] * SR)
                    txt = clean_punct(" ".join(locked_words + [w for w, _ in tail[n:]]))
                else:
                    txt = clean_punct(transcribe(self.rec, full))
                # Milestone B step 2: fix known IT mishears live, on the preview itself,
                # so they're right as words appear (not just reconciled at commit). Runs
                # before split/prefix so multi-word aliases (engine x->nginx) apply, and
                # before stable_prefix so the corrected form is what two previews agree on.
                if self.preview_corrector is not None and txt.strip():
                    txt = self.preview_corrector.correct(txt)
                preview_ms = (time.monotonic() - p0) * 1000.0
                self.tr.ev("preview", audio_s=round(secs, 2), proc_ms=round(preview_ms),
                           locked=len(locked_words),
                           tail_s=round((len(full) - locked_samples) / SR, 2),
                           q=self.q.qsize(), behind=preview_ms > STEP_S * 1000.0)
                if self.overlay is not None and ov_active:
                    # strip terminal .?! from live words — a not-yet-final word's
                    # period is unreliable (Parakeet ends every preview with one) and
                    # would get stranded mid-sentence once you keep talking. The real
                    # end mark is added at commit.
                    words = [w for w in (_END_PUNCT.sub("", t) for t in txt.split()) if w]
                    if STRIP_FILLERS:
                        words = drop_fillers(words, at_start=not self.overlay.typed)
                    if DECAP_CAPS:
                        # Decap the live preview to match the committed casing (no wrong capital shown
                        # live, live==commit). after_sentence is the STABLE per-segment protection of
                        # word 0 (genuine start iff the prev segment ended a sentence); it must not vary
                        # tick-to-tick or a legit first-word capital would flicker. _END_PUNCT already
                        # stripped per-token sentence marks, so interior safe words lower; word 0 is the
                        # only protected position. A genuine in-window marker-start ("Deploy it. So…")
                        # can read lower live and snap back at commit — rare; surfaced in the feel-check.
                        words = decap_interior(" ".join(words),
                                               after_sentence=self._prev_ended_sentence).split()
                    # IGNORE EMPTY previews: the offline model intermittently emits nothing
                    # on a growing window (verified: 'So the' -> '' -> 'So the timeline' -> '').
                    # Updating to [] would reset the two-preview agreement (delaying the first
                    # word ~1s) AND try to erase the on-screen text (a huge deferred rewrite =
                    # the chunky 'pause then dump'). So skip empties and keep the last good prefix.
                    if words:
                        # show the stable (two-preview-agreed) prefix; if nothing's shown yet
                        # and we've waited eager_after seconds, fall back to the best guess so
                        # the first word never stalls. Confirmed words => no wrong-word flash.
                        strict = stable_prefix(ov_prev, words)
                        at_start = not self.overlay.typed
                        eager_now = at_start and (secs >= self.eager_after)
                        # Phase 1 one-by-one reveal: when DISPLAY_MARGIN is set, the stable
                        # prefix is decided by audio AGE (lock-trim word timestamps) rather than
                        # two-preview agreement — a word reveals as soon as its right boundary is
                        # DISPLAY_MARGIN_S old, skipping the extra preview the agreement gate
                        # waited for. Corrections run on the revealed prefix so IT terms still
                        # come out right. Onset filler/breath/eager gates still apply via
                        # streaming_prefix. age=None => old agreement path (DISPLAY_MARGIN off).
                        age = None
                        if LOCK_TRIM and DISPLAY_MARGIN_S > 0:
                            d = max(n, age_stable_count([s for _, s in tail],
                                                        len(window) / SR, DISPLAY_MARGIN_S))
                            age_txt = clean_punct(" ".join(locked_words + [w for w, _ in tail[n:d]]))
                            if self.preview_corrector is not None and age_txt.strip():
                                age_txt = self.preview_corrector.correct(age_txt)
                            age = [w for w in (_END_PUNCT.sub("", t) for t in age_txt.split()) if w]
                            if STRIP_FILLERS:
                                age = drop_fillers(age, at_start=not self.overlay.typed)
                            if DECAP_CAPS:
                                age = decap_interior(" ".join(age),
                                                     after_sentence=self._prev_ended_sentence).split()
                        show = streaming_prefix(ov_prev, words, eager_first=eager_now,
                                                at_start=at_start, stable=age)
                        if self._alias_prefixes:
                            # hold an in-progress multi-word alias ("V S code") off-screen until it
                            # resolves to "VS Code" — reveals whole, never typed-then-retyped
                            show = hold_alias_prefix(show, self._alias_prefixes)
                        target = " ".join(show)
                        before = self.overlay.typed
                        if show and target != before:    # skip no-op previews (prefix unchanged)
                            nb, _ = reconcile_words(before, target)
                            # apply appends + SMALL live corrections; defer big tail rewrites to
                            # commit so the line doesn't thrash live
                            if not before or nb <= STREAM_FIX_MAX:
                                if self.overlay.reconcile(target):
                                    if not before:
                                        ov_eager = show[0]   # earliest word-0 shown (flicker metric)
                                    corrected = nb > 0 and bool(before)
                                    self.tr.ev("early_fix" if corrected else "lock",
                                               words=show, nb=nb, eager=not strict,
                                               audio_s=round(secs, 2))
                            else:
                                self.tr.ev("deferred", nb=nb, audio_s=round(secs, 2))
                        ov_prev = words
                elif txt.strip():
                    log(f"\r[~]    {txt}")
                since_preview = 0.0

        # flush a sentence in progress on stop. Drain any audio still queued first — when
        # you toggle off right after the last word, those frames haven't been consumed yet,
        # and committing without them dropped the tail of the sentence (#2 disappearing text).
        if in_sentence:
            while True:
                try:
                    cur.append(self.q.get_nowait())
                except queue.Empty:
                    break
            if seg_seconds() >= MIN_SEG_S:
                commit()


def load_all_aliases():
    """Phrase-aliases for every corrector: the SHIPPED global pack (packs/*.aliases, always on —
    this is what makes it a *global* dictionary) PLUS optional user/repo
    packs from $DUM_VOCAB_DIR on top. Deduped so pointing DUM_VOCAB_DIR at packs/ won't
    double-load. load_phrase_aliases stays a pure (dir->aliases) function for clean unit tests;
    the always-on policy lives here at the wiring."""
    shipped = HERE / "packs"
    aliases = load_phrase_aliases(str(shipped))
    env_dir = os.environ.get("DUM_VOCAB_DIR")
    if env_dir and Path(env_dir).resolve() != Path(shipped).resolve():
        aliases += load_phrase_aliases(env_dir)
    # Phase R (Decision G): auto-harvested cwd-repo vocab. Default-ON in the live tool
    # (main() sets DUM_REPO_VOCAB=1) so daily driving picks up project symbols; OFF in the
    # deterministic bench (it never calls main()) so the committed baseline isn't polluted by
    # whatever repo cwd happens to be. DUM_REPO_VOCAB=0 disables it.
    if os.environ.get("DUM_REPO_VOCAB", "0") not in ("0", "", "false"):
        try:
            from repo_harvest import ensure_repo_pack
            rdir = ensure_repo_pack()
            if rdir:
                aliases += load_phrase_aliases(rdir)
        except Exception:
            pass                                   # repo harvest must never break dictation
    return aliases


def load_all_alias_pairs():
    """(say_tokens, want) pairs from the same packs load_all_aliases uses (global + DUM_VOCAB_DIR
    + repo when DUM_REPO_VOCAB) — for the commit-only fuzzy symbol recovery stage. Parses the
    raw `lhs => rhs` so we keep the spoken-form tokens (load_phrase_aliases only returns regexes)."""
    dirs = [HERE / "packs"]
    env_dir = os.environ.get("DUM_VOCAB_DIR")
    if env_dir and Path(env_dir).resolve() != (HERE / "packs").resolve():
        dirs.append(Path(env_dir))
    if os.environ.get("DUM_REPO_VOCAB", "0") not in ("0", "", "false"):
        try:
            from repo_harvest import ensure_repo_pack
            rd = ensure_repo_pack()
            if rd:
                dirs.append(Path(rd))
        except Exception:
            pass
    pairs = []
    for d in dirs:
        if d and Path(d).is_dir():
            for f in sorted(Path(d).glob("*.aliases")):
                for line in f.read_text(errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=>" not in line:
                        continue
                    lhs, rhs = (s.strip() for s in line.split("=>", 1))
                    if lhs and rhs:
                        pairs.append((lhs.lower().split(), rhs))
    return pairs


def build_pipeline(terms):
    """Free built-in stages + the inert paid seam. The optional LLM stage is
    NOT added here — LiveDictation inserts it on the consumer thread (MLX streams
    are thread-local), between phonetic and external."""
    stages = [
        PunctuationStage(),                                    # Layer 1.5: drop micro-pause dots
        # Layer 2: free, built-in. extra_phrase_aliases = shipped global pack (always on) + any
        # user/repo packs via DUM_VOCAB_DIR (SEAM 2).
        PhoneticStage(PhoneticCorrector(terms, extra_phrase_aliases=load_all_aliases())),
        # SEAM 1: paid external corrector — inert unless DUM_EXTERNAL_CORRECTOR set
        ExternalCorrectorStage(os.environ.get("DUM_EXTERNAL_CORRECTOR")),
        # V2 SEAM: per-user personalization (learned corrections) — defined, inert in V1 (no learner,
        # no data). Slots in here; gated by DUM_PERSONAL_CORRECTIONS. See learn/proposer.py.
        PersonalCorrectionStage(),
        # COMMIT-ONLY constrained fuzzy symbol recovery — inert unless DUM_FUZZY_SYMBOLS=1.
        FuzzySymbolStage(load_all_alias_pairs()),
        # Revert common-word/name -> jargon corruptions (get->git, grab->grep, Rado->redis) unless the
        # sentence clearly carries command/code context. Source of truth for the 2026-06-20 theme.
        ProtectedWordsStage(),
        # LAST: re-capitalize sentence starts the alias/LLM/recovery layers may have lowercased.
        SentenceCapStage(),
    ]
    return CorrectionPipeline(stages)


def run_double_tap_toggle(app, trigger_key="cmd_l", mode="toggle"):
    """Global hotkey listener on macOS (needs Input Monitoring). The DICTATION start/stop
    trigger is configurable (key + mode, read from ~/.dum/config.json); the ⌥ "flag a problem"
    gesture stays hardcoded (double-tap LEFT ⌥) — out of scope for v1.

    `trigger_key` is a curated config token (see config.CURATED_KEYS), e.g. "cmd_l" (default,
    reproduces today's behavior exactly), "cmd_r", "alt_r", or "fn".
    `mode`:
      * "toggle" — a DOUBLE-TAP of the trigger key (two presses within DOUBLE_TAP_GAP, no other
        key between — so single presses and modifier+key shortcuts are untouched) flips
        start <-> stop. This is the original behavior.
      * "push"   — push-to-dictate: holding the trigger key starts recording, releasing it
        stops + commits. Wired through the same app.start()/app.stop() entry points.
    Global (needs Input Monitoring)."""
    from pynput import keyboard
    import config as _config

    desc = _config.key_descriptor(trigger_key)
    trig = getattr(keyboard.Key, desc["pynput"])   # the pynput Key for the chosen trigger

    cmd = {"last": 0.0, "armed": False}       # armed = a first tap is waiting for its partner
    opt = {"last": 0.0, "armed": False}       # the (hardcoded) ⌥ flag-a-problem double-tap
    push_down = {"held": False}               # push mode: ignore key-auto-repeat between press/release
    _NAV = ("left", "right", "up", "down", "home", "end", "page_up", "page_down")

    def _double(state, now):
        if state["armed"] and (now - state["last"]) <= DOUBLE_TAP_GAP:
            state["armed"] = False
            return True
        state["last"] = now
        state["armed"] = True
        return False

    def _key_category(key):
        # CONTENT-FREE: coarse category for the keystroke proxy, never the character.
        if key == keyboard.Key.backspace:
            return "backspace"
        if key == keyboard.Key.delete:
            return "delete"
        if getattr(key, "name", None) in _NAV:
            return "nav"
        return "other"

    def on_press(key):
        now = time.monotonic()
        # feed the dogfood activity monitor from this SINGLE listener (no second pynput listener —
        # two would call macOS TIS/TSM from different threads and the OS aborts the process).
        app.dogfood.record_key(_key_category(key))
        if key == trig:
            opt["armed"] = False              # a trigger tap breaks a pending ⌥ double-tap
            if mode == "push":
                if not push_down["held"]:     # one physical press = one start (ignore auto-repeat)
                    push_down["held"] = True
                    app.start()
            else:                             # toggle: start/stop on double-tap
                if _double(cmd, now):
                    app.toggle()
        elif key == keyboard.Key.alt_l and trig != keyboard.Key.alt_l:
            cmd["armed"] = False
            if _double(opt, now):
                app.flag_last_problem()
        else:
            cmd["armed"] = False              # any other key breaks both pending double-taps
            opt["armed"] = False

    def on_release(key):
        if mode == "push" and key == trig and push_down["held"]:
            push_down["held"] = False
            app.stop()                        # release => stop + commit

    if mode == "push":
        log(f"PUSH-TO-DICTATE: HOLD {desc['label']} to talk, release to stop + commit. "
            "double-tap LEFT ⌥ = flag last dictation. Ctrl+C to quit.")
        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    else:
        log(f"{desc['label']} = start/stop dictation; double-tap LEFT ⌥ = flag last dictation "
            "as a problem. Ctrl+C to quit.")
        listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        while listener.running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    listener.stop()
    app.stop()


def main():
    argv = sys.argv[1:]
    # Phase R default-ON for the live daily driver (Decision G): harvest the cwd repo's vocab.
    # The bench never calls main(), so it stays deterministic. Disable with DUM_REPO_VOCAB=0.
    os.environ.setdefault("DUM_REPO_VOCAB", "1")
    if "--list-devices" in argv:
        import sounddevice as sd
        for i, dv in enumerate(sd.query_devices()):
            if dv["max_input_channels"] > 0:
                log(f"  {i}: {dv['name']}")
        return

    do_paste = "--no-paste" not in argv
    use_llm = "--llm" in argv
    use_hotkey = "--hotkey" in argv
    use_double = "--double-cmd" in argv
    use_overlay = "--overlay" in argv
    is_replay = "--replay" in argv
    want_config = "--config" in argv
    eager_first = "--eager" in argv or os.environ.get("DUM_EAGER") == "1"
    global VAD_MARGIN_DB
    if "--margin" in argv:
        VAD_MARGIN_DB = float(argv[argv.index("--margin") + 1])

    # --- First-run / on-demand config wizard (mic + dictation hotkey) -----------------
    # GUARD (must hold ALL to run the interactive, stdin-blocking wizard, or it would hang
    # any non-interactive run incl. the test gate):
    #   * normal LIVE mode (the --double-cmd daily-driver path) AND NOT replay/bench/list-devices
    #   * stdin is a real TTY
    #   * no config file exists yet, OR --config was passed
    # bench.py never calls main(); --list-devices & --replay branch away above/here, so they
    # can't reach this. scripts/test runs --replay => the wizard never fires.
    import config as _config
    user_cfg = _config.load_config()
    wizard_ok = (use_double and not is_replay and sys.stdin.isatty()
                 and (want_config or not _config.config_exists()))
    if wizard_ok:
        try:
            devices, default_idx = _config.list_input_devices()
        except Exception as e:
            log(f"[config] could not enumerate input devices ({e}); skipping mic picker")
            devices, default_idx = [], None
        user_cfg = _config.run_wizard(devices, default_idx)
    hotkey_key = user_cfg.get("hotkey_key", _config.DEFAULT_KEY)
    hotkey_mode = user_cfg.get("hotkey_mode", _config.DEFAULT_MODE)

    # Mic precedence: explicit --mic / DUM_MIC (flag/env) > saved config > built-in default.
    flag_mic = argv[argv.index("--mic") + 1] if "--mic" in argv else None
    env_mic = os.environ.get("DUM_MIC") or os.environ.get("DICTATE_MIC")
    mic_spec = _config.resolve_mic_spec(flag_mic, env_mic, user_cfg.get("mic"), BUILTIN_DEFAULT_MIC)
    device = resolve_device(mic_spec)

    # --trace <path> : append hi-res latency events; --dump-wav <dir> : save each
    # committed segment WAV (so the exact audio the model heard can be re-checked).
    trace_path = (argv[argv.index("--trace") + 1] if "--trace" in argv
                  else os.environ.get("DUM_TRACE"))
    dump_dir = (argv[argv.index("--dump-wav") + 1] if "--dump-wav" in argv
                else os.environ.get("DUM_DUMP_WAV"))
    tracer = Tracer(trace_path)

    terms = load_terms([HERE / "terms.txt"], os.environ.get("DUM_VOCAB_DIR"))
    log(f"loaded {len(terms)} IT terms")
    if trace_path:
        log(f"[trace] -> {trace_path}")
    rec = build_parakeet(find_model_dir("sherpa-onnx-nemo-parakeet-tdt-*"))
    pipe = build_pipeline(terms)
    bus = EventBus(os.environ.get("DUM_EVENTS"))      # SEAM 3
    app = LiveDictation(rec, pipe, bus, do_paste=do_paste, device=device,
                        use_llm=use_llm, terms=terms, overlay=use_overlay,
                        tracer=tracer, dump_dir=dump_dir, eager_first=eager_first)
    if eager_first:
        log("[eager] first-word eager-lock ON")

    if "--replay" in argv:
        # headless: push a WAV through the real loop (for bench.py / regression).
        wav = argv[argv.index("--replay") + 1]
        log(f"[replay] {wav}")
        app.replay(wav, realtime="--replay-fast" not in argv)
    elif use_double:
        run_double_tap_toggle(app, trigger_key=hotkey_key, mode=hotkey_mode)
    elif use_hotkey:
        from pynput import keyboard
        log(f"hotkey daemon ready. tap {HOTKEY} to start/stop. Ctrl+C to quit.")
        with keyboard.GlobalHotKeys({HOTKEY: app.toggle}) as h:
            try:
                h.join()
            except KeyboardInterrupt:
                pass
        app.stop()
    else:
        app.start()
        try:
            while app.running.is_set():
                time.sleep(0.3)
        except KeyboardInterrupt:
            pass
        app.stop()
    tracer.close()
    app.dogfood.close()        # flush pending post-commit observers so the last commits aren't lost
    log("bye")


if __name__ == "__main__":
    main()
