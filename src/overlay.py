#!/usr/bin/env python3
"""
Word-by-word live overlay (Milestone A).

Streams *stabilized* words to the cursor as you speak, then does ONE minimal
backspace+retype "reconcile" at sentence-final to apply the correction pipeline's
fixes (e.g. grab->grep). The hard constraint: we cannot read the screen, only
type characters and send backspaces - so a wrong diff deletes real text. The
design avoids that:

  * During speech we ONLY append. A word "locks" once two consecutive growing-
    window previews agree on it (it sits in their common prefix). Locked words are
    typed and never deleted while you talk -> no flicker thrash.
  * At sentence-final we reconcile what's on screen to the corrected sentence with
    a single cursor-at-end edit: backspace past the common char prefix, retype the
    rest. This also completes any not-yet-locked tail word(s).

The two decision functions below are PURE (unit-tested in test_overlay.py).
OverlayTyper is the thin side-effectful keystroke layer (pynput), with a `dry`
mode that records ops instead of sending them - for safe testing without a cursor.
"""
import os, re, difflib

_PUNCT = re.compile(r"[^\w]+")


def _norm_word(w):
    """A word stripped to its bare alphanumerics, lowercased - for deciding whether
    two words differ *meaningfully* (grab vs grep) or only cosmetically (Hello. vs
    Hello,, github vs GitHub)."""
    return _PUNCT.sub("", w).lower()


# Onset disfluencies/fillers Parakeet tends to emit as a spurious first word on a
# single early preview, then revise away as more audio arrives ("what"/"oh"/"okay"
# /"yeah" flashing then vanishing). We refuse to EAGER-show these from one preview:
# a genuine "okay" still appears one preview later once two previews agree, but a
# hallucinated one never flashes. Normalized (lowercase, no punctuation).
# Onset breath artifacts: a breath in/out at the very start of dictation reliably
# decodes to one of these across TWO consecutive previews, so it clears the two-preview
# agreement gate and would show. We hold a lone leading one until a real word joins it -
# the breath gets revised away before that happens (never shown), while a genuine
# "yeah let's go" / "sh deploy.sh" shows intact one preview later. Scoped narrowly so
# genuine "so"/"okay" openers are NOT delayed through this path. Verified by Elias:
# "yeah" (normal breath) and "h"/"sh" (loud breath) are the onset artifacts seen.
BREATH_FILLERS = frozenset({"yeah", "h", "sh"})

# Onset disfluencies/fillers Parakeet emits as a spurious first word on a single early
# preview, then revises away. We refuse to EAGER-show these from one preview (the breath
# artifacts above are included so a single-preview flash can't sneak them in either).
EAGER_FILLERS = frozenset({
    "uh", "um", "uhm", "hmm", "mm", "mhm", "hm", "ah", "huh", "oh",
    "yeah", "yep", "yup", "okay", "ok", "well", "so", "what",
}) | BREATH_FILLERS


def stable_prefix(prev_words, cur_words):
    """The leading words two consecutive previews AGREE on (common normalized prefix),
    in their current surface form. This is the 'stable' part of the transcript safe to
    show; words still churning past this point are left for the next preview / commit.

    Agreement is on the NORMALIZED word (punctuation/case-insensitive): Parakeet flips
    trailing punctuation on early words as the growing window fills (Okay -> Okay,), and
    exact matching there would stall every later word, making whole sentences appear at
    once. We treat such cosmetically-jittery words as agreed and let the end-of-sentence
    reconcile clean punctuation up for free."""
    L = 0
    m = min(len(prev_words), len(cur_words))
    while L < m and _norm_word(prev_words[L]) == _norm_word(cur_words[L]):
        L += 1
    return cur_words[:L]


