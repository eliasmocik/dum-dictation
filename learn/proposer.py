#!/usr/bin/env python3
"""
V2 SEAM — defined, NOT built. The personalization learner.

This is the interface for V2 (automatic personalization). It reads the unified dogfood telemetry
stream (the SAME data V1 already collects) and PROPOSES per-user corrections for the user to approve,
derived chiefly from repeated `correction_pair` signals. Nothing in V1 calls this — in V1 the human
curates manually via the analyzer's "top correction pairs" report. Defining the interface now means
V2 is a *query over history*, not a re-instrumentation.

Pipeline side: approved proposals feed `pipeline.PersonalCorrectionStage` (the inert V1 seam),
gated by HOVOR_PERSONAL_CORRECTIONS.

Governing rule (PRODUCT-VISION.md): General vs Personal. A learner here proposes PERSONAL
corrections for one user; it must NEVER auto-promote a user's idiolect (e.g. "JITHUB" -> "GitHub")
into the shipped GLOBAL packs — that stays a deliberate human decision.

DO NOT IMPLEMENT in V1.
"""

# Proposed output shape (for when V2 is built) — one entry per candidate personal correction:
#   {
#     "spoken":    "jithub",        # what the user reliably says / the recognizer commits
#     "canonical": "GitHub",        # what the user reliably corrects it to
#     "support":   7,               # how many times this committed->corrected pair recurred
#     "scope":     "global|repo|app",   # where it applies (from repo_root / app context)
#     "scope_key": "it-dictation",  # the repo/app it's scoped to, if any
#     "kind":      "personal",      # personal idiolect — NOT a general/global rule
#   }


def propose_personal_corrections(events):
    """INPUT:  the unified dogfood event stream — a list of commit / user.refix / user.verdict dicts
              (as written by dogfood_log.py and read by scripts/analyze_user_corrections.py).
    OUTPUT: a list of proposed personal corrections (see the shape above) for the user to review and
            approve. Derived from recurring correction_pair signals, scoped by app / repo context,
            weighted by support and user.verdict flags.

    Defined as a seam for V2; intentionally not implemented in V1."""
    raise NotImplementedError(
        "V2 personalization learner — defined as a seam, not built in V1. "
        "In V1 the human curates via analyze_user_corrections.py 'top correction pairs'."
    )
