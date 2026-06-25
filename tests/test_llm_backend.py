#!/usr/bin/env python3
"""
LLMBackend seam tests — prove the inference seam is correct WITHOUT loading any model.

The point of the seam: model load + token generation are the only platform-specific atoms;
all the gating/validation lives once in LLMCorrector. So we inject a FakeBackend (canned raw
output) and verify the corrector still applies + validates pairs exactly as before. This is
the path that must behave identically on Mac/Win/Linux once a portable backend lands.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_backend import LLMBackend, MlxBackend, make_backend
from llm_stage import LLMCorrector

TERMS = ["git", "grep", "kubectl", "sudo", "nginx", "PostgreSQL"]
fail = 0


def check(name, cond):
    global fail
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fail = 1


class FakeBackend(LLMBackend):
    """Returns canned raw 'wrong->right' output; records what it was asked to generate."""
    def __init__(self, reply):
        self.reply = reply
        self.seen = None

    def generate(self, messages, max_tokens):
        self.seen = (messages, max_tokens)
        return self.reply


# 1) the abstract base is genuinely abstract
try:
    LLMBackend().generate([], 8)
    check("LLMBackend.generate is abstract", False)
except NotImplementedError:
    check("LLMBackend.generate is abstract", True)

# 2) factory rejects an unknown backend name (no silent fallback)
try:
    make_backend("model-x", name="bogus")
    check("make_backend rejects unknown name", False)
except ValueError:
    check("make_backend rejects unknown name", True)

# 3) factory default selects mlx (name only — don't construct: would load the model)
check("make_backend default name is mlx",
      (os.environ.get("DUM_LLM_BACKEND") or "mlx").lower() == "mlx")

# 4) injected backend drives correct(): a valid context-gated pair lands
fb = FakeBackend("get->git")
c = LLMCorrector(TERMS, backend=fb)
out, fired, dt = c.correct("first get clone the repo then push", force=True)
check("seam applies validated pair (get->git in git context)", out == "first git clone the repo then push")
check("correct() reports fired through the seam", fired is True)

# 5) the corrector — not the backend — still enforces the IT-term safety filter
fb2 = FakeBackend("coffee->latte")           # 'latte' is not an IT term => must be dropped
c2 = LLMCorrector(TERMS, backend=fb2)
out2, _, _ = c2.correct("grab a coffee at noon", force=True)
check("non-term swap dropped (safety preserved behind seam)", out2 == "grab a coffee at noon")

# 6) context gate still fires through the seam: grep needs a search-y target nearby
fb3 = FakeBackend("grab->grep")
c3 = LLMCorrector(TERMS, backend=fb3)
ungated, _, _ = c3.correct("grab a coffee with me", force=True)     # no search target
gated, _, _ = c3.correct("grab the errors in the log", force=True)  # 'errors'/'log' => grep
check("grep context gate holds through seam (ordinary 'grab' untouched)", ungated == "grab a coffee with me")
check("grep context gate holds through seam (technical 'grab'->grep)", gated == "grep the errors in the log")

# 7) the prompt the corrector hands the backend is well-formed (system first, input last, max_tokens fwd)
msgs, mt = fb.seen
check("backend receives system turn first", msgs[0]["role"] == "system")
check("backend receives the input text as the final user turn",
      msgs[-1]["role"] == "user" and msgs[-1]["content"] == "first get clone the repo then push")
check("max_tokens forwarded to backend", mt == 48)

# 8) MlxBackend exists and conforms to the interface (don't instantiate — needs Apple Silicon + model)
check("MlxBackend is an LLMBackend", issubclass(MlxBackend, LLMBackend))

print("\n" + ("ALL CHECKS PASSED" if not fail else "SOME CHECKS FAILED"))
sys.exit(fail)
