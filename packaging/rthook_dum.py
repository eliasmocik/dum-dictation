"""
PyInstaller runtime hook - runs BEFORE any of dum's modules are imported.

Only put things here that must be true before `import live` happens. Everything else belongs
in packaging/dum_app.py, which is ordinary code and far easier to read and test.

The one job: make sure a stray relative path can never resolve inside the .app bundle.

A frozen app's working directory is `/` (Finder) or whatever launchd hands it - not the app
directory. Any code that writes to a relative path is therefore writing somewhere arbitrary,
and if it ever landed inside the bundle it would invalidate the code signature. On macOS that
is not a cosmetic problem: TCC keys the user's Microphone, Accessibility and Input Monitoring
grants to that signature, so a single stray write can silently cost the user all three - two of
which are behind an admin unlock to restore.

So: chdir to the writable data directory. Relative writes then land somewhere harmless and
predictable instead of somewhere signed.
"""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # Mirror model_utils' resolution rather than importing it - this hook runs before sys.path
    # is arranged for the app's own modules, and duplicating two lines is cheaper than an
    # import-order dependency. Kept in sync by tests/test_resource_roots.py.
    data_dir = Path(os.environ.get("DUM_DATA_DIR") or (Path.home() / ".dum"))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(data_dir)
    except OSError:
        # A read-only or missing home is survivable; don't take the app down over it. The
        # absolute paths set in dum_app._bootstrap_env() are the real guarantee - this is
        # defence in depth for code that ignores them.
        pass
