#!/usr/bin/env python3
"""
Regression tests for pipeline text cleanups + the deterministic phonetic corrector.
Run: .venv/bin/python test_pipeline.py
"""
import os, tempfile
from pipeline import clean_punct, capitalize_sentences, strip_fillers, drop_fillers
from correct_phonetic import PhoneticCorrector, PHRASE_ALIASES
from vocab import load_phrase_aliases

CASES = [
    # spurious micro-pause punctuation (lowercase follows) -> dropped
    ("See? this is a good example.", "See this is a good example."),
    ("open the config. and grab the port", "open the config and grab the port"),
    ("restart the redis cache! tail the logs", "restart the redis cache tail the logs"),
    ("Wait... what now", "Wait what now"),
    # real sentence breaks (capital follows) -> kept
    ("First clone the repo. Then run npm.", "First clone the repo. Then run npm."),
    # tokens that aren't sentence breaks -> safe
    ("the value is 3.5 exactly", "the value is 3.5 exactly"),
    # trailing punctuation (nothing follows) -> kept
    ("commit the change.", "commit the change."),
]

passed = 0
for src, want in CASES:
    got = clean_punct(src)
    assert got == want, f"FAIL clean_punct\n  in  : {src!r}\n  got : {got!r}\n  want: {want!r}"
    passed += 1
    print(f"ok  {src!r} -> {got!r}")

# --- sentence-initial capitalization (fixes alias-lowercased sentence starts) ---
CAP_CASES = [
    # the real bug: alias replaced a sentence-initial word with a lowercase canonical
    ("nginx is down, so restart the server.", "Nginx is down, so restart the server."),
    ("push the branch. nginx keeps crashing.", "Push the branch. Nginx keeps crashing."),
    # mid-sentence canonicals stay lowercase; already-capital first word untouched
    ("Run the kubectl to get the pods.", "Run the kubectl to get the pods."),
    ("use webpack here.", "Use webpack here."),
    # capitalize after ? and !
    ("what now? okay then.", "What now? Okay then."),
    # leading digit / decimals not mis-capitalized; no spurious changes
    ("3.5 is the value", "3.5 is the value"),
    ("Hello world.", "Hello world."),
]
for src, want in CAP_CASES:
    got = capitalize_sentences(src)
    assert got == want, f"FAIL capitalize\n  in  : {src!r}\n  got : {got!r}\n  want: {want!r}"
    passed += 1
    print(f"ok  {src!r} -> {got!r}")

# --- strip_fillers: remove standalone disfluencies from settled (commit) text ---
FILLER_CASES = [
    # (strip_fillers also runs capitalize_sentences, so the sentence start ends up capitalized -
    #  in the real flow `fixed` is already capitalized by SentenceCapStage, so that's a no-op there;
    #  here it just means lowercase-initial test inputs come back capitalized.)
    ("I think um the plan works", "I think the plan works"),          # mid-sentence
    ("Uh let's grep the logs", "Let's grep the logs"),                # sentence-initial -> recapitalize
    ("ship it. Uh, actually wait", "Ship it. Actually wait"),         # filler + orphaned comma + recap after '.'
    ("I think, uh, the plan", "I think, the plan"),                   # filler between commas (keep the first)
    ("restart it right now. Uh", "Restart it right now."),            # trailing filler + stranded period
    ("um er the server is hmm down", "The server is down"),           # multiple fillers, incl. recap of new lead
    # whole-token only - real words that START like a filler must survive
    ("grab an umbrella before the storm", "Grab an umbrella before the storm"),
    ("go ahead and run the error handler", "Go ahead and run the error handler"),
    ("the umma model is under review", "The umma model is under review"),
    # nasal-grunt variants Parakeet actually emits ("Mm"/"Hm", not just "mmm") - must strip
    ("Mm okay let's go", "Okay let's go"),
    ("no hm fill words here", "No fill words here"),
    ("the plan is mm solid", "The plan is solid"),
    # must NOT strip: "m" alone (real flag/var; would break contractions), "oh", real words w/ substrings
    ("I'm going to deploy now", "I'm going to deploy now"),     # "m" in I'm untouched
    ("set the m flag on the command", "Set the m flag on the command"),  # standalone "m" kept
    ("measure it in ohm not amps", "Measure it in ohm not amps"),        # "ohm" (hm substring) kept
    ("oh wow it actually worked", "Oh wow it actually worked"),          # "oh" kept (real interjection)
    # no fillers -> unchanged (bar the leading cap)
    ("Deploy the server and check the logs", "Deploy the server and check the logs"),
    # idempotent
    ("Let's grep the logs", "Let's grep the logs"),
]
for src, want in FILLER_CASES:
    got = strip_fillers(src)
    assert got == want, f"FAIL strip_fillers\n  in  : {src!r}\n  got : {got!r}\n  want: {want!r}"
    assert strip_fillers(got) == got, f"strip_fillers not idempotent on {got!r}"
    passed += 1
    print(f"ok  strip {src!r} -> {got!r}")

