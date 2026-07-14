#!/usr/bin/env python3
"""Self-timed evdev hotkey diagnostic. Run it, tap LEFT Ctrl (single + double),
then press Ctrl+C. It mirrors dum's _build_evdev_hotkey double-tap logic so you can
see whether presses arrive and whether the 0.40s double-tap window triggers."""
import time, threading
import evdev
from evdev import ecodes

GAP = 0.40
wanted = {
    ecodes.KEY_LEFTCTRL: "ctrl_l", ecodes.KEY_RIGHTCTRL: "ctrl_r",
    ecodes.KEY_LEFTALT: "alt_l", ecodes.KEY_RIGHTALT: "alt_r",
    ecodes.KEY_LEFTMETA: "cmd_l", ecodes.KEY_RIGHTMETA: "cmd_r",
}
devs = []
for p in evdev.list_devices():
    try:
        d = evdev.InputDevice(p)
        if any(c in wanted for c in d.capabilities().get(ecodes.EV_KEY, [])):
            devs.append(d)
            print("READING:", p, "|", d.name)
    except Exception as e:
        print("skip", p, e)
if not devs:
    raise SystemExit("NO readable key devices - are you in the 'input' group? (id | grep input)")

st = {"t": 0.0, "armed": False}
lock = threading.Lock()

def loop(d):
    try:
        for e in d.read_loop():
            if e.type != ecodes.EV_KEY:
                continue
            tok = wanted.get(e.code)
            if tok == "ctrl_l" and e.value == 1:
                with lock:
                    now = time.monotonic(); gap = now - st["t"]
                    dbl = st["armed"] and gap <= GAP
                    tag = "*** DOUBLE-TAP (would toggle) ***" if dbl else "first tap"
                    print(f"  LCTRL  gap={gap:5.3f}s  {tag}   [{d.name}]", flush=True)
                    if dbl:
                        st["armed"] = False
                    else:
                        st["t"] = now; st["armed"] = True
            elif tok and e.value == 1:
                print(f"  {tok} (breaks pending double-tap)  [{d.name}]", flush=True)
    except Exception as ex:
        print("loop error", d.name, ex, flush=True)

for d in devs:
    threading.Thread(target=loop, args=(d,), daemon=True).start()
print("\n=== Tap LEFT Ctrl: single, then a quick double. Ctrl+C to quit. ===\n", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nbye")
