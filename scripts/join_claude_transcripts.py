#!/usr/bin/env python3
"""
join_claude_transcripts.py — recover the EXACT post-commit edit signal for dictation that went into
the Claude Code prompt, which no VS Code extension can see.

WHY: in the VS Code integrated terminal, Claude Code runs as a TUI. macOS Accessibility is blind to
it and the document-model extension can't read it either (it's not a TextDocument) — so Hovor's
commits there fall back to the keystroke proxy, which can't tell a correction from normal typing.
But Claude Code persists EXACTLY what the user submitted in its own transcript
(~/.claude/projects/<mangled-cwd>/*.jsonl). So we join post-hoc: for each Hovor dictation commit,
find the human Claude-Code message that contains it (time window + fuzzy match), and edit-distance
the committed text against what was actually submitted. That diff is the real correction signal.

This is LOCAL-ONLY (reads files already on this machine) and writes only metrics + the minimal
changed token span (REDACT_MAX-capped, same redaction policy as dogfood_log) — never whole messages.
It is the Claude-Code analogue of the VS Code extension's vscode-ext refix: same shape, joined by
commit_id, capture_method="claude-transcript". Idempotent: rewrites claude-join-<session>.jsonl each
run (derived data), so re-running never duplicates events.

Usage:
    python scripts/join_claude_transcripts.py [dogfood/sessions/*.jsonl]
    (default commit glob: dogfood/sessions/dictation-*.jsonl; transcripts: ~/.claude/projects/*/*.jsonl)
"""
import sys, os, json, glob, collections
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dogfood_log as dl
dl.KEEP_CORRECTIONS = True          # joiner always captures the committed->submitted changed span

# Surfaces where a Claude Code TUI can live and the live bucket couldn't see the prompt: the current
# coarse "vscode", the legacy pre-v5 VS Code bucket "editor", and standalone terminals ("shell", and
# its pre-v5 name "terminal") running `claude`. The match itself is self-gating — only text that
# actually appears as a submitted human Claude message in the time window joins — so this filter is a
# scope guard, not the safety mechanism. Surfaces that never host Claude Code (browser, rich-text)
# are excluded so a coincidental same-window message can't pull them in.
JOIN_SURFACES = {"vscode", "editor", "shell", "terminal"}
# A Hovor commit is the moment text was inserted into the prompt; the user may keep dictating more
# parts before pressing Enter, so the submitted message can land well after the commit. Allow a small
# negative skew for clock jitter.
WINDOW_AFTER_S = 900.0      # 15 min: commit -> eventual submit
SKEW_BEFORE_S = 15.0        # tolerate the message timestamp landing slightly before the commit ts
# Minimum fuzzy partial-ratio (committed text vs best-aligned region of the message) to accept a
# match. Dictated text is distinctive; this keeps a coincidental same-window message from joining.
MATCH_THRESHOLD = 80.0