def streaming_prefix(prev_words, cur_words, eager_first=False, at_start=False, stable=None):
    """What the overlay should show on screen THIS preview. Normally the two-preview
    stable prefix; but when `eager_first` is set and nothing has agreed yet, fall back
    to the first word of this single preview so typing STARTS a preview sooner.

    `stable` lets the caller SUPPLY the stable prefix instead of computing two-preview
    agreement here - Phase 1 passes the age-based reveal prefix (see age_stable_count),
    while all the onset gates below (breath/filler/eager) still apply unchanged. Pass a
    list (possibly empty = nothing stable yet) to use it; None = compute agreement (old).

    The overlay reconciles its on-screen text TO this string each tick, which both
    appends newly-stable words (cheap, no backspaces) AND corrects an earlier word the
    model has since revised - turning an eager mis-guess from a wrong word that lingers
    until commit into a ~one-preview flash that self-corrects as more audio arrives.

    `at_start` (nothing typed yet) additionally suppresses an all-breath stable prefix:
    a breath at the onset reliably decodes to "yeah" / "h" / "sh" across TWO previews,
    so it clears the agreement gate and would show. We hold such a lone leading token
    until a real word joins it - the breath gets revised away before that happens (never
    shown), while a genuine "yeah let's go" shows intact one preview later. Scoped to
    BREATH_FILLERS so genuine "so"/"okay" openers are NOT delayed through this path."""
    sp = stable_prefix(prev_words, cur_words) if stable is None else stable
    if sp:
        if at_start and all(_norm_word(w) in BREATH_FILLERS for w in sp):
            return []
        return sp
    if eager_first and cur_words and _norm_word(cur_words[0]) not in EAGER_FILLERS:
        # Don't eager-flash a leading onset filler from a single preview - wait for
        # two-preview agreement so a hallucinated "what"/"oh"/"okay" never shows. A
        # real filler is confirmed (and shown) one preview later. See EAGER_FILLERS.
        return cur_words[:1]
    return []


def alias_prefix_set(alias_token_seqs):
    """Set of all PROPER prefixes (normalized token tuples) of the given alias spoken-forms,
    for hold_alias_prefix. E.g. [["v","s","code"], ["vs","code"]] -> {("v",), ("v","s"), ("vs",)}.
    A full alias is NOT its own prefix, so a RESOLVED alias ("VS Code") reveals immediately.
    Tokens are normalized with _norm_word so the membership test matches on-screen surface words
    case/punctuation-insensitively."""
    out = set()
    for seq in alias_token_seqs:
        toks = [t for t in (_norm_word(x) for x in seq) if t]
        for k in range(1, len(toks)):          # proper prefixes only: 1 .. len-1
            out.add(tuple(toks[:k]))
    return frozenset(out)


def hold_alias_prefix(words, prefix_set):
    """Withhold trailing words that form the START of a known multi-word vocab alias, so a phrase
    like "V S code" reveals as "VS Code" in one shot instead of typing the literal tokens and
    retyping once the alias fires. Returns `words` with the longest trailing run whose normalized
    tokens are a proper alias-prefix removed.

    Suffix-only by design: when the alias completes, the corrector has already collapsed the tokens
    to the canonical form (no longer a prefix) so the whole phrase reveals; when the prefix BREAKS
    (you actually said "V S Go"), the trailing run stops matching and everything reveals - one preview
    later, never a backspace-retype. Pure; never mutates kept tokens. Empty prefix_set => unchanged.
    commit() retypes the full corrected text, so a held phrase is never lost even if held to commit."""
    if not prefix_set or not words:
        return words
    norm = [_norm_word(w) for w in words]
    maxk = min(len(words), max((len(p) for p in prefix_set), default=0))
    for k in range(maxk, 0, -1):               # largest in-progress run first
        if tuple(norm[-k:]) in prefix_set:
            return words[:-k]
    return words


def age_stable_count(starts, window_len_s, margin):
    """How many leading words are stable *by audio age* - Phase 1 (one-by-one reveal).

    A word is age-stable once its SUCCESSOR starts at or before `window_len_s - margin`,
    i.e. the word's right boundary sits at least `margin` seconds behind the live edge -
    enough right-context that the recognizer won't revise it. We always leave >=1 trailing
    word unstable (the word still being spoken), so the last word never reveals on its own
    half-formed guess. `starts` are per-word start times in seconds within the decode
    window (as `transcribe_words()` returns). Returns the count of leading stable words.

    This is the lock-and-trim lock loop (live.py) generalized to an arbitrary margin, so
    ONE function drives both thresholds: the conservative LOCK margin (commit the word and
    trim its audio) and the smaller DISPLAY margin (reveal the word on screen sooner). A
    smaller margin => larger count => earlier reveal; `margin == LOCK_MARGIN_S` reproduces
    the lock count exactly. Because it keys on the audio timeline (not on two consecutive
    previews agreeing), it skips the extra preview the old two-preview gate waited for -
    the cause of the 3-4 word clumps / "freeze while it confirms" feel."""
    cutoff = window_len_s - margin
    n = 0
    while n + 1 < len(starts) and starts[n + 1] <= cutoff:
        n += 1
    return n


