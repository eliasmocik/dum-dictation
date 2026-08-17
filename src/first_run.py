#!/usr/bin/env python3
"""
First-run setup for the bundled .app: fetch the models, with something on screen while it happens.

Split deliberately in two:

  FirstRunPlan   pure logic - what needs downloading, what to ask, how to phrase progress.
                 No AppKit, so it is unit-tested on any machine including CI.
  _Window        a thin AppKit progress window. Imported lazily and only when actually shown.

WHY A WINDOW AND NOT A MENU-BAR PERCENTAGE
Every comparable local-AI Mac app (Ollama, LM Studio, MacWhisper, Superwhisper, VoiceInk, Handy)
uses the menu bar for STATE and opens a real window for a GB-scale download. A 487 MB fetch
behind a 22px icon reads as a hang.

WHY THE ACTIVATION POLICY FLIPS
The app ships LSUIElement=true, so it has no Dock icon and its windows cannot come forward. That
is right for the steady state and wrong for a modal first run, so we switch to .regular while the
window is up and back to .accessory afterwards - the pattern Ollama uses. LSUIElement is a
starting position, not a constraint.
"""
import threading

# Progress phases, in the order a user sees them. Two of the three are NOT downloads, which is
# the whole reason they are named: a determinate bar parked at 100% while the app quietly
# verifies and extracts 671 MB is where "it froze" bug reports come from.
PHASE_LABELS = {
    "downloading":     "Downloading speech model",
    "verifying":       "Verifying download",
    "extracting":      "Extracting speech model",
    "downloading-llm": "Downloading correction model (optional)",
    "done":            "Ready",
    "done-llm":        "Ready",
}


