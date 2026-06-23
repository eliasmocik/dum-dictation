#!/usr/bin/env python3
"""
SEAM 1 — correction pipeline with an external-corrector boundary.

The correction layer is an ordered list of stages. Built-in stages (phonetic,
LLM) are free. After them sits ExternalCorrectorStage: a boundary where a CLOSED
"advanced Layer 3" corrector can plug in later as a separate helper process over
stdio — defined now, disabled until DUM_EXTERNAL_CORRECTOR points to an executable.

Each stage returns (text, events); events flow to the EventBus (SEAM 3).
"""
import difflib, json, os, re, shlex, subprocess, time, unicodedata
from fuzzy_recover import recover as _fuzzy_recover, build_index as _fuzzy_index

def _ev(stage, before, after):
    if after == before:
        return []
    return [{"type": "correction.applied", "stage": stage, "before": before, "after": after}]

# Parakeet inserts a sentence-final mark at micro-pauses. A . ? ! followed by
# whitespace then a lowercase word is almost never a real break (real ones are
# followed by a capital), so drop it: "See? this is" -> "See this is". Tokens like
# "3.5" are safe (no trailing space + a digit, not lowercase, follows).
_MIDPUNCT = re.compile(r"[.?!]+\s+(?=[a-z])")

def clean_punct(text):
    return _MIDPUNCT.sub(" ", text)

# Sentence-initial capitalization. The vocab/alias layer can replace a sentence-initial word with a
# LOWERCASE canonical ("Engine X is down" -> "nginx is down"; "...request. Engine X" -> "...request.
# nginx"), dropping the capital the recognizer had put there. Re-capitalize the first letter of the
# text and the first letter after each sentence end. This runs LAST (after alias + LLM), so it fixes
# whatever they lowercased. clean_punct already removed micro-pause dots, so a remaining ". lowercase"
# is a real boundary the alias lowercased — safe to capitalize.
_SENT_START = re.compile(r"(^\s*|[.?!]['\"”’]?\s+)([a-z])")

def capitalize_sentences(text):
    return _SENT_START.sub(lambda m: m.group(1) + m.group(2).upper(), text)

# --- filler / disfluency removal (General cleanup — everyone says "uh") --------------------------
# A curated set of STANDALONE disfluency tokens, removed from BOTH the live preview and the committed
# text so they never reach the screen or the field. WHOLE-token match only (never substrings:
# "umbrella" / "ahead" / "error" are safe). Toggled via DUM_STRIP_FILLERS, gated at the live.py call
# sites — these helpers are pure (always strip) so they're directly testable. Distinct from
# live.HALLUCINATIONS (which drops a commit that is ENTIRELY filler); this removes a filler wherever it sits.
# Includes the nasal-grunt variants Parakeet actually emits — esp. "mm" / "hm" (it writes a nasal
# disfluency as "Mm", NOT "mmm") — found leaking into the live preview on real clips (2026-06-21).
# Deliberately EXCLUDED: "oh" (a real interjection/word), "m" (would break "I'm" and is a real flag/var),
# "yeah" (a meaningful affirmation) — those are kept verbatim.
FILLERS = frozenset({"uh", "uhh", "uhm", "um", "umm", "er", "erm",
                     "hm", "hmm", "mm", "mmm", "mhm", "ah", "eh", "huh"})

def _is_filler(tok):
    """True if `tok` (one token; surrounding punctuation/case ignored) is a standalone filler."""
    return re.sub(r"[^a-z]", "", tok.lower()) in FILLERS

# a standalone filler word + an optional comma it owned (longest-first so "umm" beats "um")
_FILLER_SUB = re.compile(r"\b(?:" + "|".join(sorted(FILLERS, key=len, reverse=True)) + r")\b,?",
                         re.IGNORECASE)

