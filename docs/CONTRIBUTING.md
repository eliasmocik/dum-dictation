# Contributing

Most contributions are vocabulary fixes, which involve teaching the tool a new
technical term.

## Differentiating between general and personal fixes

- **General** - the recognizer's fault. You said the word normally and the model got it
  wrong: "ten stack query" for TanStack Query, "postgress" for PostgreSQL. Anyone speaking
  standard English hits the same error, so the fix belongs in the shipped packs.
- **Personal** - your accent. The model heard you correctly, you just say the word your own
  way. Adding it would break the tool for everyone who does not.

The test to apply is this: **would a general user, speaking standard English, produce the
same error?** If yes, the fix is General and belongs in the packs. If no, it is Personal
and should be left out.

When in doubt, leave it out.

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
