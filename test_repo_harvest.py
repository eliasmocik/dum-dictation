#!/usr/bin/env python3
"""
Unit tests for the Phase-R repo vocabulary harvester (repo_harvest.py).
Run: .venv/bin/python test_repo_harvest.py
"""
import os
import tempfile
from collections import Counter
from repo_harvest import (split_identifier, is_multicomponent, keep_decomposition,
                          harvest_text, build_aliases, ensure_repo_pack)

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"ok  {msg}")


# --- decomposition: every identifier style -> the words you'd speak ---
SPLIT = [
    ("ArgumentParser", ["argument", "parser"]),
    ("argumentParser", ["argument", "parser"]),
    ("build_pipeline", ["build", "pipeline"]),
    ("MAX_RETRIES", ["max", "retries"]),
    ("parseHTTPResponse", ["parse", "http", "response"]),
    ("getURLPath", ["get", "url", "path"]),
    ("HTTPSConnection", ["https", "connection"]),
    ("kubectl", ["kubectl"]),                 # single token -> 1 word
    ("snake_case_thing", ["snake", "case", "thing"]),
]
for ident, want in SPLIT:
    check(split_identifier(ident) == want, f"split {ident!r} -> {want}")

# --- multi-component gate ---
check(is_multicomponent("ArgumentParser"), "ArgumentParser is multi-component")
check(is_multicomponent("build_pipeline"), "build_pipeline is multi-component")
check(not is_multicomponent("kubectl"), "kubectl is single-token (not multi-component)")
check(not is_multicomponent("parser"), "parser is single-token (not multi-component)")

# --- drop rule: all-common short decompositions collide with prose ---
check(not keep_decomposition(["data", "model"]), "drop 'data model' (all common, 2 words)")
check(not keep_decomposition(["user", "name"]), "drop 'user name' (all common, 2 words)")
check(keep_decomposition(["argument", "parser"]), "keep 'argument parser' (parser not common)")
check(keep_decomposition(["build", "pipeline"]), "keep 'build pipeline' (pipeline not common)")
check(keep_decomposition(["get", "user", "session"]), "keep 3-word phrase even if commonish")

# --- build_aliases: frequency filter, identity skip, spoken-form dedup, top_k ---
c = Counter({
    "ArgumentParser": 5,      # kept
    "build_pipeline": 4,      # kept
    "DataModel": 9,           # dropped by all-common rule
    "rareThing": 2,           # dropped: below min_freq=3
    "argumentParser": 7,      # same spoken form as ArgumentParser, MORE frequent -> wins canonical
})
aliases = dict(build_aliases(c, min_freq=3, top_k=500))
check("argument parser" in aliases, "'argument parser' harvested")
check(aliases["argument parser"] == "argumentParser", "more-frequent casing wins the canonical (argumentParser)")
check("build pipeline" in aliases, "'build pipeline' harvested")
check("data model" not in aliases, "'data model' dropped (all-common)")
check("rare thing" not in aliases, "below-min_freq identifier dropped")

# top_k cap
big = Counter({f"thingNumber{i}": 10 + i for i in range(50)})
check(len(build_aliases(big, min_freq=3, top_k=10)) == 10, "top_k caps the alias count")

# harvest_text only counts multi-component tokens
hc = Counter()
harvest_text("def buildPipeline(): return parser  # plain words here", hc)
check(hc.get("buildPipeline") == 1, "harvest_text counts buildPipeline")
check("parser" not in hc and "return" not in hc, "harvest_text skips single-token/plain words")

# --- ensure_repo_pack: HEAD-keyed cache on this repo (integration; needs git) ---
with tempfile.TemporaryDirectory() as cache:
    os.environ["HOVOR_CACHE"] = cache
    try:
        d1 = ensure_repo_pack(".")
        check(d1 is not None and os.path.exists(os.path.join(d1, "repo.aliases")),
              "ensure_repo_pack writes repo.aliases into the cache")
        check(os.path.exists(os.path.join(d1, "HEAD")), "cache HEAD marker written")
        d2 = ensure_repo_pack(".")
        check(d1 == d2, "second call is a cache hit (same dir)")
        # a non-repo path -> None (global pack still applies, never crashes)
        check(ensure_repo_pack(tempfile.gettempdir()) is None, "non-repo cwd -> None")
    finally:
        os.environ.pop("HOVOR_CACHE", None)

print(f"\nALL {passed} CHECKS PASSED")
