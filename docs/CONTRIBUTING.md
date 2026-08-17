# Contributing

Most contributions are vocabulary fixes: teaching the tool a technical term it
mis-transcribes.

## Differentiating between general and personal fixes

- **General** - the recognizer's fault. A word said normally, mis-transcribed by the model
  ("ten stack query" => `TanStack Query`, "postgress" => `PostgreSQL`). Belongs in the
  shipped vocab packs; any user speaking standard English hits the same error.
- **Personal** - your accent or idiolect. The model heard you correctly (you say "JITHUB"
  but mean GitHub). Does NOT belong in the shipped tool; a global "fix" breaks it for
  users who don't talk that way.

The test to apply is this: **would a general user, speaking standard English, produce the
same error?** If yes, the fix is General and belongs in the packs. If no, it is Personal
and should be left out.

Note that anyone testing the tool is dictating into it, and is therefore both a tester and
a speaker with an accent. Two edits can look identical to the machine and still fall on
opposite sides of the rule:

- `Ugres => PostGres` - a recognizer mishear of "postgres" => General, accept.
- `the => this` - you changed your wording, not a mishear => Personal / neither, never add.

Only a careful human read tells them apart. When in doubt, leave it out.

## Adding a General term

1. Add a phrase alias to the relevant file in `packs/` (`spoken form => Canonical Form`,
   see existing entries). Use misheard jargon, never a common English word.
2. Run the gate; it must stay green:
   ```
   scripts/test
   ```
3. Open a PR describing the mishear and why it's General, not Personal.

## Code changes

Run `scripts/test` before opening a PR; correctness (term recall, WER, zero
over-correction) must not regress. See `DEV-NOTES.md` for the local dev loop and
`ARCHITECTURE.md` for how the pipeline fits together.