def human(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0


def describe(phase, current, total):
    """One line of user-facing status. Determinate only for the phase that really is."""
    label = PHASE_LABELS.get(phase, phase)
    if phase in ("downloading", "downloading-llm") and total:
        return f"{label}… {human(current)} of {human(total)}", (current / total)
    if phase in ("done", "done-llm"):
        return label, 1.0
    return f"{label}…", None            # None = indeterminate, i.e. show a spinner


class FirstRunPlan:
    """What first run has to do, decided without touching the network or the screen."""

    def __init__(self, models_dir, llm_wanted=True, asr_installed=False):
        self.models_dir = models_dir
        self.llm_wanted = llm_wanted
        self.asr_installed = asr_installed

    @property
    def needs_asr(self):
        return not self.asr_installed

    @property
    def needs_window(self):
        # Only the REQUIRED model justifies blocking the user with a window. The LLM is
        # optional and downloads quietly afterwards.
        return self.needs_asr

    @property
    def steps(self):
        s = []
        if self.needs_asr:
            s.append("asr")
        if self.llm_wanted:
            s.append("llm")
        return s


def ask_llm_consent(default=True):
    """Ask once, on first launch, whether to also fetch the optional correction model.

    Defaults to YES: it is the layer that fixes git/get and grep/grab, which is most of why
    dum exists. But it is 808 MB, so we say so rather than spending a user's bandwidth silently.
    Falls back to the default on any failure - a broken dialog must not block startup.
    """
    try:
        from AppKit import NSAlert, NSApplication, NSAlertFirstButtonReturn
        NSApplication.sharedApplication()
        a = NSAlert.alloc().init()
        a.setMessageText_("Enable smarter corrections?")
        a.setInformativeText_(
            "dum can also fix words that sound identical - \"git\" vs \"get\", "
            "\"grep\" vs \"grab\" - using a small model that runs entirely on this Mac.\n\n"
            "This downloads about 800 MB in the background. Dictation works right away either "
            "way, and you can change this later.")
        a.addButtonWithTitle_("Download (recommended)")
        a.addButtonWithTitle_("Skip for now")
        return a.runModal() == NSAlertFirstButtonReturn
    except Exception:
        return default


class _Window:
    """Minimal AppKit progress window. Created on the main thread, updated from any thread."""

    def __init__(self, title="Setting up dum"):
        from AppKit import (NSWindow, NSTextField, NSProgressIndicator, NSApplication,
                            NSWindowStyleMaskTitled, NSBackingStoreBuffered, NSMakeRect,
                            NSApplicationActivationPolicyRegular)
        self._app = NSApplication.sharedApplication()
        # Come forward for the duration of setup; LSUIElement apps otherwise cannot.
        self._app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

        self.win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 460, 130), NSWindowStyleMaskTitled, NSBackingStoreBuffered, False)
        self.win.setTitle_(title)
        self.win.center()
        content = self.win.contentView()

        self.label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 78, 420, 20))
        for setter, val in (("setBezeled_", False), ("setDrawsBackground_", False),
                            ("setEditable_", False), ("setSelectable_", False)):
            getattr(self.label, setter)(val)
        self.label.setStringValue_("Preparing…")
        content.addSubview_(self.label)

        self.sub = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 24, 420, 18))
        for setter, val in (("setBezeled_", False), ("setDrawsBackground_", False),
                            ("setEditable_", False), ("setSelectable_", False)):
            getattr(self.sub, setter)(val)
        self.sub.setStringValue_("")
        content.addSubview_(self.sub)

        self.bar = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(20, 50, 420, 20))
        self.bar.setIndeterminate_(True)
        self.bar.setUsesThreadedAnimation_(True)
        self.bar.startAnimation_(None)
        content.addSubview_(self.bar)

        self.win.makeKeyAndOrderFront_(None)
        self._app.activateIgnoringOtherApps_(True)

    def update(self, text, fraction):
        from AppKit import NSApp
        def _apply():
            self.label.setStringValue_(text)
            if fraction is None:
                self.bar.setIndeterminate_(True)
                self.bar.startAnimation_(None)
            else:
                self.bar.setIndeterminate_(False)
                self.bar.setDoubleValue_(max(0.0, min(1.0, fraction)) * 100.0)
        _apply()
        # Pump the run loop so the window actually redraws while we block on the download.
        try:
            from Foundation import NSDate, NSDefaultRunLoopMode
            NSApp().nextEventMatchingMask_untilDate_inMode_dequeue_(
                (1 << 32) - 1, NSDate.dateWithTimeIntervalSinceNow_(0.0),
                NSDefaultRunLoopMode, True)
        except Exception:
            pass

    def note(self, text):
        self.sub.setStringValue_(text)

    def close(self):
        from AppKit import NSApplicationActivationPolicyAccessory
        try:
            self.win.close()
        finally:
            # Back to menu-bar-only. Leaving it .regular would put a Dock icon on an app that
            # deliberately doesn't have one.
            self._app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def run_first_run(models_dir, log=print, ask=None, window_factory=None):
    """Ensure the ASR model exists, showing progress. Returns True if dictation can start.

    The LLM is NOT waited on: it is optional, so it is fetched on a daemon thread afterwards
    and the user starts dictating immediately.
    """
    import model_download as md

    installed = md.is_installed(models_dir)
    plan = FirstRunPlan(models_dir, asr_installed=installed)

    llm_wanted = True
    if not installed:                       # only ask on a genuine first run
        llm_wanted = (ask or ask_llm_consent)()
    plan.llm_wanted = llm_wanted

    win = None
    if plan.needs_window:
        try:
            win = (window_factory or _Window)()
            win.note("dum is getting ready. This happens once.")
        except Exception as e:              # never let a GUI failure block setup
            log(f"[first-run] no progress window ({e}); continuing headless")

    def report(phase, cur, total):
        text, frac = describe(phase, cur, total)
        log(f"[first-run] {text}")
        if win:
            try:
                win.update(text, frac)
            except Exception:
                pass

    try:
        if plan.needs_asr:
            md.ensure_parakeet(models_dir, progress=report)
    except md.DownloadError as e:
        log(f"[first-run] FAILED: {e}")
        if win:
            win.close()
        _alert_failure(str(e))
        return False
    finally:
        if win:
            try:
                win.close()
            except Exception:
                pass

    if llm_wanted:
        # Background, daemon: the user is already dictating. A failure here is logged and
        # ignored, exactly as live._build_llm() treats a missing model.
        def _bg():
            try:
                md.ensure_llm(progress=lambda p, c, t: log(f"[first-run] {describe(p, c, t)[0]}"))
                log("[first-run] correction model ready")
            except Exception as e:
                log(f"[first-run] correction model unavailable ({e}); "
                    "dictation continues without it")
        threading.Thread(target=_bg, name="dum-llm-fetch", daemon=True).start()

    return True


def _alert_failure(msg):
    try:
        from AppKit import NSAlert, NSApplication
        NSApplication.sharedApplication()
        a = NSAlert.alloc().init()
        a.setMessageText_("dum couldn't finish setting up")
        a.setInformativeText_(f"{msg}\n\nCheck your internet connection and open dum again.")
        a.addButtonWithTitle_("OK")
        a.runModal()
    except Exception:
        pass
