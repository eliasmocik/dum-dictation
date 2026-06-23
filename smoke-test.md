# Quick smoke test — read these two sentences aloud

Run `./dum` (or `.venv/bin/python live.py --overlay --llm --mic 1`), double-tap
left ⌘, then read the two sentences below **into a scratch doc**. Pause for a breath at
the `[breath]` mark on purpose — that tests the dot fix.

---

**1.** Get clone the GitHub repo `[breath]` and grab the errors in the nginx logs on localhost.

**2.** Then run sudo kubectl, commit and rebase onto main, push to Redis over SSH, and grab a coffee.

---

## What should land at your cursor

> **Git** clone the GitHub repo and **grep** the errors in the nginx logs on localhost.
> Then run sudo kubectl, commit and rebase onto main, push to Redis over SSH, and **grab** a coffee.

## Checklist (what each part proves)

- `Get clone` → **Git** clone — homophone LLM in command context ✓
- `grab the errors` → **grep** the errors — homophone LLM in command context ✓
- `grab a coffee` → stays **grab** — LLM precision (doesn't over-correct ordinary use) ✓
- GitHub, nginx, localhost, sudo, kubectl, Redis, SSH spelled right — ASR + phonetic terms ✓
- the `[breath]` pause produced **no stray period** mid-sentence — punctuation cleanup ✓
- words appeared live as you spoke and settled cleanly on the pause — overlay cadence + reconcile ✓

## Two extra 5-second checks

- **Silence:** stay quiet ~5s / give a small cough → **nothing** types (hallucination filter).
- **Toggle:** double-tap left ⌘ again → stops; double-tap → starts (global hotkey).