# --- drop_fillers: live-preview word-list filtering (+ the implicit one-tick gate) ---
DROP_CASES = [
    # (words, at_start, expected)
    (["I", "think", "um", "the", "plan"], False, ["I", "think", "the", "plan"]),  # confirmed filler dropped
    (["the", "um"], False, ["the"]),                       # trailing filler-shaped token held (absent this tick)
    (["the", "umbrella"], False, ["the", "umbrella"]),     # next tick it grew into a real word -> revealed
    (["uh", "the", "plan"], True, ["The", "plan"]),        # leading filler at sentence start -> recap new lead
    (["uh", "the", "plan"], False, ["the", "plan"]),       # same mid-stream (not at start) -> no recap
    (["umbrella"], False, ["umbrella"]),                   # lone real word survives
    (["uh", "um"], True, []),                              # all-filler -> empty (nothing to show)
]
for words, at_start, want in DROP_CASES:
    got = drop_fillers(words, at_start=at_start)
    assert got == want, f"FAIL drop_fillers\n  in  : {words!r} at_start={at_start}\n  got : {got!r}\n  want: {want!r}"
    passed += 1
    print(f"ok  drop {words!r} -> {got!r}")

# --- phonetic corrector: alias hits + no false positives ---
_pc = PhoneticCorrector([t.strip() for t in open("terms.txt") if t.strip()])
PHONETIC_CASES = [
    ("then run sudo cube CTL commit", "then run sudo kubectl commit"),   # cube CTL -> kubectl
    ("run sudo, cube, CTL, commit", "run sudo, kubectl, commit"),        # comma-separated
    ("deploy with cube control apply", "deploy with kubectl apply"),     # cube control -> kubectl
    ("the cube root is nine", "the cube root is nine"),                  # NOT over-corrected
    ("engine x is down", "nginx is down"),                              # existing alias still works
    ("grab a coffee", "grab a coffee"),                                # ordinary word untouched
    ("how how do I proceed", "how do I proceed"),                       # true stutter -> collapse
    ("cache cache miss", "cache miss"),                                # stutter -> collapse
    ("very, very important reason", "very, very important reason"),     # comma = emphasis, NOT stutter -> kept (dedup bug fix)
]
for src, want in PHONETIC_CASES:
    got = _pc.correct(src)
    assert got == want, f"FAIL phonetic\n  in  : {src!r}\n  got : {got!r}\n  want: {want!r}"
    passed += 1
    print(f"ok  {src!r} -> {got!r}")

# --- G1a: external phrase-alias loading (additive) ---
_terms = [t.strip() for t in open("terms.txt") if t.strip()]

# no-pack identity: a corrector with no extras is EXACTLY the shipped base (nothing migrated)
assert PhoneticCorrector(_terms).phrase_aliases == PHRASE_ALIASES, "no-pack identity drifted"
print("ok  no-pack identity: phrase_aliases == PHRASE_ALIASES")