def strip_fillers(text):
    """Remove standalone fillers from SETTLED text (commit path); repair the seams and recapitalize a
    sentence start exposed by a removed leading filler. Pure + idempotent.
      "Uh, let's grep"        -> "Let's grep"
      "ship it. Uh, actually" -> "ship it. Actually"
      "I think um the plan"   -> "I think the plan"
      "umbrella" / "ahead"    -> unchanged (whole-token match)"""
    out = _FILLER_SUB.sub(" ", text)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)                 # no space left before punctuation
    out = re.sub(r"([.?!,;:])(?:\s*[.?!,;:])+", r"\1", out)    # collapse a run of punctuation -> the first
    out = re.sub(r"\s{2,}", " ", out).strip()                 # collapse gaps
    out = re.sub(r"^[,.;:!?]+\s*", "", out).strip()           # drop orphaned leading punctuation
    return capitalize_sentences(out)

def drop_fillers(words, at_start=False):
    """Filler-strip a LIVE-PREVIEW word list. A trailing filler-shaped token is simply absent until the
    re-transcribed window resolves it into a real word ("um" -> "umbrella") — that per-tick re-evaluation
    IS the one-tick gate, so no separate hold-state / reveal-path change is needed. If a leading filler
    is dropped at sentence start, recapitalize the new first word so preview casing == commit casing."""
    led = bool(words) and _is_filler(words[0])
    kept = [w for w in words if not _is_filler(w)]
    if at_start and led and kept and kept[0][:1].isalpha():
        kept[0] = kept[0][:1].upper() + kept[0][1:]
    return kept

# --- decapitalize stray boundary capitals (the CAP face of the over-eager-boundary bug) ----------
# An over-eager sentence boundary (Parakeet/VAD cutting mid-utterance) surfaces as a capitalized next
# word where there should be none — WITHIN one commit ("make The switch") or ACROSS commits (a
# continuation segment, the previous one not ending in .?!, typed inline starting with a capital).
# This lowercases ONLY a TIGHT, curated closed set of words that are NEVER names/identifiers, and ONLY
# when the word is NOT a real sentence start. The closed set IS the entire name-protection guarantee —
# adding any word that could plausibly be a name/variable would break it. Measured on the real
# 1,300-commit dogfood corpus: the cross-commit decap fires ~101×, ~97%+ correct continuations, and the
# curated set touches zero proper nouns. Pure + idempotent. Toggled via DUM_DECAP_CAPS (default ON in
# the launcher); gated at the live.py call sites. Distinct from the period face ("…a lot more. Smooth")
# which is separate boundary/MIN_SIL work and deliberately left alone here (fix the capital, not the dot).
SAFE_LOWER = frozenset({
    # articles, conjunctions, common prepositions, non-"I" pronouns, discourse markers — never names
    "the", "a", "an", "and", "but", "or", "so", "then", "now", "also", "because", "if",
    "when", "while", "to", "of", "in", "on", "at", "for", "with",
    "it", "its", "he", "she", "they", "we", "you", "this", "that", "these", "those",
    "there", "here", "okay", "ok", "no", "yeah", "yep", "well", "just", "maybe",
    # added 2026-06-22 from the 2,087-commit dogfood corpus: grammatical words in the same
    # never-a-name class as the set above, measured firing on real cross-commit continuations.
    "my", "as", "is", "what", "whatever", "more", "both", "oh",
})
_DECAP_STRIP = ".,!?;:\"'()[]{}“”‘’"   # surrounding punctuation/quotes stripped for the membership test

