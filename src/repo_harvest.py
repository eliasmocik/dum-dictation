#!/usr/bin/env python3
"""
Phase R - auto repo/path vocabulary harvester.

Parse a git repo's tracked source, harvest DISTINCTIVE multi-component identifiers
(camelCase / snake_case / SCREAMING_SNAKE / PascalCase), decompose each to the words
you'd SPEAK ("ArgumentParser" -> "argument parser"), and emit a phrase-alias pack
(`spoken form => CanonicalIdentifier`) in the exact format vocab.load_phrase_aliases
already loads. The pack then flows through the same correction layers as the global
pack - no new correction code.

Why ONLY multi-component identifiers (the de-risked safe rule): a multi-word spoken
form is high-precision by construction - it can't collide with a single ordinary
spoken word. Single-token identifiers (even distinctive ones like "kubectl") go to
the fuzzy path, which the de-risk proved over-corrects at scale - so the auto-harvester
does NOT emit single-token aliases. Plus a drop rule for all-common-English short
decompositions ("DataModel" -> "data model") that WOULD collide with ordinary speech.

This module is pure + offline; the auto-at-launch wiring + HEAD-keyed caching is a
separate integration step (kept out until the harvest logic is proven on a safety bench).

CLI:  .venv/bin/python repo_harvest.py <repo_root> [out.aliases]
"""
import hashlib
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Source extensions worth scanning for identifiers (skip data/markup/lockfiles).
SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".cs", ".swift", ".kt", ".scala", ".php", ".m", ".mm",
}

# An identifier token: starts with a letter, contains letters/digits/_; we also keep
# camelCase runs. We deliberately do NOT match all-lowercase single words here at the
# distinctiveness stage - those are filtered out by is_multicomponent.
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*[A-Za-z0-9]")

# Common English + generic-code words. Used ONLY by the drop rule: a decomposition whose
# words are ALL common AND is short (< 3 words) is dropped (it would fire on ordinary
# speech, e.g. "data model", "user name", "file path"). A single non-common word in the
# decomposition ("argument PARSER", "build PIPELINE") makes it distinctive enough to keep.
COMMON_WORDS = {
    # articles / prepositions / pronouns / aux
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by",
    "is", "are", "be", "as", "it", "this", "that", "from", "into", "out", "up", "we",
    "i", "you", "he", "she", "they", "my", "our", "your",
    # very common English
    "time", "date", "name", "list", "item", "value", "key", "type", "data", "user",
    "file", "path", "line", "text", "code", "page", "form", "field", "table", "index",
    "count", "size", "number", "string", "char", "word", "char", "info", "detail",
    "group", "level", "state", "status", "result", "error", "message", "event", "id",
    "main", "base", "core", "view", "model", "test", "app", "node", "edge", "point",
    "start", "stop", "end", "next", "last", "first", "new", "old", "open", "close",
    "get", "set", "add", "run", "make", "build", "load", "save", "read", "write",
    "send", "find", "check", "update", "create", "delete", "remove", "init", "config",
    "option", "param", "args", "input", "output", "request", "response", "client",
    "server", "object", "class", "method", "func", "call", "task", "job", "queue",
    "cache", "store", "map", "set", "array", "buffer", "stream", "block", "frame",
}


def split_identifier(ident):
    """Decompose an identifier into the lowercase words you'd SPEAK.

    Handles snake_case, kebab-case, camelCase, PascalCase, SCREAMING_SNAKE, and internal
    acronyms:  ArgumentParser->[argument,parser]  build_pipeline->[build,pipeline]
    parseHTTPResponse->[parse,http,response]  getURLPath->[get,url,path]
    HTTPSConnection->[https,connection]  MAX_RETRIES->[max,retries].
    """
    words = []
    for part in re.split(r"[_\-]+", ident):
        if not part:
            continue
        s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", part)        # camel boundary: aB -> a B
        s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)         # acronym->Word: HTTPResp -> HTTP Resp
        words.extend(w.lower() for w in s.split() if w)
    return words


def is_multicomponent(ident):
    """True if the identifier decomposes into >= 2 spoken words (camel/snake/SCREAMING).
    The de-risked safe rule - only these become phrase-aliases."""
    return len(split_identifier(ident)) >= 2


def keep_decomposition(words):
    """Drop a decomposition that is ALL common-English words AND short (< 3 words) - it
    would collide with ordinary speech ('data model'). Longer or jargon-bearing phrases
    are high-precision and kept."""
    if len(words) >= 3:
        return True
    return not all(w in COMMON_WORDS for w in words)


def harvest_text(text, counter):
    """Accumulate raw identifier occurrence counts from one source blob into `counter`."""
    for m in _IDENT.finditer(text):
        tok = m.group(0)
        if is_multicomponent(tok):
            counter[tok] += 1


