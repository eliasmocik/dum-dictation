# Contributing

Thanks for helping make this dictation tool better. The most common contribution is a
**vocabulary fix** - teaching the tool a technical term it mis-transcribes. Before you add
one, read the rule below. It's the single most important discipline in this project, and a
wrong call degrades the tool for *everyone*.

## The one rule: General vs Personal (the "JITHUB rule")

Every correction you might add falls into one of two buckets, and only **one** of them
belongs in this tool:

| | **General** (the recognizer's fault) | **Personal** (your accent / idiolect) |
|---|---|---|
| What happened | a word said normally, mis-transcribed by the model | your own pronunciation is non-standard; the model heard you correctly |
| Examples | "ten stack query" => `TanStack Query`, "postgress" => `PostgreSQL` | someone says "JITHUB" but means GitHub |
| Belongs in | **the shipped vocab packs** (helps everyone) | **NOT the shipped tool** - it would *harm* users who don't talk that way |
| Why | any user speaking standard English hits the same error | a general user does not make this error; a global "fix" breaks it for them |

### Litmus test for every candidate

> **"Would a general user, speaking standard English, produce this same error?"**
> **Yes** => General => add it to the packs.
> **No, that's just how I talk** => Personal => leave it out.

### The trap

If you dictate to test the tool, you are *both* a tester and a specific person with an accent.
The danger is letting *your* idiolect leak into the shared packs. Two edits can look identical
to the machine but have opposite verdicts:

- `Ugres => PostGres` - a recognizer **mishear** of "postgres" => **General**, accept it.
- `the => this` - you **changed your wording**, not a mishear => **Personal / neither**, never add it.

Only a careful human read tells them apart. **When in doubt, leave it out.**

## How to add a General term

1. Add a phrase alias to the relevant file in `packs/` (look at the existing entries for the
   `spoken form => Canonical Form` format). Use *misheard jargon*, never a common English word.
2. Run the gate - it must stay green:
   ```
   scripts/test
   ```
3. Open a PR describing the mishear you observed and why it's General, not Personal.

## Code changes

Run `scripts/test` before opening a PR; correctness (term recall, WER, zero over-correction)
must not regress. See `DEV-NOTES.md` for the local dev loop and `ARCHITECTURE.md` for how the
pipeline fits together.