def _safe_to_lower(tok):
    """Return `tok` with its stray leading capital lowercased IF it is a SAFE_LOWER word wearing one,
    else None. The bare word (surrounding punctuation/quotes stripped) must be in SAFE_LOWER; the
    original token's leading/trailing punctuation is preserved on return. Protected (returns None):
    a single letter ("A"/"I"; "A" may be a label/grade, "I" the pronoun), "I"/"I'm"/"I'd"/..., all-caps
    acronyms (IT, US, SSH, HUD), and CamelCase identifiers (GitHub, WebSocket). Possessives/contractions
    normalize for the test: "It's"->"it's", "Its"->"its"."""
    core = tok.strip(_DECAP_STRIP)
    if len(core) < 2 or not core[0].isupper():
        return None                                  # single letter ("A"/"I") or not capitalized
    if core == "I" or core[:2] == "I'":              # I, I'm, I'd, I'll
        return None
    if core.isupper():                               # all-caps acronym: IT, US, SSH, HUD
        return None
    if any(c.isupper() for c in core[1:]):           # CamelCase identifier: GitHub, WebSocket
        return None
    low = core.lower()
    if low not in SAFE_LOWER and low.replace("'", "") not in SAFE_LOWER:
        return None
    for i, ch in enumerate(tok):                     # lower the first alpha char, keep surrounding punct
        if ch.isalpha():
            return tok[:i] + ch.lower() + tok[i + 1:]
    return None

def _tok_ends_sentence(tok):
    """True if a single token ends a sentence (.?! allowing a trailing closing quote/paren)."""
    t = tok.rstrip("\"'”’)")
    return bool(t) and t[-1] in ".!?"

def _ends_sentence(text):
    """True if `text` ends in . ! or ? (allowing a trailing closing quote/paren)."""
    return _tok_ends_sentence(text.rstrip())

def decap_interior(text, after_sentence=True):
    """Lowercase a stray boundary capital on a SAFE_LOWER word when it is NOT a real sentence start.
    A token is a sentence start iff it is the FIRST token and `after_sentence` is True, OR it is
    immediately preceded by a token that ended a sentence (.?! + optional closing quote). Every other
    token is offered to _safe_to_lower (which protects names/identifiers/acronyms/"I"). Pure +
    idempotent. Fixes the CAP face only — the period face is out of scope (left untouched)."""
    toks = text.split()
    if not toks:
        return text
    out = []
    prev_ended = bool(after_sentence)
    for tok in toks:
        if not prev_ended:
            lowered = _safe_to_lower(tok)
            if lowered is not None:
                tok = lowered
        out.append(tok)
        prev_ended = _tok_ends_sentence(tok)
    return " ".join(out)

class Stage:
    name = "stage"
    def run(self, text, ctx):
        return text, []

class PunctuationStage(Stage):
    name = "punct"
    def run(self, text, ctx):
        after = clean_punct(text)
        return after, _ev(self.name, text, after)

class SentenceCapStage(Stage):
    """Restore sentence-initial capitals dropped by the alias/LLM layers. Runs LAST."""
    name = "sentcap"
    def run(self, text, ctx):
        after = capitalize_sentences(text)
        return after, _ev(self.name, text, after)

class FuzzySymbolStage(Stage):
    """COMMIT-ONLY constrained fuzzy symbol recovery (Path 3 spike → flagged feature). Recovers
    recognizer near-misses that resolve to a KNOWN symbol's spoken form ("find model deer" ->
    find_model_dir). OFF unless DUM_FUZZY_SYMBOLS=1; never added to the live-preview path. Does
    NOT broaden the matching rule — all logic is the proven fuzzy_recover.recover() (multi-word
    anchors, one near-miss, known-symbol target). Index is built once at construction."""
    name = "fuzzysym"
    def __init__(self, alias_pairs):
        self._pairs = alias_pairs or []
        self._index = _fuzzy_index(self._pairs) if self._pairs else None
    def run(self, text, ctx):
        # ON when DUM_FUZZY_SYMBOLS is truthy, or (unset) when the dogfood master DUM_DOGFOOD_FULL is.
        fz = os.environ.get("DUM_FUZZY_SYMBOLS")
        on = (fz not in ("0", "", "false")) if fz is not None \
            else (os.environ.get("DUM_DOGFOOD_FULL", "0") not in ("0", "", "false"))
        if not self._pairs or not on:
            return text, []
        after, _events = _fuzzy_recover(text, self._pairs, index=self._index)
        return after, _ev(self.name, text, after)