def _iso_to_epoch(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def load_commits(paths):
    """All commit records across the given dogfood logs, keyed by session for per-session output."""
    by_session = collections.defaultdict(list)
    for p in paths:
        try:
            for line in open(p):
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if e.get("type") == "commit" and e.get("ts") is not None:
                    by_session[e.get("session")].append(e)
        except (OSError, json.JSONDecodeError):
            continue
    return by_session


def load_transcript_messages(root=None):
    """Every human-authored Claude Code message across all local projects, as (epoch, text).
    Skips tool-results (content is a list), system-reminder/command wrappers (start with '<'),
    meta lines, and anything not flagged as a human prompt. Sorted by time for windowed lookup."""
    root = root or (Path.home() / ".claude" / "projects")
    msgs = []
    for p in glob.glob(str(root / "*" / "*.jsonl")):
        try:
            for line in open(p):
                line = line.strip()
                if not line or '"type":"user"' not in line:
                    continue
                d = json.loads(line)
                if d.get("type") != "user" or d.get("isMeta"):
                    continue
                if (d.get("origin") or {}).get("kind") != "human":
                    continue
                c = (d.get("message") or {}).get("content")
                if not isinstance(c, str):           # tool_result content is a list
                    continue
                txt = c.strip()
                if not txt or txt.startswith("<"):   # system-reminder / slash-command wrapper
                    continue
                ep = _iso_to_epoch(d.get("timestamp"))
                if ep is not None:
                    msgs.append((ep, txt))
        except (OSError, json.JSONDecodeError):
            continue
    msgs.sort(key=lambda m: m[0])
    return msgs


def best_match(committed, commit_ts, msgs):
    """The human Claude message in the time window whose text best contains `committed`.
    Returns (epoch, text, score) or None. Score is rapidfuzz partial_ratio (0-100)."""
    from rapidfuzz import fuzz
    lo, hi = commit_ts - SKEW_BEFORE_S, commit_ts + WINDOW_AFTER_S
    best = None
    for ep, txt in msgs:
        if ep < lo:
            continue
        if ep > hi:
            break                                    # msgs sorted by time -> nothing later can match
        score = fuzz.partial_ratio(committed, txt)
        if best is None or score > best[2]:
            best = (ep, txt, score)
    return best if (best and best[2] >= MATCH_THRESHOLD) else None


def join_session(commits, msgs):
    """Build claude-transcript refix events for the matchable commits in one session."""
    events = []
    for c in commits:
        if c.get("surface") not in JOIN_SURFACES:
            continue
        committed = c.get("fixed", "")
        if not committed.strip():
            continue
        m = best_match(committed, c["ts"], msgs)
        if not m:
            continue
        ep, submitted, score = m
        sig = dl.edit_signal(committed, submitted)   # exact: aligns committed within the message,
        if sig.get("edit_capture") != "ok":          # edit-distances only that region, keeps the span
            continue
        evt = {
            "type": "user.refix", "commit_id": c["commit_id"], "commit_app": c.get("app"),
            "edit_capture": "ok", "capture_method": "claude-transcript",
            "surface_refined": "claude-code", "match_score": round(score, 1),
            "accepted_unchanged": sig["accepted_unchanged"], "edit_distance": sig["edit_distance"],
            "normalized": sig["normalized"], "ts": ep,
        }
        if sig.get("correction_pair"):
            evt["correction_pair"] = sig["correction_pair"]
        events.append(evt)
    return events


def main(argv):
    globs = argv or ["dogfood/sessions/dictation-*.jsonl"]
    paths = []
    for a in globs:
        paths.extend(glob.glob(a))
    if not paths:
        print("no dogfood commit logs found (looked for dictation-*.jsonl).")
        return 0
    by_session = load_commits(paths)
    msgs = load_transcript_messages()
    if not msgs:
        print("no Claude Code transcripts found under ~/.claude/projects/ — nothing to join.")
        return 0

    out_dir = Path(paths[0]).parent
    total_written = total_matched = 0
    for session, commits in by_session.items():
        if session is None:
            continue
        events = join_session(commits, msgs)
        out = out_dir / f"claude-join-{session}.jsonl"
        if not events:
            # remove a stale join file so re-runs don't leave orphaned matches behind
            try:
                out.unlink()
            except FileNotFoundError:
                pass
            continue
        with open(out, "w") as f:                    # OVERWRITE: derived, idempotent
            for e in events:
                f.write(json.dumps(e) + "\n")
        total_written += 1
        total_matched += len(events)
        corrected = sum(1 for e in events if e["edit_distance"] > 0)
        print(f"  {session}: {len(events)} claude-code commit(s) joined, {corrected} corrected -> {out.name}")

    vscode_commits = sum(1 for cs in by_session.values() for c in cs if c.get("surface") in JOIN_SURFACES)
    print(f"\njoined {total_matched}/{vscode_commits} VS Code commit(s) to Claude Code messages "
          f"across {total_written} session file(s). (local-only; transcript join, not terminal reading)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
