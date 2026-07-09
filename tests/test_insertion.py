#!/usr/bin/env python3
"""Contract test for the InsertionBackend seam (insertion.py) - the narrow, backend-agnostic
text-insertion interface the core drives. Locks the method names so backends stay swappable."""
import insertion

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"ok  {msg}")


# the agreed narrow interface
for meth in ("start_preview", "update_preview", "commit", "cancel", "supports_marked_text"):
    check(hasattr(insertion.InsertionBackend, meth), f"InsertionBackend defines {meth}()")

base = insertion.InsertionBackend()
check(base.supports_marked_text() is False, "base backend: no marked text by default")

imk = insertion.IMKBackend()
check(isinstance(imk, insertion.InsertionBackend), "IMKBackend is an InsertionBackend")
check(imk.supports_marked_text() is True, "IMKBackend advertises marked-text support")
# insertion-only stub until spike Checkpoint 2 (IPC -> dum IMK)
for meth in ("start_preview", "update_preview", "commit"):
    try:
        getattr(imk, meth)("x")
        raise AssertionError(f"{meth} should be a NotImplementedError stub in V1")
    except NotImplementedError:
        pass
check(True, "IMKBackend lifecycle methods are stubs pending Checkpoint 2")

print(f"\nALL {passed} CHECKS PASSED")
