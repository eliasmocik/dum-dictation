#!/usr/bin/env python3
"""
Correction layer, precision-first.

Lesson from the aggressive v1: blind phonetic/fuzzy matching against the term
list boosts recall but wrecks precision -- "out"->OAuth, "the container"->container
(article absorbed). The looseness that fixes grab->grep is the SAME looseness that
breaks out->OAuth. So this version is conservative and only fires when it's safe:

  1. PHRASE aliases  -- explicit multi-word mishears (regex)         e.g. "engine x"->nginx
  2. TOKEN aliases   -- non-English-word mishears (safe to force)    e.g. "qctl"->kubectl, "jso"->JSON
  3. single-token close match -- metaphone-equal AND fuzzy>=0.6, OR fuzzy>=0.90,
       with a STOPWORD guard (never touch common words like out/get/the)

Real-word ambiguities (grab->grep, get->git, reddish->redis) are deliberately
NOT handled here -- only context can tell "grab the port" (grep) from "grab a
coffee" (grab). Those are the job of the context-aware LLM layer.
"""
import re
import jellyfish
from rapidfuzz import fuzz

def _key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())

# multi-word mishears -> canonical (applied as regex, case-insensitive)
PHRASE_ALIASES = [
    (r"\bcome ?back\s+(?:to|tl)\b", "kubectl"),
    (r"\bcube[\s,]+(?:c ?t ?l|control)\b", "kubectl"),  # "cube CTL"/"cube, CTL"/"cube control" -> kubectl
    (r"\bengine\s+x\b", "nginx"),
    (r"\blocal\s+host\b", "localhost"),
]
# single non-word mishears -> canonical (safe: these aren't real English words)
TOKEN_ALIASES = {
    "qctl": "kubectl", "kubecontrol": "kubectl", "cubecontrol": "kubectl",
    "ngx": "nginx", "ngn": "nginx", "jso": "JSON",
}
# never auto-correct these (common words that sound/look like a term)
STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "at",
    "then", "we", "it", "is", "are", "be", "need", "run", "open", "add", "write",
    "out", "up", "new", "set", "get", "got", "let", "this", "that", "part", "port",
    "main", "over", "change", "first", "query", "apply", "deploy", "check", "fix",
    "validate", "against", "from", "into", "tail", "logs", "flow", "handler", "cache",
}

class PhoneticCorrector:
    def __init__(self, terms, thresh=0.90, extra_phrase_aliases=None):
        self.terms = terms
        self.thresh = thresh
        self.tk = {t: _key(t) for t in terms}
        self.meta = {t: jellyfish.metaphone(self.tk[t]) for t in terms}
        # ADDITIVE: shipped base first, then any pack-loaded aliases (vocab.load_phrase_aliases).
        # With no extras this is exactly PHRASE_ALIASES -> behaviour is identical by construction
        # (no migration, no identity drift).
        self.phrase_aliases = PHRASE_ALIASES + list(extra_phrase_aliases or [])

    def _single(self, tok):
        sk = _key(tok)
        if len(sk) < 3 or sk in STOPWORDS:
            return None
        if sk in TOKEN_ALIASES:
            return TOKEN_ALIASES[sk]
        m = jellyfish.metaphone(sk)
        best, bs = None, 0.0
        for t in self.terms:
            tkey = self.tk[t]
            if sk == tkey:
                return None  # already correct
            sim = fuzz.ratio(sk, tkey) / 100
            ok = (m and m == self.meta[t] and sim >= 0.6) or sim >= self.thresh
            if ok and sim > bs:
                bs, best = sim, t
        return best

    def correct(self, text):
        # explicit multi-word aliases first (shipped base + pack-loaded, see __init__)
        for pat, rep in self.phrase_aliases:
            text = re.sub(pat, rep, text, flags=re.IGNORECASE)
        # collapse immediate duplicate words ("cache cache" -> "cache") - ASR stutter.
        # BUT keep the repeat when the first copy carries trailing punctuation ("very,
        # very important"): that comma is a deliberate pause/emphasis, not a stutter, and
        # collapsing it drops a meaningful word. True stutter has no punctuation between
        # the copies, so it still collapses ("how how do I" -> "how do I").
        toks, dd = text.split(), []
        for w in toks:
            if (dd and _key(dd[-1]) == _key(w) and _key(w)
                    and not re.search(r"[^\w]$", dd[-1])):
                continue
            dd.append(w)
        # conservative single-token correction
        out = []
        for tok in dd:
            term = self._single(tok)
            if term:
                trail = re.search(r"[^\w]+$", tok)
                out.append(term + (trail.group(0) if trail else ""))
            else:
                out.append(tok)
        return re.sub(r"\s+", " ", " ".join(out)).strip()