with tempfile.TemporaryDirectory() as _d:
    with open(os.path.join(_d, "test.aliases"), "w") as _f:
        _f.write(
            "# a comment line, skipped\n"
            "ten stack => TanStack\n"
            "\n"                                  # blank, skipped
            "argument parser => ArgumentParser\n"
            "malformed line with no arrow\n"      # skipped (no =>)
            "  => onlyrhs\n"                      # skipped (empty lhs)
            "spoken => \n"                        # skipped (empty rhs)
        )
    _aliases = load_phrase_aliases(_d)
    assert len(_aliases) == 2, f"parser: expected 2 valid aliases, got {len(_aliases)}: {_aliases}"
    print(f"ok  parser: 3 bad lines skipped, {len(_aliases)} aliases loaded")

    _pc2 = PhoneticCorrector(_terms, extra_phrase_aliases=_aliases)
    ALIAS_CASES = [
        ("use ten stack query for the cache", "use TanStack query for the cache"),  # packed alias fires
        ("ten stacks of paper on the desk", "ten stacks of paper on the desk"),     # precision: word-boundary, no false fire
        ("call the argument parser now", "call the ArgumentParser now"),            # second packed alias fires
        ("engine x is down", "nginx is down"),                                      # ADDITIVITY: hardcoded base still fires
        ("grab a coffee", "grab a coffee"),                                         # ordinary prose untouched with a pack loaded
    ]
    for src, want in ALIAS_CASES:
        got = _pc2.correct(src)
        assert got == want, f"FAIL alias\n  in  : {src!r}\n  got : {got!r}\n  want: {want!r}"
        passed += 1
        print(f"ok  {src!r} -> {got!r}")

# empty/absent pack dir -> no aliases (free core never depends on packs)
assert load_phrase_aliases("/nonexistent/dir/xyz") == [], "missing dir should yield no aliases"
print("ok  missing pack dir -> [] (free core safe)")

# --- G2 Layer 1: the shipped global-tech pack - precision (positives + near-miss negatives) ---
_gpack = load_phrase_aliases("packs")
assert _gpack, "packs/global-tech.aliases should load some aliases"
_pcg = PhoneticCorrector(_terms, extra_phrase_aliases=_gpack)
G2_CASES = [
    # positives: spoken form => canonical lands
    ("bundle the assets with web pack", "bundle the assets with webpack"),
    ("run the tests using pie test", "run the tests using pytest"),
    ("store the documents in mongo DB", "store the documents in MongoDB"),
    ("open a web socket for live updates", "open a WebSocket for live updates"),
    ("push the code to git hub", "push the code to GitHub"),
    ("run the model locally with oh llama", "run the model locally with Ollama"),
    ("the engine uses sherpa onnx", "the engine uses sherpa-onnx"),
    ("the streaming model is a zip former", "the streaming model is a Zipformer"),
    ("connect through wire guard", "connect through WireGuard"),
    # group 2b: true spoken forms captured from the payoff recording (§7)
    ("the engine uses sherpa onyx", "the engine uses sherpa-onnx"),
    ("use the nemo DB for sessions", "use the DynamoDB for sessions"),
    ("run it with o lama", "run it with Ollama"),                       # "O Lama" form...
    ("I saw a llama at the zoo", "I saw a llama at the zoo"),           # ...but "a llama" still safe
    # group 5: "dum dictation" brand - phrase alias fires to the canonical (intentionally-misspelled) brand
    ("I use dumb dictation every day", "I use dum dictation every day"),                  # required brand phrase
    ("try dom dictation on your mac", "try dum dictation on your mac"),                   # near-mishear variant
    # over-correction GUARD: a bare "dumb"/"done" in normal prose must NEVER be remapped to "dum"
    ("that's a dumb idea honestly", "that's a dumb idea honestly"),                       # bare "dumb" untouched
    ("I'm done with this task", "I'm done with this task"),                               # bare "done" untouched
    ("dumb luck got me through", "dumb luck got me through"),                             # "dumb" + non-dictation word safe
    # group 6: "ChatGPT" mishears (issue #6) - single-token AND split emissions each need their own line
    ("ask ChadGBT about the error", "ask ChatGPT about the error"),                       # single token
    ("ask chad gbt about the error", "ask ChatGPT about the error"),                      # two-token split
    ("ask chad g b t about the error", "ask ChatGPT about the error"),                    # letter split
    ("paste it into chat gbt", "paste it into ChatGPT"),                                  # near-miss: chat right, GBT wrong
    ("paste it into chat g b t", "paste it into ChatGPT"),                                # near-miss letter split
    ("chad gpt wrote this function", "ChatGPT wrote this function"),                      # near-miss: GPT right, chad wrong
    ("chad g p t wrote this function", "ChatGPT wrote this function"),
    ("open chat GPT in the browser", "open ChatGPT in the browser"),                      # spacing normalization (git hub precedent)
    # over-correction GUARD: bare "chad"/"chat" in normal prose must NEVER be touched
    ("I met Chad at the conference", "I met Chad at the conference"),                     # bare name untouched
    ("open the chat window on the left", "open the chat window on the left"),             # bare "chat" untouched
    # near-miss NEGATIVES: must stay untouched (the over-correction tripwires)
    ("I saw a llama at the zoo last weekend", "I saw a llama at the zoo last weekend"),   # a llama != Ollama
    ("I need to type a script for the video", "I need to type a script for the video"),   # no TypeScript alias
    ("let's grab a coffee and talk", "let's grab a coffee and talk"),                     # plain prose intact
]
for src, want in G2_CASES:
    got = _pcg.correct(src)
    assert got == want, f"FAIL g2-pack\n  in  : {src!r}\n  got : {got!r}\n  want: {want!r}"
    passed += 1
    print(f"ok  {src!r} -> {got!r}")

