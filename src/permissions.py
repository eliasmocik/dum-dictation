#!/usr/bin/env python3
"""macOS permission status + requests for the three grants dum cannot work without.

WHY THIS EXISTS - the bug it fixes
-----------------------------------
The menu items used to only DEEP-LINK to System Settings. That looks right on a machine
that has run dum before, and is useless on a fresh one: macOS lists an app under
Microphone / Accessibility / Input Monitoring only once the app has actually ASKED for
that permission. Never having asked, dum was absent from every pane, so the menu item
opened a list with nothing in it to toggle - a dead end that looked like a broken app.

So the rule here is: if the permission has never been decided, ASK (which both shows the
native prompt and creates the row); only once it HAS been decided is System Settings the
right destination, because a denial can only be reversed there.

Three permissions, three completely different APIs - none of them interchangeable:

  Microphone       AVCaptureDevice authorizationStatus / requestAccess. Loaded from the
                   framework with pyobjc-core directly, so we do NOT take a dependency on
                   pyobjc-framework-AVFoundation for two calls.
  Accessibility    AXIsProcessTrusted (silent) vs AXIsProcessTrustedWithOptions (prompts).
                   There is no "undetermined" here - macOS only tells you trusted or not -
                   so an untrusted app is always offered the prompt.
  Input Monitoring IOHIDCheckAccess / IOHIDRequestAccess, via ctypes: pyobjc exposes no
                   wrapper for these. This is the grant the double-tap hotkey needs, and
                   it had no menu item at all before.

Everything degrades to UNKNOWN off macOS (or if a framework will not load) so the tray
menu still builds and the unit suite still runs on Linux.
"""
import ctypes
import ctypes.util
import subprocess
import sys

# Status values. UNDETERMINED is the one that matters: it is the only state in which
# asking produces a prompt, and the only state in which System Settings is the WRONG place
# to send someone, because the app is not listed there yet.
UNDETERMINED = "undetermined"
GRANTED = "granted"
DENIED = "denied"
UNKNOWN = "unknown"

# What a click should do, given a status. Pure, and unit-tested on its own.
REQUEST = "request"
OPEN_SETTINGS = "open_settings"

# Deep-link anchors for the Privacy & Security panes.
PANES = {
    "microphone": "Privacy_Microphone",
    "accessibility": "Privacy_Accessibility",
    "input_monitoring": "Privacy_ListenEvent",
}

_AV_MEDIA_TYPE_AUDIO = "soun"          # AVMediaTypeAudio, as its raw four-char code

# Why a request failed, per permission. These calls are made from menu callbacks where an
# exception would break the menu, so they are caught - but a silently swallowed failure is
# how the microphone request shipped dead. Kept here so summary() and the bundle selftest
# can surface it instead of the user discovering it.
_last_error = {}
_HID_REQUEST_TYPE_LISTEN_EVENT = 1     # kIOHIDRequestTypeListenEvent


def is_supported():
    """Permission gates are a macOS concept; elsewhere there is nothing to ask for."""
    return sys.platform == "darwin"


def decide(status):
    """Given a permission's status, what should clicking its menu item DO?

    This is the whole fix in one function, kept pure so it can be tested without a Mac:
    an undetermined permission must be REQUESTED (the prompt is what creates the row in
    System Settings), anything else is sent to Settings where a live toggle exists.
    UNKNOWN means we could not read the status - Settings is the safe fallback, since it
    always works and never fires a prompt we cannot account for.
    """
    return REQUEST if status == UNDETERMINED else OPEN_SETTINGS


# --------------------------------------------------------------------------- microphone

def _av_capture_device():
    """AVCaptureDevice, loaded straight out of the system framework via pyobjc-core.

    Also declares the completion-handler block, which is NOT optional. Loading a framework
    this way brings the classes but none of the BridgeSupport metadata, so pyobjc has no
    way to know argument 3 of requestAccessForMediaType:completionHandler: is a block, and
    every call raises "Argument 3 is a block, but no signature available". That failure was
    invisible - the caller caught it and quietly opened System Settings instead - which is
    precisely the dead end this module exists to prevent, reintroduced one layer down.
    """
    import objc
    ns = {}
    objc.loadBundle("AVFoundation", ns,
                    bundle_path="/System/Library/Frameworks/AVFoundation.framework")
    try:
        objc.registerMetaDataForSelector(
            b"AVCaptureDevice", b"requestAccessForMediaType:completionHandler:",
            {"arguments": {3: {"callable": {                 # void (^)(BOOL granted)
                "retval": {"type": b"v"},
                "arguments": {0: {"type": b"^v"}, 1: {"type": b"Z"}}}}}})
    except Exception:
        pass                    # already registered, or a pyobjc that does not need it
    return ns["AVCaptureDevice"]


def _prompt_by_opening_the_mic():
    """Last resort: actually open an input stream.

    Opening the device is what makes macOS show the prompt in the first place - it is what
    happens the moment a user presses Start listening. So if the AVFoundation route is
    unavailable for any reason, do the real thing briefly rather than report success we did
    not achieve. sounddevice is already a hard dependency; this adds nothing to the bundle.
    """
    import sounddevice as sd
    stream = sd.InputStream(channels=1, samplerate=16000, blocksize=256)
    try:
        stream.start()
    finally:
        stream.stop()
        stream.close()
    return True