def reconcile_ops(typed, target):
    """Minimal cursor-at-end edit from `typed` to `target`.

    Backspace everything after the longest common character prefix, then type the
    remainder. Returns (n_backspace, to_type). Cursor-at-end only (no repositioning),
    so we can't exploit a common suffix - but it's always correct and stays at the
    end where dictation leaves it."""
    n = 0
    m = min(len(typed), len(target))
    while n < m and typed[n] == target[n]:
        n += 1
    return len(typed) - n, target[n:]


def reconcile_words(typed, target):
    """Lower-churn reconcile: skip leading words that differ only cosmetically
    (punctuation/case) - keep them as typed - then char-level reconcile from the
    first *meaningfully* changed word onward.

    This is the daily-driver win: `Hello.`->`Hello,` (cosmetic) costs zero edits,
    while `grab`->`grep` or `q`->`kubectl` still gets fixed. We accept minor
    cosmetic drift (a stray live period, lowercase `github`) in exchange for not
    wiping+retyping the sentence tail on every pause. Returns (n_backspace, to_type)."""
    tw, fw = typed.split(), target.split()
    cut = 0
    while cut < min(len(tw), len(fw)) and _norm_word(tw[cut]) == _norm_word(fw[cut]):
        cut += 1
    # char index in typed/target at the start of the first meaningfully-diff word
    t_keep = len(" ".join(tw[:cut]))
    f_keep = len(" ".join(fw[:cut]))
    # char-level reconcile of the remainder (the leading space, if any, is shared)
    nb, to_type = reconcile_ops(typed[t_keep:], target[f_keep:])
    return nb, to_type


def min_edit_script(typed, target, max_spans=8):
    """Multi-span minimal cursor edit from `typed` to `target` (Phase 2 - smart
    cursor-edit). Returns a list of `(start, n_backspace, text)` ops in ascending
    `start` order; the caller (OverlayTyper.apply_edits) applies them RIGHT-TO-LEFT
    so each left span's offset stays valid as the right ones rewrite.

    `start` is the char offset IN `typed` where a changed region begins, `n_backspace`
    chars are deleted there, and `text` is typed in their place. Char-level diff
    (difflib) so a fix touches only the changed characters instead of wiping the tail:
      * get push -> git push      => 1 span (e->i): position, 1 backspace, type 'i'
      * grab the logs -> grep the logs.  => 2 spans (ab->ep, append '.')
    vs the cursor-at-end reconcile_ops which would backspace+retype everything after
    the first changed char.

    Returns None when the diff fragments into more than `max_spans` regions
    (pathological interleaving where in-place editing isn't a win) so the caller
    falls back to the always-correct backspace-retype reconcile. autojunk=False keeps
    the diff deterministic on short strings (no 'popular character' heuristic)."""
    sm = difflib.SequenceMatcher(None, typed, target, autojunk=False)
    edits = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        edits.append((i1, i2 - i1, target[j1:j2]))
    if len(edits) > max_spans:
        return None
    return edits