# --- CorrectionPipeline.run: returns the stage trace (events) with per-stage ms timing ---
from pipeline import CorrectionPipeline, PunctuationStage, SentenceCapStage
# punct drops the micro-pause "? "; sentcap then recapitalizes the leading "see"->"See". Both fire.
_pipe = CorrectionPipeline([PunctuationStage(), SentenceCapStage()])
_out, _evs = _pipe.run("see? this is fine")
assert _out == "See this is fine", f"pipeline run output wrong: {_out!r}"
assert {e["stage"] for e in _evs} == {"punct", "sentcap"}, f"expected both stages to fire: {_evs}"
assert all(e["type"] == "correction.applied" for e in _evs), "events keep type for the bus"
assert all(isinstance(e.get("ms"), float) and e["ms"] >= 0 for e in _evs), f"each event carries ms: {_evs}"
passed += 1
print("ok  CorrectionPipeline.run -> (text, events) with per-stage ms timing")

# a no-op pipeline (text unchanged) emits NO events -> empty trace
_out2, _evs2 = CorrectionPipeline([PunctuationStage()]).run("nothing to fix here")
assert _out2 == "nothing to fix here" and _evs2 == [], f"no-op stage must emit no event: {_evs2}"
passed += 1
print("ok  CorrectionPipeline.run -> no event when a stage changes nothing")

# --- V2 seam: PersonalCorrectionStage is a strict passthrough in V1 (no learner, no data) ---
from pipeline import PersonalCorrectionStage
_ps = PersonalCorrectionStage()                          # no learned corrections (V1)
assert _ps.run("git push to jithub", {}) == ("git push to jithub", []), "personal seam must be inert in V1"
# even with the flag on but no data, still inert (never invents corrections)
os.environ["DUM_PERSONAL_CORRECTIONS"] = "1"
assert PersonalCorrectionStage().run("deploy now", {}) == ("deploy now", []), "no data -> still passthrough"
os.environ.pop("DUM_PERSONAL_CORRECTIONS", None)
passed += 1
print("ok  PersonalCorrectionStage inert in V1 (defined seam, no behaviour)")
# the learn/ proposer is a defined-but-unbuilt seam
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("proposer", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "learn", "proposer.py"))
_prop = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_prop)
try:
    _prop.propose_personal_corrections([])
    raise AssertionError("learn proposer should be unbuilt (NotImplementedError) in V1")
except NotImplementedError:
    passed += 1
    print("ok  learn/proposer.py defined as a seam, raises NotImplementedError in V1")

# --- ProtectedWordsStage: common words/names must not be silently rewritten into jargon ---
# (session theme 2026-06-20; reverts measured over-corrections from dogfood telemetry)
from pipeline import ProtectedWordsStage
_pw = ProtectedWordsStage()
def _guard(raw, corrupted):
    return _pw.run(corrupted, {"_raw_input": raw})[0]