def microphone_status():
    if not is_supported():
        return UNKNOWN
    try:
        st = _av_capture_device().authorizationStatusForMediaType_(_AV_MEDIA_TYPE_AUDIO)
    except Exception:
        return UNKNOWN
    # 0 notDetermined, 1 restricted, 2 denied, 3 authorized
    return {0: UNDETERMINED, 1: DENIED, 2: DENIED, 3: GRANTED}.get(st, UNKNOWN)


def request_microphone():
    """Fire the native "would like to access the microphone" prompt.

    Fire-and-forget: the completion handler runs on some other queue whenever the user
    answers, and blocking the menu thread on that would freeze the menu bar. What matters
    is that the ask happens at all - it is what puts dum in the Microphone pane.

    Two routes, because the first one silently failing is exactly how this shipped broken
    once already. If AVFoundation will not cooperate, open the microphone for real - the
    prompt is a side effect of access, so doing the access cannot fail to produce it.
    """
    if not is_supported():
        return False
    for route in (lambda: _av_capture_device().requestAccessForMediaType_completionHandler_(
                      _AV_MEDIA_TYPE_AUDIO, lambda _granted: None),
                  _prompt_by_opening_the_mic):
        try:
            route()
            return True
        except Exception as e:
            _last_error["microphone"] = f"{type(e).__name__}: {e}"
    return False


# ------------------------------------------------------------------------ accessibility

def accessibility_status():
    """Trusted or not - macOS exposes no third state here.

    Reported as UNDETERMINED rather than DENIED when untrusted, deliberately: that is what
    routes an untrusted app to the PROMPT, which is the only call that adds it to the
    Accessibility list. Sending it to Settings instead is the dead end this module exists
    to kill.
    """
    if not is_supported():
        return UNKNOWN
    try:
        from ApplicationServices import AXIsProcessTrusted
        return GRANTED if AXIsProcessTrusted() else UNDETERMINED
    except Exception:
        return UNKNOWN


def request_accessibility():
    """Prompt for Accessibility. Unlike AXIsProcessTrusted this DOES show UI, and it
    registers the app so the Accessibility list has a row to toggle afterwards."""
    if not is_supported():
        return False
    try:
        from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                         kAXTrustedCheckOptionPrompt)
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------- input monitoring

def _iokit():
    lib = ctypes.CDLL(ctypes.util.find_library("IOKit"))
    lib.IOHIDCheckAccess.restype = ctypes.c_int
    lib.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
    lib.IOHIDRequestAccess.restype = ctypes.c_bool
    lib.IOHIDRequestAccess.argtypes = [ctypes.c_uint32]
    return lib


def input_monitoring_status():
    """The grant the global hotkey needs. pynput's CGEventTap is silently born dead
    without it, which is why a missing Input Monitoring reads as "the hotkey is broken"."""
    if not is_supported():
        return UNKNOWN
    try:
        rc = _iokit().IOHIDCheckAccess(_HID_REQUEST_TYPE_LISTEN_EVENT)
    except Exception:
        return UNKNOWN
    # 0 kIOHIDAccessTypeGranted, 1 kIOHIDAccessTypeDenied, 2 kIOHIDAccessTypeUnknown
    return {0: GRANTED, 1: DENIED, 2: UNDETERMINED}.get(rc, UNKNOWN)


def request_input_monitoring():
    if not is_supported():
        return False
    try:
        _iokit().IOHIDRequestAccess(_HID_REQUEST_TYPE_LISTEN_EVENT)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------------------ routing

_HANDLERS = {
    "microphone": (microphone_status, request_microphone),
    "accessibility": (accessibility_status, request_accessibility),
    "input_monitoring": (input_monitoring_status, request_input_monitoring),
}


def open_pane(kind, runner=subprocess.run):
    """Deep-link to one Privacy & Security pane. Two URL forms because the scheme was
    renamed: the first works on modern System Settings, the second on older ones."""
    anchor = PANES[kind]
    for url in (f"x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?{anchor}",
                f"x-apple.systempreferences:com.apple.preference.security?{anchor}"):
        try:
            runner(["open", url], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    return False


def ensure(kind, opener=None):
    """Ask for `kind` if it has never been decided, otherwise open System Settings.

    Returns the action taken ("request" / "open_settings"), which is what the tests assert
    on - the point of the fix is WHICH of the two happens, not whether a pane rendered.
    """
    status_fn, request_fn = _HANDLERS[kind]
    action = decide(status_fn())
    if action == REQUEST:
        request_fn()
    else:
        (opener or open_pane)(kind)
    return action


def summary():
    """All three at a glance - used by the selftest, and handy when a user reports
    "it types nothing" and the answer is one missing grant."""
    return {kind: fn() for kind, (fn, _) in _HANDLERS.items()}


def last_errors():
    """Any request failures recorded so far. Empty is the healthy state."""
    return dict(_last_error)