class OverlayTyper:
    """Side-effectful keystroke layer. Tracks `typed` = the exact characters
    currently on screen for the in-progress sentence, so reconcile diffs are exact.

    dry=True records ops to `self.ops` and prints them instead of sending keystrokes
    (test/observe without touching a real cursor)."""

    def __init__(self, dry=False, max_backspace=300, platform=None, quiet=None,
                 min_edit=None, max_travel=200):
        self.dry = dry
        # quiet dry mode still records ops (tests read them) but skips the per-op print -
        # used by bench/replay so the regression output isn't buried in keystroke logs.
        self.quiet = (os.environ.get("DUM_OVERLAY_QUIET") == "1") if quiet is None else quiet
        self.max_backspace = max_backspace
        # Phase 2: smart cursor-edit. When on, exact (commit-time) reconciles use
        # min_edit_script + apply_edits (in-place surgical edits) instead of the
        # cursor-at-end backspace-retype. DEFAULT ON (Decision B1, feel-checked clean in
        # VS Code + terminal 2026-06-17); set DUM_MIN_EDIT=0 to fall back to the
        # backspace-retype path. The travel cap + min_edit_script's None fragmentation
        # bail still fall back per-reconcile when an edit isn't a clean in-place win.
        self.min_edit = (os.environ.get("DUM_MIN_EDIT", "1") != "0") if min_edit is None else min_edit
        self.max_travel = max_travel  # cap on total arrow-key movement before falling back
        self.platform = platform      # if set, type via it (layout-independent on mac)
        self.typed = ""
        self.ops = []                 # (kind, payload) log, for dry mode + tests
        self.kb = None
        self._Key = None
        if not dry:
            from pynput.keyboard import Controller, Key
            self.kb = Controller()
            self._Key = Key

    def _type(self, s):
        if self.dry:
            self.ops.append(("type", s))
            if not self.quiet:
                print(f"   [overlay] type {s!r}", flush=True)
        elif self.platform is not None:
            self.platform.type_text(s)    # Unicode insertion - not mangled by Slovak/etc layouts
        else:
            self.kb.type(s)

    def _backspace(self, n):
        if n <= 0:
            return
        if self.dry:
            self.ops.append(("backspace", n))
            if not self.quiet:
                print(f"   [overlay] backspace x{n}", flush=True)
        else:
            for _ in range(n):
                self.kb.press(self._Key.backspace)
                self.kb.release(self._Key.backspace)

    def _arrow(self, key_name, n):
        if n <= 0:
            return
        if self.dry:
            self.ops.append((key_name, n))
            if not self.quiet:
                print(f"   [overlay] {key_name} x{n}", flush=True)
        else:
            key = getattr(self._Key, key_name)
            for _ in range(n):
                self.kb.press(key)
                self.kb.release(key)

    def _move(self, delta):
        """Move the insertion point by `delta` chars: >0 right, <0 left, 0 no-op."""
        if delta > 0:
            self._arrow("right", delta)
        elif delta < 0:
            self._arrow("left", -delta)

    def apply_edits(self, target, edits):
        """Phase 2 smart cursor-edit: apply a `min_edit_script` in place. Returns True
        if applied, False if the total arrow travel would exceed `max_travel` (caller
        falls back to backspace-retype). Cursor starts and ends at the buffer end.

        Spans are applied RIGHT-TO-LEFT: editing a rightward span never shifts the char
        offsets of leftward ones (they sit at smaller, untouched positions), so each
        span's `start` stays valid in the live buffer. For each span we move the cursor
        to its end, backspace its old chars (deleting leftward to `start`), type the new
        text, then finally return the cursor to the end of `target`."""
        cur = len(self.typed)
        travel = 0
        for start, nb, text in reversed(edits):
            travel += abs((start + nb) - cur)
            cur = start + len(text)
        travel += abs(len(target) - cur)
        if travel > self.max_travel:
            return False
        cur = len(self.typed)
        for start, nb, text in reversed(edits):
            self._move((start + nb) - cur)   # cursor to span end
            self._backspace(nb)              # delete leftward -> cursor at `start`
            self._type(text)
            cur = start + len(text)
        self._move(len(target) - cur)        # back to end
        self.typed = target
        return True

    def append_words(self, words):
        """Append newly-locked words at the cursor (with a leading space if needed)."""
        if not words:
            return
        chunk = (" " if self.typed else "") + " ".join(words)
        self._type(chunk)
        self.typed += chunk

    def reconcile(self, target, exact=False):
        """Edit the on-screen text to `target`. Returns True if applied, False if the
        edit would exceed max_backspace (a safety bail-out - leaves text untouched).

        exact=False (streaming): low-churn word diff that SKIPS cosmetic-only leading
        words (punctuation/case) to avoid retyping the tail every preview.
        exact=True (commit): char-level diff that reproduces `target` EXACTLY - so the
        final text keeps Parakeet's real punctuation (?, .) and casing instead of the
        cosmetic-skipped version (which silently dropped sentence-final '?'/'.').

        When `min_edit` is on, an exact reconcile first tries the surgical multi-span
        in-place edit (min_edit_script + apply_edits); it falls back to the cursor-at-end
        backspace-retype below if the diff is too fragmented or the cursor travel exceeds
        the cap. The fallback (and the streaming exact=False path) is unchanged."""
        if exact and self.min_edit:
            edits = min_edit_script(self.typed, target)
            if edits is not None and self.apply_edits(target, edits):
                return True
            # too fragmented / too much travel -> safe backspace-retype below
        nb, to_type = (reconcile_ops if exact else reconcile_words)(self.typed, target)
        if nb > self.max_backspace:
            return False
        self._backspace(nb)
        self._type(to_type)
        self.typed = target
        return True

    def finish(self, trailing=" "):
        """Type a trailing separator and end the sentence (resets typed-state)."""
        if trailing:
            self._type(trailing)
        self.typed = ""

    def reset(self):
        self.typed = ""