# REVERT: no command/code cue in the sentence
assert _guard("where we can get it.", "where we can git it.") == "where we can get it.", "get->git must revert"
assert _guard("Get the SSH working.", "Git the SSH working.") == "Get the SSH working.", "casing-preserving revert"
assert _guard("the Docker group to take effect", "the Docker grep to take effect") == "the Docker group to take effect", "group->grep must revert"
assert _guard("push it to Rado for review", "push it to redis for review") == "push it to Rado for review", "name Rado must not become redis"
assert _guard("we collected a lot of data", "we collected a_lo of data") == "we collected a lot of data", "phrase 'a lot' must revert"
# ALLOW: clear command context present -> swap stands
assert _guard("get clone the repo then get push", "git clone the repo then git push") == "git clone the repo then git push", "git allowed with clone/push cue"
# UNTOUCHED: genuine jargon fix not in the protect-list
assert _guard("open the engine x config", "open the nginx config") == "open the nginx config", "non-protected fix passes through"
# inert without a stashed raw input (never invents reverts)
assert _pw.run("git push now", {}) == ("git push now", []), "no _raw_input -> passthrough"
passed += 1
print("ok  ProtectedWordsStage reverts word->jargon corruptions unless command context is clear")

# --- decap_interior: lowercase stray boundary capitals on the closed SAFE_LOWER set ---
# (CAP face of the over-eager-boundary bug - within one commit AND across commits. The closed set
#  is the entire name protection. Fixes the capital only; the period face is out of scope.)
from pipeline import (decap_interior, _ends_sentence, _safe_to_lower, SAFE_LOWER,
                      CorrectionPipeline as _CP, PunctuationStage as _PS, SentenceCapStage as _SCS)

# (A) HIGHEST-RISK INTERACTION - decap vs capitalize_sentences, through the FULL ORDERED commit path.
# Mirrors live.py commit ordering: pipeline (... SentenceCapStage) -> strip_fillers -> decap. The
# pipeline + strip_fillers BOTH recapitalize the first word; decap must undo that for a continuation
# and leave it alone for a real sentence start. Ordering is load-bearing - this proves it.
_ORDER = _CP([_PS(), _SCS()])
def _commit_order(raw, after_sentence):
    fixed, _ = _ORDER.run(raw)          # ends with SentenceCapStage (caps word 0)
    fixed = strip_fillers(fixed)        # also recapitalizes word 0
    return decap_interior(fixed, after_sentence=after_sentence)

# continuation (prev segment did NOT end a sentence): pipeline caps "the"->"The", decap lowers it back
_cont = _commit_order("the switch as soon as possible", after_sentence=False)
assert _cont == "the switch as soon as possible", f"continuation should lower word 0: {_cont!r}"
# real sentence start (prev ended .?!): the capital must SURVIVE the full ordered path
_start = _commit_order("the Jetson is set up", after_sentence=True)
assert _start == "The Jetson is set up", f"real start must stay capital: {_start!r}"
# idempotency through the ordering (decap-of-decap, and re-running the whole order, are stable)
assert decap_interior(_cont, after_sentence=False) == _cont, "decap not idempotent (continuation)"
assert decap_interior(_start, after_sentence=True) == _start, "decap not idempotent (real start)"
assert _commit_order(_start, after_sentence=True) == _start, "full order not idempotent (real start)"
passed += 1
print("ok  decap vs capitalize_sentences through the full ordered commit path (continuation/start/idempotent)")

