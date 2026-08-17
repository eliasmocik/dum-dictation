#!/usr/bin/env python3
"""Bake src/icon.py's mark into packaging/dum.icns (build time only).

    .venv/bin/python packaging/make_icon.py

The drawing itself lives in src/icon.py so the running app can share it - see that module.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from icon import monogram          # noqa: E402


def build_icns(out=HERE / "dum.icns"):
    iconset = HERE / "dum.iconset"
    iconset.mkdir(exist_ok=True)
    for px in (16, 32, 128, 256, 512):
        monogram(px).save(iconset / f"icon_{px}x{px}.png")
        monogram(px * 2).save(iconset / f"icon_{px}x{px}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    for p in iconset.iterdir():
        p.unlink()
    iconset.rmdir()
    return out


if __name__ == "__main__":
    monogram(512).save(HERE / "preview.png")
    icns = build_icns()
    print(f"wrote {icns} ({icns.stat().st_size // 1024} KB)")
