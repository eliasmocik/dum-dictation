#!/usr/bin/env python3
"""
Context-aware LLM correction stage (Layer 3, the hard 5%).

Runs AFTER the conservative phonetic layer. It is GATED: only fires when a
sentence contains a real-word homophone "suspect" (grab/get/...) that the
deterministic layer must not touch (since those words are valid English).
This keeps the LLM off the latency path for the easy 95%.

Local model via MLX (Apple Silicon). Default: Llama-3.2-1B-Instruct-4bit (~700MB,
downloaded once to the HF cache) — chosen over the 3B for ~2.5x lower commit latency
at equal task accuracy (constrained output + IT-term filter). Override with
HOVOR_LLM_MODEL. Long-term on Mac this is replaceable by Apple's on-device
FoundationModels (free, no download) behind the same interface.

Mandate is deliberately narrow: fix ONLY misheard tech terms, never rephrase. The model
emits "wrong->right" pairs (or NONE); we apply each only if `right` is a validated IT term.

Usage (standalone smoke test):
  .venv/bin/python llm_stage.py
"""
import os, queue, re, threading, time

# Default homophone-fix model. 1B (vs 3B) is ~2.5x faster (~350ms vs ~900ms) and, with the
# constrained "wrong->right" output + IT-term validation filter, at least as accurate on the
# narrow task — so it stays on the latency path without the ~1s commit stall. Override with
# HOVOR_LLM_MODEL (e.g. the 3B for max accuracy on a faster machine).
DEFAULT_LLM_MODEL = os.environ.get("HOVOR_LLM_MODEL", "mlx-community/Llama-3.2-1B-Instruct-4bit")

def _key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())

# real English words that are often misheard developer terms -> gate the LLM on these
SUSPECTS = {
    "grab", "grabbed", "get", "gets", "getting", "got", "see", "sea",
    "make", "makes", "cad", "shell", "colonel", "kernel", "node", "no",
}

# Constrained output: the model emits ONLY the corrections as "wrong->right" pairs (or
# NONE), not the whole rewritten sentence. ~2x faster (generation time scales with output
# length, and a few words << a full sentence), so it stays on the latency path without the
# ~1s stall the full-sentence rewrite cost. We apply the pairs ourselves and validate each
# against the IT-term set, so the model can't rephrase/reorder/touch punctuation.
SYSTEM = (
    "You fix speech-to-text errors in dictated developer commands: ordinary words that are "
    "actually misheard technical terms (grab->grep, get->git, pseudo->sudo, "
    "cube control->kubectl), but ONLY in a clearly technical/command context. DEFAULT TO LEAVING "
    "WORDS ALONE. A common word like get/grab/group is the ordinary word unless an explicit command "
    "follows it (get->git ONLY before clone/push/pull/checkout/commit/merge; grab->grep ONLY before "
    "a search target). NEVER change a person's name (e.g. Rado) or a non-English word (e.g. Slovak "
    "'jasně') into a term. When unsure, output NONE. Output ONLY the corrections as 'wrong->right' "
    "pairs separated by commas, or exactly 'NONE'. One pair per occurrence to fix. Never rephrase, "
    "reorder, or change punctuation."
)

# few-shot: small models obey a narrow mandate far better with examples than rules,
# especially the NEGATIVE cases (don't touch ordinary uses). The negatives below are real
# over-corrections caught in dogfood telemetry (2026-06-20): ordinary get/grab, a name, a SK word.
FEWSHOT = [
    ("Open the nginx config and grab the localhost port.", "grab->grep"),
    ("Let's grab a coffee after the standup.", "NONE"),
    ("First get clone the repo then get push to main.", "get->git, get->git"),
    ("Then run pseudo cube control apply to deploy.", "pseudo->sudo, cube control->kubectl"),
    ("Did you get the email about the kernel update?", "NONE"),
    ("He needs to get the vision that we collect the data first.", "NONE"),
    ("Get the SSH working and the Wi-Fi on it working.", "NONE"),
    ("Push the change to Rado so he can review it.", "NONE"),
    ("Reboot for the Docker group to take effect.", "NONE"),
]