class PhoneticStage(Stage):
    name = "phonetic"
    def __init__(self, corrector):
        self.c = corrector
    def run(self, text, ctx):
        after = self.c.correct(text)
        return after, _ev(self.name, text, after)

class LLMStage(Stage):
    name = "llm"
    def __init__(self, corrector):
        self.c = corrector
        self.fired, self.time = 0, 0.0
    def run(self, text, ctx):
        after, fired, dt = self.c.correct(text)
        if fired:
            self.fired += 1; self.time += dt
        return after, _ev(self.name, text, after)

class ExternalCorrectorStage(Stage):
    """THE PAID SEAM. If DUM_EXTERNAL_CORRECTOR is set to a command, call it per
    sentence over stdio: send `{"text","context"}\\n`, read `{"text"}\\n` back.
    Any failure (or no command) => passthrough, so the free core can never break."""
    name = "external"
    def __init__(self, cmd=None, timeout=5.0):
        self.cmd = cmd
        self.timeout = timeout
    def run(self, text, ctx):
        if not self.cmd:
            return text, []                       # seam present, unused
        try:
            req = json.dumps({"text": text, "context": ctx}) + "\n"
            p = subprocess.run(shlex.split(self.cmd), input=req, capture_output=True,
                               text=True, timeout=self.timeout)
            resp = json.loads(p.stdout.strip().splitlines()[-1])
            out = resp.get("text", text)
            if not isinstance(out, str) or not out.strip():
                out = text
        except Exception:
            out = text                            # never break the free core
        return out, _ev(self.name, text, out)

class PersonalCorrectionStage(Stage):
    """V2 SEAM — defined, NOT built. The future per-user personalization layer: applies corrections
    LEARNED from THIS user's telemetry (the correction_pair stream -> learn/proposer.py -> approved
    personal aliases), e.g. this user's "JITHUB" -> "GitHub" (see PRODUCT-VISION.md, the General-vs-
    Personal rule). In V1 there is no learner and no data, so this is a strict passthrough — defined
    now so V2 is purely additive: it slots in here, gated by DUM_PERSONAL_CORRECTIONS, and can never
    break the free core (exactly like ExternalCorrectorStage). Populated only in V2."""
    name = "personal"
    def __init__(self, corrections=None):
        self.corrections = corrections or {}   # {spoken_form: canonical}; empty in V1, filled by V2
    def run(self, text, ctx):
        if not self.corrections or os.environ.get("DUM_PERSONAL_CORRECTIONS", "0") in ("0", "", "false"):
            return text, []                     # seam present, inert until V2 supplies learned data
        # V2 applies learned per-user corrections here (phrase-alias style). Intentionally unbuilt.
        return text, []

# ---------------------------------------------------------------------------
# Protect-list guard (session theme, 2026-06-20): common English words and known names must NOT be
# silently rewritten into IT jargon by the phonetic/LLM stages. Telemetry over 478 real commits
# showed ~20 such corruptions (get->git, grab->grep, group->grep, Rado->redis, jasně->json,
# "a lot"->a_lo) for every ~3 genuine jargon fixes (nginx/postgres/localhost). RULE: a protected
# common word/name is reverted to what the recognizer heard UNLESS the same sentence clearly carries
# command/code context (an explicit cue). This stage is the deterministic source of truth for that
# rule and runs LAST (after every corrector, before sentence-capitalization).

