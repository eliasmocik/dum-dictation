#!/usr/bin/env python3
"""Platform dispatcher - the stable import surface (`from platform_io import get_platform`).

OS implementations live one-per-file (platform_mac/windows/linux.py, one owner each); the
shared interface is platform_base.Platform. This module only selects by sys.platform and
re-exports the classes (tests reference platform_io.MacPlatform etc.). Module-level imports of
the OS backends are safe everywhere - every OS-specific import inside them is method-local."""
import sys

from platform_base import Platform, FallbackPlatform, PASTE_SETTLE_S
from platform_mac import MacPlatform
from platform_windows import WindowsPlatform
from platform_linux import LinuxPlatform


def get_platform():
    if sys.platform == "darwin":
        return MacPlatform()
    if sys.platform == "win32":
        return WindowsPlatform()
    if sys.platform.startswith("linux"):
        return LinuxPlatform()
    return FallbackPlatform()
