# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for dum.app - arm64, onedir, menu-bar only.

Build:   .venv/bin/pyinstaller --noconfirm packaging/dum.spec
Verify:  dist/dum.app/Contents/MacOS/dum --selftest      # must print 0 failures
Sign:    scripts/release sign                            # NOT here - see codesign_identity below

Four things in here are load-bearing and each one was learned by a bundle that built cleanly
and then failed at runtime. Read the comments before changing any of them.
"""
import os
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files, collect_submodules, copy_metadata

REPO = os.path.dirname(os.path.abspath(SPECPATH))   # the spec lives in packaging/


# --- what ships inside the bundle (read-only) ---------------------------------------
# Models are deliberately NOT here: they are ~1.4 GB, they change independently of the code,
# and anything written into a signed bundle invalidates its signature (which on macOS also
# discards the user's TCC permission grants). They download to ~/.dum/models at first run.
datas = [
    (os.path.join(REPO, "packs"), "packs"),      # aliases + terms.txt
]

# huggingface_hub >= 1.19 is a LAZY package: it defines `__getattr__ = _attach(...)` and the
# real names exist only under `if TYPE_CHECKING`. PyInstaller's static analysis therefore sees
# no submodules at all, `import huggingface_hub` succeeds, and `from huggingface_hub import
# hf_hub_download` dies at runtime - i.e. the model download breaks and nothing else does.
# Collect the submodules explicitly and keep the dist-info, which the lazy loader reads.
hiddenimports = collect_submodules("huggingface_hub")
datas += copy_metadata("huggingface_hub")

hiddenimports += [
    # pystray picks its backend at import time; the darwin one is never statically referenced.
    "pystray._darwin",
    # pystray/_darwin.py does a bare `import PIL` and then calls PIL.Image.new(). That only
    # works unfrozen because something else imported PIL.Image first (tray.py happens to).
    # Relying on that is luck, not design - pin it.
    "PIL.Image", "PIL.ImageDraw",
    # pyobjc framework bindings are loaded dynamically by name.
    "objc", "Foundation", "AppKit", "Quartz",
    # pynput selects its macOS backend at runtime.
    "pynput.keyboard._darwin", "pynput.mouse._darwin",
]

binaries = []
for pkg in ("sherpa_onnx", "llama_cpp", "soundfile", "sounddevice", "_soundfile_data"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass          # not all of these are packages on every platform
for pkg in ("sounddevice", "soundfile"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass


# --- what must NOT ship -------------------------------------------------------------
# MLX is Apple-Silicon-only and is now an opt-in alternative backend (DUM_LLM_BACKEND=mlx);
# llama.cpp is the default on every OS and is measurably faster here. Carrying a second
# inference engine the shipped app never uses costs ~298 MB on disk - the single biggest
# saving available. It stays fully usable in a git checkout.
#
# NOTE the absence of "sqlite3": llama_cpp needs it via diskcache. Excluding it produces a
# bundle that builds, launches, dictates - and then silently never loads the LLM. --selftest
# asserts it for exactly this reason. Do not add it here.
excludes = [
    "mlx", "mlx_lm", "mlx_metal", "transformers", "tokenizers", "sentencepiece",
    "torch", "tensorflow",                       # never used; guard against transitive pull-in
    "PyObjCTest", "tkinter", "pytest", "jiwer",  # dev/test cruft
    "matplotlib", "IPython", "notebook",
]


a = Analysis(
    [os.path.join(REPO, "packaging", "dum_app.py")],
    pathex=[os.path.join(REPO, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[os.path.join(REPO, "packaging", "rthook_dum.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="dum",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX corrupts Mach-O dylibs and breaks codesign validation. Off on macOS by default;
    # stated explicitly so nobody "optimises" it on.
    upx=False,
    # console=False is what makes this a GUI app. It is ALSO what keeps LSUIElement working:
    # with console=True, PyInstaller injects LSBackgroundOnly=True, which is stricter than
    # LSUIElement and would stop the pystray menu from opening at all.
    console=False,
    target_arch="arm64",
    # Signing happens in scripts/release, NOT here. Passing a real identity on this line makes
    # PyInstaller add --options=runtime (hardened runtime), and a self-signed cert has no Team
    # ID, so Library Validation then refuses every nested dylib:
    #   "mapping process and mapped file (non-platform) have different Team IDs"
    # i.e. sherpa-onnx / numpy / llama.cpp all fail to load. Leaving this None means PyInstaller
    # ad-hoc signs, and the release script re-signs over the top with the project cert.
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="dum",
)

app = BUNDLE(
    coll,
    name="dum.app",
    icon=os.path.join(REPO, "packaging", "dum.icns") if os.path.exists(
        os.path.join(REPO, "packaging", "dum.icns")) else None,
    bundle_identifier="sk.zaprazny.dum",   # matches the existing launchd label - do not change
    info_plist={
        # Menu-bar only: no Dock icon, no app switcher entry. The welcome window flips the
        # activation policy temporarily (see the first-run work) rather than dropping this.
        "LSUIElement": True,
        # Without this the system TERMINATES the app on first mic access - it is not a soft
        # deny. The string is user-visible in the permission dialog.
        "NSMicrophoneUsageDescription":
            "dum transcribes your speech on this Mac. Audio is never sent anywhere.",
        "LSMinimumSystemVersion": "11.0",
        "CFBundleShortVersionString": os.environ.get("DUM_VERSION", "0.0.0-dev"),
        "CFBundleVersion": os.environ.get("DUM_VERSION", "0.0.0-dev"),
        "CFBundleName": "dum",
        "CFBundleDisplayName": "dum dictation",
        "NSHighResolutionCapable": True,
    },
)