# protected source token (lowercased, accent-stripped) -> jargon target(s) it must not silently become
_FORBIDDEN = {
    "get": {"git"}, "grab": {"grep"}, "group": {"grep"},
    "rado": {"redis"}, "rados": {"redis"}, "rado's": {"redis"},
    "jasne": {"json"},                                   # SK "jasně" (accent-stripped)
}
# multiword protected phrase (accent-stripped, space-joined) -> single jargon token it must not become
_FORBIDDEN_PHRASE = {"a lot": "a_lo"}
# per-target command/code cues. If ANY appears in the sentence, the swap is "clear command context"
# and is allowed to stand. Kept STRICT on purpose (logs/errors alone are NOT a clear command cue —
# "grab the logs" is ordinary speech), so the default is to protect.
_CUES = {
    "git":   {"clone", "commit", "commits", "push", "pushed", "pull", "checkout", "branch",
              "branches", "merge", "rebase", "stash", "init", "remote", "origin", "repo", "repos",
              "repository", "staged", "diff", "fetch"},
    "grep":  {"|", "pipe", "regex", "pattern", "awk", "sed", "stdout", "stderr", "-r", "-i", "-e", "-v"},
    "redis": {"cache", "caching", "server", "port", "6379", "queue", "pubsub", "database", "db"},
    "json":  {"parse", "parsed", "payload", "api", "schema", "endpoint", "curl", "field",
              "object", "array", "serialize", "deserialize"},
}

def _pnorm(tok):
    """Lowercase + strip surrounding punctuation + drop accents, for token comparison."""
    t = tok.strip(".,!?;:\"'()[]{}").lower()
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")

def _has_cue(target, raw_norm_set):
    return bool(_CUES.get(target, set()) & raw_norm_set)

class ProtectedWordsStage(Stage):
    """Revert common-word/name -> jargon corruptions (get->git, grab->grep, Rado->redis, ...) by
    comparing the final text against the RAW recognizer output stashed in ctx['_raw_input']. Only the
    specific forbidden swaps are touched; genuine jargon fixes (nginx, postgres, localhost) and all
    other edits pass through untouched. See the session-theme note above."""
    name = "protect"
    def run(self, text, ctx):
        raw = (ctx or {}).get("_raw_input")
        if not raw or not text:
            return text, []
        raw_toks, cur_toks = raw.split(), text.split()
        raw_norm = {_pnorm(t) for t in raw_toks}
        sm = difflib.SequenceMatcher(None, [_pnorm(t) for t in raw_toks],
                                     [_pnorm(t) for t in cur_toks])
        out = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal" or tag == "insert":
                out.extend(cur_toks[j1:j2])
            elif tag == "delete":
                continue                                  # a stage removed these (e.g. dedup) — keep removed
            else:                                         # replace
                out.extend(self._maybe_revert(raw_toks[i1:i2], cur_toks[j1:j2], raw_norm))
        after = " ".join(out)
        return after, _ev(self.name, text, after)

    def _maybe_revert(self, raw_span, cur_span, raw_norm):
        # 1:1 token swaps inside the block (the common case: get->git, grab->grep)
        if len(raw_span) == len(cur_span):
            res = []
            for r, c in zip(raw_span, cur_span):
                rs, cs = _pnorm(r), _pnorm(c)
                if rs in _FORBIDDEN and cs in _FORBIDDEN[rs] and not _has_cue(cs, raw_norm):
                    res.append(r)                         # restore raw token (keeps casing/punct)
                else:
                    res.append(c)
            return res
        # phrase swap (e.g. "a lot" -> "a_lo"): protect unconditionally
        raw_join = " ".join(_pnorm(t) for t in raw_span)
        cur_join = "_".join(_pnorm(t) for t in cur_span) if len(cur_span) > 1 else _pnorm(cur_span[0])
        if _FORBIDDEN_PHRASE.get(raw_join) == cur_join:
            return list(raw_span)
        return list(cur_span)


class CorrectionPipeline:
    def __init__(self, stages):
        self.stages = stages
    def run(self, text, ctx=None):
        ctx = ctx or {}
        ctx.setdefault("_raw_input", text)              # pristine recognizer output for the protect guard
        events = []
        for s in self.stages:
            t0 = time.monotonic()
            text, evs = s.run(text, ctx)
            dt_ms = round((time.monotonic() - t0) * 1000.0, 3)
            for e in evs:                       # only stages that changed text emit an event
                e.setdefault("ms", dt_ms)       # per-stage timing for the embedded stage trace
            events.extend(evs)
        return text, events