_WORD = lambda w: re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)


def _sounds(s):
    """Crude phonetic normalization so homophone-close strings compare equal-ish:
    collapse silent/equivalent sounds (ps->s, c/q->k, ph->f, z->s)."""
    s = _key(s)
    for a, b in (("ps", "s"), ("ck", "k"), ("qu", "k"), ("c", "k"),
                 ("q", "k"), ("ph", "f"), ("z", "s")):
        s = s.replace(a, b)
    return s


def _lev(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# Some real-word homophones only become a term in the right CONTEXT. `grep` SEARCHES TEXT,
# so a swap into grep needs a search-y target nearby (logs, errors, a pattern). Without this
# the 1B over-fires grep onto any "grab" — e.g. "grab the database" -> "grep the database"
# (measured over-correction). git/sudo/kubectl swaps aren't ambiguous this way, so unguarded.
_GREP_CTX = {"log", "logs", "error", "errors", "output", "file", "files", "pattern",
             "string", "stdout", "stderr", "trace", "grep", "match", "search", "regex"}


def _grep_match(out, wrong):
    """Pick WHICH occurrence of `wrong` to turn into grep: the one whose next few words are a
    search target (logs/errors/pattern/...), since grep's object follows it. This both gates
    the swap (none qualifies -> no grep) AND fixes which word is changed when the sentence has
    several — 'grab a coffee after we grab the logs' must grep the second grab, not the first."""
    for m in _WORD(wrong).finditer(out):
        after = {_key(w) for w in out[m.end():].split()[:4]}
        if after & _GREP_CTX:
            return m
    return None


# `get`->`git` was historically UNGUARDED, so the 1B over-fired it onto ordinary "get" — telemetry
# caught "get the vision"->"git the vision", "Get the SSH working"->"Git the SSH working" (measured).
# git is a verb whose SUBCOMMAND follows it (git clone/push/checkout/...), so gate the swap on a git
# subcommand appearing in the next few words, exactly like grep. No subcommand nearby -> not git.
_GIT_CTX = {"clone", "commit", "commits", "push", "pushed", "pull", "checkout", "branch", "branches",
            "merge", "rebase", "stash", "init", "fetch", "remote", "origin", "status", "diff",
            "reset", "revert", "tag", "log", "add", "stage", "staged", "cherry", "blame"}


def _git_match(out, wrong):
    """Pick the occurrence of `wrong` (get/got) to turn into git: the one immediately followed by a
    git subcommand. Gates the swap entirely when no subcommand is nearby (ordinary 'get')."""
    for m in _WORD(wrong).finditer(out):
        after = {_key(w) for w in out[m.end():].split()[:3]}
        if after & _GIT_CTX:
            return m
    return None


# context-gated terms: a common-word homophone only becomes this term when the right cue is nearby
_CTX_GATE = {"grep": _grep_match, "git": _git_match}


def _plausible(wrong, right):
    """A genuine misheard-term swap is PHONETICALLY close (grab->grep, engine x->nginx,
    pseudo->sudo). This rejects the nonsense pairs a small model sometimes emits that
    still pass the term filter because the target IS a term (Redis->ssh, LLM->grep,
    coffee->grep) — phonetically far, so dropped."""
    w, r = _sounds(wrong), _sounds(right)
    if not w or not r:
        return False
    return _lev(w, r) <= max(2, min(len(w), len(r)) // 2 + 1)


class LLMCorrector:
    def __init__(self, terms, model_id=DEFAULT_LLM_MODEL):
        from mlx_lm import load
        self.model_id = model_id
        self.termset = {_key(t) for t in terms}
        self.model, self.tok = load(model_id)

    def _apply_pairs(self, text, raw_out):
        """Apply the model's 'wrong->right' corrections to `text`, but ONLY when `right`
        is a known IT term (so the model can never rephrase into a non-term). Each pair
        replaces ONE whole-word occurrence (left-to-right), preserving the matched word's
        capitalisation — so 'get->git, get->git' fixes the first two technical `get`s while
        an ordinary 'get' the model left out stays. Malformed output => no change."""
        if not raw_out or raw_out.strip().upper().startswith("NONE"):
            return text
        out = text
        for chunk in raw_out.split(","):
            if "->" not in chunk:
                continue
            wrong, right = chunk.split("->", 1)
            wrong, right = wrong.strip().strip('"\'.'), right.strip().strip('"\'.')
            if not wrong or not right or _key(right) not in self.termset:
                continue                       # safety: only land real IT terms
            if not _plausible(wrong, right):
                continue                       # reject phonetically-implausible nonsense swaps
            # context-gated terms (grep, git) only land when their cue is nearby — pick that
            # occurrence or skip. Other terms apply to the first occurrence.
            gate = _CTX_GATE.get(_key(right))
            m = gate(out, wrong) if gate else _WORD(wrong).search(out)
            if not m:
                continue
            rep = (right[:1].upper() + right[1:]) if m.group(0)[:1].isupper() else right
            out = out[:m.start()] + rep + out[m.end():]
        return out

    def _gen(self, text):
        from mlx_lm import generate
        msgs = [{"role": "system", "content": SYSTEM}]
        for u, a in FEWSHOT:
            msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
        msgs.append({"role": "user", "content": text})
        prompt = self.tok.apply_chat_template(msgs, add_generation_prompt=True)
        return generate(self.model, self.tok, prompt=prompt, max_tokens=48,
                        verbose=False).strip()

    def correct(self, text, force=False):
        """Returns (text_out, fired_bool, latency_s)."""
        words = {_key(w) for w in text.split()}
        if not force and not (words & SUSPECTS):
            return text, False, 0.0          # gate: skip LLM entirely
        t0 = time.monotonic()
        raw = self._gen(text)
        dt = time.monotonic() - t0
        return self._apply_pairs(text, raw), True, dt


class LLMWorker:
    """Drop-in for LLMCorrector that owns the MLX model on ONE long-lived thread.

    MLX GPU streams are thread-local, and the dictation consumer thread is recreated
    on every start/stop toggle — so loading or running the model on that ephemeral
    thread crashes with 'no Stream(gpu, N) in current thread' once you toggle again.
    This worker pins ALL MLX work (load + every inference) to a single persistent
    thread for the app's lifetime. `.correct()` is called from any thread and blocks
    for the result, so it slots into LLMStage unchanged."""

    def __init__(self, terms, model_id=DEFAULT_LLM_MODEL):
        self._terms = terms
        self._model_id = model_id
        self._req = queue.Queue()
        self._ready = threading.Event()
        self._err = None
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait()
        if self._err:
            raise self._err

    def _run(self):
        try:
            corr = LLMCorrector(self._terms, self._model_id)   # load on THIS thread
        except Exception as e:
            self._err = e
            self._ready.set()
            return
        self._ready.set()
        while True:
            text, force, box = self._req.get()
            try:
                box["out"] = corr.correct(text, force=force)
            except Exception:
                box["out"] = (text, False, 0.0)                # never break the pipeline
            box["done"].set()

    def correct(self, text, force=False):
        box = {"done": threading.Event()}
        self._req.put((text, force, box))
        box["done"].wait()
        return box["out"]


if __name__ == "__main__":
    from pathlib import Path
    terms = [t.strip() for t in (Path(__file__).parent / "terms.txt").read_text().splitlines() if t.strip()]
    print("loading model...")
    llm = LLMCorrector(terms)
    # novel sentences (NOT the few-shot examples) -> tests real generalization
    tests = [
        "Use grab to find the error in the log file.",     # grep (technical)
        "Can you grab my charger from upstairs?",          # grab stays (ordinary)
        "Then get checkout main and get merge the branch.",# git checkout / git merge
        "Did you get my message about lunch?",             # get stays (ordinary)
    ]
    for t in tests:
        out, fired, dt = llm.correct(t)
        tag = f"LLM {dt*1000:.0f}ms" if fired else "skipped (gate)"
        print(f"\n[{tag}]\n  in : {t}\n  out: {out}")
