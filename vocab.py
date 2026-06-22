#!/usr/bin/env python3
"""
SEAM 2 — vocab as a pluggable data source.

Base vocab (free, shipped) loads from base files. Additional vocab packs load from
an optional extra directory (env HOVOR_VOCAB_DIR). Paid/closed vocab packs are then
just .txt files dropped into that dir — no code change. The free core never depends
on the extra dir existing.
"""
import os
import re
from pathlib import Path

def load_terms(base_files, extra_dir=None):
    """Merge term lists: base files first, then every *.txt in extra_dir. Dedup
    case-insensitively, preserve first-seen casing/order."""
    terms, seen = [], set()

    def _add(path):
        p = Path(path)
        if not p.exists():
            return
        for line in p.read_text(errors="ignore").splitlines():
            t = line.strip()
            if t and not t.startswith("#") and t.lower() not in seen:
                seen.add(t.lower())
                terms.append(t)

    for f in base_files:
        _add(f)
    extra_dir = extra_dir or os.environ.get("HOVOR_VOCAB_DIR")
    if extra_dir and Path(extra_dir).is_dir():
        for f in sorted(Path(extra_dir).glob("*.txt")):
            _add(f)
    return terms


def load_phrase_aliases(extra_dir=None):
    """Load phrase-alias packs (*.aliases) from extra_dir (or $HOVOR_VOCAB_DIR).

    Each line is `spoken form => CanonicalForm`; blank lines, #comments and
    malformed lines (no `=>`, empty side) are skipped. The spoken form (left
    side) is compiled to a word-bounded, whitespace-flexible regex PATTERN STRING
    ("ten stack" -> r"\\bten\\s+stack\\b"), matching the shape of the hardcoded
    PHRASE_ALIASES in correct_phonetic.py so the two merge uniformly (same
    re.sub(..., flags=IGNORECASE) loop). Returns a list of (pattern, replacement)
    tuples; empty when no packs are present. Value mechanism for the tech-vocab
    dictionary (GLOBAL-VOCAB-PLAN.md G1a) — additive, never replaces the base."""
    out = []
    extra_dir = extra_dir or os.environ.get("HOVOR_VOCAB_DIR")
    if not (extra_dir and Path(extra_dir).is_dir()):
        return out
    for f in sorted(Path(extra_dir).glob("*.aliases")):
        for line in f.read_text(errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=>" not in line:
                continue
            lhs, rhs = (s.strip() for s in line.split("=>", 1))
            if not lhs or not rhs:
                continue
            pat = r"\b" + r"\s+".join(re.escape(w) for w in lhs.split()) + r"\b"
            out.append((pat, rhs))
    return out