# (B) REAL corpus cases locked as fixtures. (after_sentence, raw, expected)
DECAP_CASES = [
    # within-commit: a capital stranded mid-utterance is lowered (interior, not a start)
    (True,  "what I meant with The window size", "what I meant with the window size"),
    (True,  "I think It's a lot more",           "I think it's a lot more"),   # It's -> it's
    # cross-commit continuation (prev did NOT end a sentence) -> lower the inline first word
    (False, "The switch as soon as possible",    "the switch as soon as possible"),
    (False, "No fill words here",                "no fill words here"),
    (False, "We are going to deploy",            "we are going to deploy"),
    # PROTECTED: a real sentence start (prev ended .?!) keeps its capital
    (True,  "The Jetson is set up.",             "The Jetson is set up."),
    # PROTECTED: proper nouns / vocab anywhere are untouched (names are NOT in the closed set)
    (True,  "Push to GitHub and restart Nginx.", "Push to GitHub and restart Nginx."),
    (True,  "Deploy to Jetson and VS Code.",     "Deploy to Jetson and VS Code."),
    (False, "Open Apple menu then run Redis.",   "Open Apple menu then run Redis."),  # word0 "Open" not in set
    # TOKEN GUARDS: single letter A/I, all-caps acronym, CamelCase - never lowered even mid-utterance
    (True,  "make A switch here",                "make A switch here"),   # lone "A" is a label/grade
    (True,  "and I think so",                    "and I think so"),       # lone "I" pronoun
    (True,  "we ship IT to US today",            "we ship IT to US today"),  # IT/US acronyms
    (True,  "push to GitHub now",                "push to GitHub now"),   # CamelCase identifier
    # known-acceptable border: continuation lowers a leading "For" even if it reads "for for" at the seam
    (False, "For the config we set",             "for the config we set"),
    # 2026-06-22 additions (my/as/is/what/whatever/more/both/oh) - grammatical, never names
    (False, "My laptop is the one",              "my laptop is the one"),
    (False, "As far as I can tell",              "as far as I can tell"),
    (False, "Is that the right path",            "is that the right path"),
    (False, "What I meant was different",        "what I meant was different"),
    (False, "Whatever works for you",            "whatever works for you"),
    (False, "More logs than before",             "more logs than before"),
    (False, "Both of them are down",             "both of them are down"),
    (False, "Oh and one more thing",             "oh and one more thing"),
    # PROTECTED: the same words at a REAL sentence start keep their capital
    (True,  "My laptop is the one.",             "My laptop is the one."),
    (True,  "Is that the right path?",           "Is that the right path?"),
]
for after, raw, want in DECAP_CASES:
    got = decap_interior(raw, after_sentence=after)
    assert got == want, f"FAIL decap (after_sentence={after})\n  in  : {raw!r}\n  got : {got!r}\n  want: {want!r}"
    assert decap_interior(got, after_sentence=after) == got, f"decap not idempotent on {got!r}"
    passed += 1
    print(f"ok  decap [{after}] {raw!r} -> {got!r}")

# (C) token-guard unit checks on _safe_to_lower (the closed-set membership + protections)
assert _safe_to_lower("The") == "the" and _safe_to_lower("It's") == "it's" and _safe_to_lower("Its") == "its"
assert _safe_to_lower("I") is None and _safe_to_lower("A") is None, "single letters protected"
assert _safe_to_lower("I'm") is None and _safe_to_lower("I'd") is None, "I-contractions protected"
assert _safe_to_lower("IT") is None and _safe_to_lower("US") is None, "all-caps acronyms protected"
assert _safe_to_lower("GitHub") is None and _safe_to_lower("WebSocket") is None, "CamelCase protected"
assert _safe_to_lower("Jetson") is None and _safe_to_lower("Apple") is None, "names not in set -> protected"
assert _safe_to_lower("My") == "my" and _safe_to_lower("Whatever") == "whatever", "2026-06-22 additions in set"
assert _safe_to_lower("Oh") == "oh" and _safe_to_lower("Is") == "is", "2026-06-22 additions in set"
assert _safe_to_lower("the") is None, "already-lowercase -> no-op (idempotent)"
assert _safe_to_lower('"The') == '"the', "leading quote preserved while lowering"
passed += 1
print("ok  _safe_to_lower: closed-set membership + I/acronym/CamelCase/name guards")

# (D) _ends_sentence: the cross-commit state signal
assert _ends_sentence("The Jetson is set up.") and _ends_sentence("Is it? ") and _ends_sentence('he said "go."')
assert not _ends_sentence("if we can make") and not _ends_sentence("Okay, so")
passed += 1
print("ok  _ends_sentence detects .?! (incl. trailing quote), False on continuations")

print(f"\nALL {passed} CHECKS PASSED")
