#!/usr/bin/env python3
"""
Constrained fuzzy alias RECOVERY - SPIKE LOGIC ONLY. NOT wired into the live pipeline.

Goal: recover recognizer near-misses (find model DEER -> find_model_dir, min EDITED script ->
min_edit_script) that the EXACT phrase-alias layer can't catch - but ONLY when the span resolves
to a KNOWN symbol's spoken form. Deterministic, no global fuzzy, no standalone word rules.

Hard constraints (deliberately tight - this is a kill-or-keep probe):
  - multi-word spoken forms only (len >= 2): a single mangled word never recovers on its own
    ("deer -> dir" alone is forbidden; "model deer" ~ "model dir" recovers because "model" anchors it).
  - at most `max_fuzzy` words in the window may be near-misses; the rest must match EXACTLY (anchors).
  - a near-miss = metaphone-equal AND fuzz>=0.45, OR fuzz>=0.80. Pure-exact windows are skipped
    (those are the regular alias's job).
  - target is always a known (say -> want) pair from the loaded vocab; never an open dictionary.

If later promoted to the live pipeline, gate behind DUM_FUZZY_RECOVER (off by default). For now it
is import-only and exercised solely by fuzzy_recovery_spike.py.
"""
import re
import jellyfish
from rapidfuzz import fuzz

METAPHONE_FUZZ_MIN = 0.45
FUZZ_MIN = 0.80


def _key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _near(a, b):
    """'exact' | 'near' | None - how word a relates to alias word b."""
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return None
    if ka == kb:
        return "exact"
    r = fuzz.ratio(ka, kb) / 100
    meta = len(ka) >= 2 and len(kb) >= 2 and jellyfish.metaphone(ka) == jellyfish.metaphone(kb)
    if (meta and r >= METAPHONE_FUZZ_MIN) or r >= FUZZ_MIN:
        return "near"
    return None


def build_index(alias_pairs):
    """Index multi-word aliases by first- and second-word keys so recover() only fully-checks
    aliases sharing an exact ANCHOR word with the window. Lossless: any firing window has an exact
    first word (idx0) OR an exact second word with a fuzzy first (idx1) - since at most one word is
    fuzzy and n>=2, at least one of the first two words is an exact anchor. Build once, reuse per
    utterance (commit-time)."""
    idx0, idx1 = {}, {}
    for say, want in alias_pairs:
        if len(say) < 2:
            continue
        idx0.setdefault(_key(say[0]), []).append((say, want))
        idx1.setdefault(_key(say[1]), []).append((say, want))
    return idx0, idx1


def recover(text, alias_pairs, max_fuzzy=1, index=None):
    """alias_pairs: list of (say_tokens:list[str], want:str). Returns (new_text, events) where
    events = [(input_span, want, alias_say)]. Non-overlapping; longest spoken form + fewest fuzzy
    words win. Trailing punctuation of the span is preserved on the replacement. `index` (from
    build_index) makes this O(tokens) instead of O(aliases x tokens) - SAME matches, just faster."""
    toks = text.split()
    keys = [_key(t) for t in toks]
    idx0, idx1 = index if index is not None else build_index(alias_pairs)
    matches, seen = [], set()
    for i in range(len(toks)):
        cands = list(idx0.get(keys[i], ()))
        if i + 1 < len(toks):
            cands += idx1.get(keys[i + 1], ())          # window starts at i with a fuzzy first word
        for say, want in cands:
            n = len(say)
            if i + n > len(toks) or (id(say), i) in seen:
                continue
            seen.add((id(say), i))
            states = [_near(toks[i + j], say[j]) for j in range(n)]
            if any(s is None for s in states):
                continue
            nf = states.count("near")
            if nf == 0 or nf > max_fuzzy:
                continue                               # 0 = exact (regular alias); >max = too loose
            matches.append((n, -nf, i, n, want, " ".join(toks[i:i + n]), " ".join(say)))
    matches.sort(key=lambda m: (-m[0], m[1], m[2]))
    used = [False] * len(toks)
    repl = {}
    for _n0, _nf, i, n, want, inp, say in matches:
        if any(used[i:i + n]):
            continue
        for k in range(i, i + n):
            used[k] = True
        repl[i] = (n, want, inp, say)
    out, events, i = [], [], 0
    while i < len(toks):
        if i in repl:
            n, want, inp, say = repl[i]
            lead = re.match(r"^[^\w]+", toks[i])
            trail = re.search(r"[^\w]+$", toks[i + n - 1])
            out.append((lead.group(0) if lead else "") + want
                       + (trail.group(0) if trail else ""))
            events.append((inp, want, say))
            i += n
        else:
            out.append(toks[i])
            i += 1
    return " ".join(out), events
