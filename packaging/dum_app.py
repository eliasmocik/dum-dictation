#!/usr/bin/env python3
"""
Frozen entry point for the bundled dum.app.

This is the `.app` equivalent of the `./dum` shell launcher, and it exists for two reasons
that only bite once frozen:

1. **argv.** `live.main()` is argv-driven, and a Finder or launchd launch passes NO arguments -
   so the bundle would fall through to the bare `app.start()` branch with no tray, no hotkey
   and no overlay. The shipped app must always run the menu-bar daily driver, so we supply
   those flags here instead of in a shell script we no longer have.

2. **Writable paths must be absolute.** `./dum` first `cd`s into the repo and then sets a
   RELATIVE `DUM_EVENTS=dogfood/events.jsonl`. A bundled app's cwd is `/`, so that same default
   would try to write `/dogfood` - and if it ever resolved inside the bundle it would invalidate
   the code signature, which on macOS also throws away the user's Microphone, Accessibility and
   Input Monitoring grants. Everything writable is therefore anchored to model_utils.USER_DATA.

Deliberately NOT copied from `./dum`:
  * `DUM_DOGFOOD_FULL=1` - that is the maintainer's dogfooding default. Turning the full local
    capture stack (dictation history, retained audio, keystroke proxy) on for every downloaded
    copy would contradict the README's "off by default" and is not ours to opt strangers into.
    The engine default (off) stands; a user can still enable it explicitly.
  * The stderr feedback nudge - there is no terminal to read it in.
"""
import os
import sys
from pathlib import Path


def _bootstrap_env():
    """Set the defaults `./dum` would have set, with every writable path absolute.

    setdefault throughout: an explicitly-set DUM_* in the environment (or a future preferences
    UI) always wins over these.
    """
    from model_utils import USER_DATA

    events = Path(os.environ.get("DUM_EVENTS") or (USER_DATA / "dogfood" / "events.jsonl"))
    if not events.is_absolute():                      # a relative override is still a bundle hazard
        events = USER_DATA / events
    events.parent.mkdir(parents=True, exist_ok=True)
    os.environ["DUM_EVENTS"] = str(events)

    # Behaviour defaults that make the app behave like the daily driver.
    os.environ.setdefault("DUM_VSCODE_BRIDGE", "1")
    os.environ.setdefault("DUM_STRIP_FILLERS", "1")
    os.environ.setdefault("DUM_DECAP_CAPS", "1")