def git_tracked_sources(root):
    """Yield (path, text) for every git-tracked source file under `root`."""
    root = Path(root)
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, text=True, check=True,
                             timeout=30.0).stdout
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired):
        return
    for rel in out.split("\0"):
        if not rel or Path(rel).suffix not in SOURCE_EXTS:
            continue
        p = root / rel
        try:
            yield p, p.read_text(errors="ignore")
        except OSError:
            continue


def build_aliases(counter, min_freq=3, top_k=500):
    """Counter[identifier]->freq  =>  list of (spoken_form, canonical) alias pairs.

    Keep identifiers seen >= min_freq, drop all-common short decompositions, rank by
    frequency and cap at top_k (the fuzzy/phrase layer stays precision-safe - the de-risk
    proved dumping thousands over-corrects). When two identifiers share a spoken form
    (argumentParser vs ArgumentParser), the more frequent canonical wins."""
    by_spoken = {}   # spoken -> (canonical, freq)
    for ident, freq in counter.most_common():
        if freq < min_freq:
            break    # most_common is descending, nothing below min_freq remains
        words = split_identifier(ident)
        if not keep_decomposition(words):
            continue
        spoken = " ".join(words)
        if spoken == ident.lower():
            continue    # decomposition == identifier (e.g. all-lowercase) -> nothing to fix
        prev = by_spoken.get(spoken)
        if prev is None or freq > prev[1]:
            by_spoken[spoken] = (ident, freq)
    ranked = sorted(by_spoken.items(), key=lambda kv: -kv[1][1])[:top_k]
    return [(spoken, canon) for spoken, (canon, _f) in ranked]


def harvest_repo(root, min_freq=3, top_k=500):
    """Full pipeline: git-tracked sources -> identifier counts -> alias pairs."""
    counter = Counter()
    for _p, text in git_tracked_sources(root):
        harvest_text(text, counter)
    return build_aliases(counter, min_freq=min_freq, top_k=top_k)


def aliases_to_pack(aliases):
    """Render alias pairs as a *.aliases pack body (vocab.load_phrase_aliases format)."""
    lines = ["# auto-harvested repo vocabulary (repo_harvest.py) - spoken form => CanonicalIdentifier"]
    lines += [f"{spoken} => {canon}" for spoken, canon in aliases]
    return "\n".join(lines) + "\n"


def _git(args, cwd):
    try:
        return subprocess.run(["git", "-C", str(cwd)] + args,
                              capture_output=True, text=True, check=True,
                              timeout=10.0).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired):
        return None


def _cache_root():
    return Path(os.environ.get("DUM_CACHE") or (Path.home() / ".cache" / "dum" / "vocab"))


def ensure_repo_pack(cwd=None, min_freq=3, top_k=500):
    """Auto-at-launch entry (Decision G). Detect the git repo containing cwd, harvest the
    cwd subtree's vocabulary into a HEAD-keyed cache, and return the cache DIR (holding
    repo.aliases, ready for load_phrase_aliases) - or None if cwd isn't in a git repo.

    Harvest is scoped to cwd (not the repo toplevel) so a monorepo subproject doesn't pull
    in sibling projects. Regenerates only when HEAD changes or the cache is missing, so a
    warm launch is a cheap file read (R.2 - never block the hotkey on re-harvest)."""
    cwd = os.path.abspath(cwd or os.getcwd())
    root = _git(["rev-parse", "--show-toplevel"], cwd)
    if not root:
        return None                               # not a git repo -> global pack still applies
    head = _git(["rev-parse", "HEAD"], root) or "nohead"
    cdir = _cache_root() / hashlib.sha1(cwd.encode()).hexdigest()[:16]
    marker, pack = cdir / "HEAD", cdir / "repo.aliases"
    if pack.exists() and marker.exists() and marker.read_text().strip() == head:
        return str(cdir)                          # warm cache hit
    aliases = harvest_repo(cwd, min_freq=min_freq, top_k=top_k)
    cdir.mkdir(parents=True, exist_ok=True)
    pack.write_text(aliases_to_pack(aliases))
    marker.write_text(head + "\n")
    return str(cdir)


def main(argv):
    if not argv:
        print("usage: repo_harvest.py <repo_root> [out.aliases]")
        return 1
    root = argv[0]
    aliases = harvest_repo(root)
    body = aliases_to_pack(aliases)
    if len(argv) > 1:
        Path(argv[1]).write_text(body)
        print(f"wrote {len(aliases)} aliases -> {argv[1]}")
    else:
        print(body)
        print(f"# {len(aliases)} aliases", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
