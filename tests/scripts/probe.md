# G0 mechanism probe — READ THESE ALOUD

Goal: confirm the **already-hardcoded** phrase aliases fire on your real mic, and that ordinary prose
+ near-miss controls are left untouched. No new code is being tested — these aliases already ship.

**How to read:** speak naturally, **pause ~1s between sentences** (so the recognizer segments them).
Read each line **exactly as written** — including "engine x" and "cube control" (those are the spoken
forms the aliases target; don't say "nginx" or "kubectl" yourself).

## Lines to read

1. engine x is down so restart the server
2. run cube control to get the pods
3. deploy it on local host port three thousand
4. the cube root of nine is three
5. let's grab a coffee after the meeting
6. the quick brown fox jumps over the lazy dog
7. push the branch then open a pull request
8. engine x keeps crashing under load

## What we're checking (you don't read this)

- **Aliases must FIRE:** lines 1/8 → `nginx`; line 2 → `kubectl`; line 3 → `localhost`.
- **Controls must stay UNTOUCHED:** line 4 "cube root" must NOT become kubectl; line 5 "grab" stays
  (no LLM in this probe); lines 6/7 plain prose unchanged.
- If an alias does NOT fire, the raw transcript tells us the **true spoken form** Parakeet emits —
  that's the single most valuable thing this probe can surface.
