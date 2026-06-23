#!/usr/bin/env python3
"""
InsertionBackend — the ONE narrow seam through which the core puts text on screen.

The platform-neutral core (audio -> VAD -> recognizer -> correction pipeline -> telemetry) drives this
interface and NEVER knows the implementation: IMK (macOS marked text), clipboard paste, the synthetic
keystroke overlay, Windows TSF, Linux IBus/Fcitx, etc. Backends do INSERTION ONLY — no recognition,
correction, vocab, telemetry, personalization, or business logic lives in a backend (this is a hard
architectural rule, esp. for the IMK backend). Goal: Apple-grade insertion on macOS without locking the
product to Apple.

Lifecycle per dictated utterance:
    start_preview(text)              # begin a live, in-progress preview (provisional/"marked" if supported)
    update_preview(text)            # replace the provisional text as it grows / corrects (0+ times)
    commit(text)                    # finalize: replace the provisional with the final corrected text
  or cancel()                       # discard the provisional, insert nothing

Capability + context:
    supports_marked_text()          # True => true in-field provisional preview (IMK/TSF/IBus);
                                    # False => no inline preview (e.g. paste-at-commit fallback)
    target = {"app","surface",...}  # optional per-call hint about where text is going, when known

Concrete backends (built out in Phase 1):
  * IMKBackend          — macOS marked text via the dum IMK over IPC (supports_marked_text=True)
  * OverlayBackend      — the existing synthetic keystroke overlay (VS Code fallback)
  * PasteBackend        — clipboard paste at commit (universal last-resort; no live preview)
"""


class InsertionBackend:
    """Interface. Subclass per OS/mechanism. Every method must be safe to call and never raise into
    the core (guard internally) — insertion must never break dictation."""

    name = "base"

    def supports_marked_text(self):
        """True if this backend renders an in-field provisional (marked/underlined) preview that is
        replaced atomically on commit. False => commit-only insertion (no live in-field preview)."""
        return False

    def start_preview(self, text, target=None):
        """Begin a live preview for a new utterance with the first provisional `text`."""
        raise NotImplementedError

    def update_preview(self, text, target=None):
        """Replace the in-progress provisional text (called as the transcript grows or self-corrects)."""
        raise NotImplementedError

    def commit(self, text, target=None):
        """Finalize: replace the provisional preview with the final corrected `text`."""
        raise NotImplementedError

    def cancel(self, target=None):
        """Discard the in-progress provisional preview; insert nothing."""
        raise NotImplementedError


class IMKBackend(InsertionBackend):
    """macOS marked-text backend — drives the dum IMK input method (spikes/imk-dum) over IPC.
    INSERTION ONLY. Built in spike Checkpoint 2; stub for now so the interface + wiring exist."""

    name = "imk"

    def supports_marked_text(self):
        return True

    def start_preview(self, text, target=None):
        raise NotImplementedError("IMK backend: wired in spike Checkpoint 2 (IPC -> dum IMK)")

    def update_preview(self, text, target=None):
        raise NotImplementedError("IMK backend: wired in spike Checkpoint 2 (IPC -> dum IMK)")

    def commit(self, text, target=None):
        raise NotImplementedError("IMK backend: wired in spike Checkpoint 2 (IPC -> dum IMK)")

    def cancel(self, target=None):
        raise NotImplementedError("IMK backend: wired in spike Checkpoint 2 (IPC -> dum IMK)")
