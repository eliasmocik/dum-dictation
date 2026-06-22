# R payoff script — READ THESE ALOUD (proves the auto-harvested REPO pack lands)

Goal: confirm the repo harvester's aliases actually fire on real mic audio — i.e. when you talk about
*this codebase's* symbols, they come out as the real identifiers. Read each line naturally, **pause
~1s between lines**. Say the **spoken** form (the words), not the identifier — that's the whole point.

Run this WITH the harvested repo pack loaded (commands below the lines).

## Lines that NAME this repo's symbols (should become the identifiers)

1. call build pipeline then run the phonetic corrector
2. the phonetic corrector loads load phrase aliases from the vocab dir
3. find model dir returns the parakeet path
4. the overlay typer applies the min edit script
5. check the age stable count before the stable prefix
6. clean punct runs before the phonetic stage
7. set hovor vocab dir to point at the pack
8. build parakeet then call event bus

## Controls — must stay untouched

9. let's grab a coffee and talk about the weekend
10. the quick brown fox jumps over the lazy dog

## Check (you don't read this)

- Lines 1-8 → the canonical identifiers appear: `build_pipeline`, `phonetic corrector`→`PhoneticCorrector`,
  `load_phrase_aliases`, `find_model_dir`, `OverlayTyper`, `min_edit_script`, `age_stable_count`,
  `stable_prefix`, `clean_punct`, `HOVOR_VOCAB_DIR`, `build_parakeet`, `EventBus`.
- Lines 9-10 → untouched.
- Any miss → the RAW column shows the true spoken form; we refine the harvester or accept the miss.

## Commands (from bakeoff/)

```
# 1. (re)generate the repo pack from this repo's tracked source:
mkdir -p /tmp/repo_pack && .venv/bin/python repo_harvest.py . /tmp/repo_pack/repo.aliases

# 2. record (~40s):
scripts/record.sh recordings/repo-vocab.wav 40

# 3. pack ON (global + repo): read the FIXED column — do the identifiers land?
HOVOR_VOCAB_DIR=/tmp/repo_pack .venv/bin/python probe.py recordings/repo-vocab.wav
```