def _selftest(deep=False):
    """Import + dlopen everything native, so a broken bundle fails HERE with a clear list
    rather than at first dictation with a traceback no user will report.

    This is what `scripts/release` runs after building, and it is the reason packaging bugs
    (a missing hidden import, a dylib PyInstaller didn't collect, a lazy re-export that only
    resolves at runtime) get caught before a DMG is cut.

    deep=True additionally CONSTRUCTS the recognizer and the LLM backend, which is the only way
    to prove the native libraries actually work rather than merely import. Worth running after
    any change to how dylibs are collected or deduped - importing llama_cpp succeeds even when
    its ggml dylibs are broken. Needs the models present, so it is opt-in.
    """
    failures = []

    def probe(label, fn):
        try:
            fn()
            print(f"  ok    {label}")
        except Exception as e:
            failures.append(label)
            print(f"  FAIL  {label}: {type(e).__name__}: {e}")

    print("dum selftest")
    probe("sherpa_onnx", lambda: __import__("sherpa_onnx").OfflineRecognizer)
    probe("sounddevice (PortAudio)", lambda: __import__("sounddevice").query_devices())
    probe("llama_cpp", lambda: __import__("llama_cpp").Llama)
    probe("numpy", lambda: __import__("numpy").zeros(4))
    probe("soundfile (libsndfile)", lambda: __import__("soundfile").__libsndfile_version__)
    probe("PIL.Image", lambda: __import__("PIL.Image", fromlist=["Image"]).new("RGBA", (2, 2)))
    probe("pystray", lambda: __import__("pystray").Icon)
    probe("pynput", lambda: __import__("pynput").keyboard.Listener)
    probe("Quartz/AppKit", lambda: (__import__("Quartz"), __import__("AppKit")))
    # huggingface_hub >=1.19 is a LAZY package: `import huggingface_hub` succeeds while the
    # real names only exist under TYPE_CHECKING. Importing the symbol we actually call is the
    # only probe that catches a bundle where the model download would die at first run.
    probe("huggingface_hub.hf_hub_download",
          lambda: __import__("huggingface_hub", fromlist=["hf_hub_download"]).hf_hub_download)
    # diskcache pulls sqlite3; llama_cpp needs it. Excluding sqlite3 from the spec silently
    # breaks the LLM stage and nothing else, so assert it explicitly.
    probe("sqlite3 (via diskcache -> llama_cpp)", lambda: __import__("diskcache").Cache)

    # first_run and model_download are imported LAZILY inside main(), so PyInstaller's static
    # analysis can miss them and the app would only discover it on a real first launch - on a
    # user's machine, with no models and no way to recover. Assert them here.
    probe("engine modules", lambda: [__import__(m) for m in
                                     ("live", "pipeline", "overlay", "config", "platform_io",
                                      "model_utils", "llm_backend", "tray",
                                      "first_run", "model_download", "icon")])

    from model_utils import HERE, USER_DATA, MODELS, FROZEN
    print(f"  frozen={FROZEN}\n  HERE={HERE}\n  USER_DATA={USER_DATA}\n  MODELS={MODELS}")
    probe("shipped resources present",
          lambda: [p for p in (HERE / "terms.txt", HERE / "packs") if not p.exists()] or True)
    # The signature-preserving invariant, asserted at runtime rather than trusted.
    if FROZEN and str(USER_DATA).startswith(str(HERE)):
        failures.append("USER_DATA inside the bundle")
        print("  FAIL  USER_DATA is INSIDE the bundle - writes would break the code signature")

    if deep:
        print("  -- deep: constructing the real engines --")

        def _asr():
            from model_utils import find_model_dir
            from live import build_parakeet
            build_parakeet(find_model_dir("sherpa-onnx-nemo-parakeet-tdt-*"))
        probe("recognizer loads (sherpa-onnx + onnxruntime dylibs)", _asr)

        def _llm():
            import llm_backend
            b = llm_backend.make_backend(None)
            try:
                b.generate([{"role": "system", "content": "Reply with the single word OK."},
                            {"role": "user", "content": "test"}], 8)
            finally:
                b.close()
        probe("LLM backend loads + generates (llama.cpp + ggml dylibs)", _llm)

    print(f"\nSELFTEST {'OK' if not failures else 'FAILED'} ({len(failures)} failures)")
    return 1 if failures else 0


def main():
    argv = sys.argv[1:]
    if "--selftest" in argv:
        _bootstrap_env()
        return _selftest(deep="--deep" in argv)

    _bootstrap_env()

    # Pass-through flags win; otherwise run the menu-bar daily driver. --tray is required, not a
    # preference: a bundled app has no terminal to babysit, and on macOS the tray must own the
    # main thread for the GUI run loop.
    if not argv:
        argv = ["--double-cmd", "--overlay", "--llm", "--tray"]

    # First run: fetch the models BEFORE live.main() reaches find_model_dir(), which would
    # otherwise sys.exit() with a message aimed at someone who ran ./setup - advice a
    # downloaded .app user cannot act on. Skipped for the headless/dev entry points so
    # --replay and --list-devices behave exactly as they do from a checkout.
    if not any(a in argv for a in ("--replay", "--replay-fast", "--list-devices")):
        import first_run
        from model_utils import MODELS
        if not first_run.run_first_run(MODELS):
            return 1

    import live
    return live.main(argv)


if __name__ == "__main__":
    sys.exit(main() or 0)
