#!/usr/bin/env python3
"""Unit tests for the user config + first-run wizard (config.py).

Headless: every interactive path is driven with a mocked input_fn + a StringIO `out`,
so no TTY/mic is needed. Covers:
  * load/save round-trip
  * default-when-missing (no file) and default-healing (corrupt/partial file)
  * wizard input parsing - numbered choice AND Enter-for-default - with mocked stdin
  * the no-regression default (recommended choices == today's behavior)

The interactive hotkey firing / push-to-talk / real mic capture are NOT testable
headlessly - flagged PENDING in the build report.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path

import config


def _feed(*answers):
    """Build an input_fn that returns the given answers in order (then raises if
    over-consumed - catches a picker that loops when it shouldn't)."""
    it = iter(answers)

    def _fn():
        try:
            return next(it)
        except StopIteration:
            raise AssertionError("picker asked for more input than expected")
    return _fn


class TestLoadSaveRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            # alt_r + push: the one key/mode pair the catalog offers for push-to-dictate.
            # Pairing push with cmd_r would now heal to toggle, since holding ⌘ bare would
            # swallow every ⌘ shortcut for as long as you talk.
            cfg = {"mic": "Studio Mic", "hotkey_key": "alt_r", "hotkey_mode": "push"}
            config.save_config(cfg, p)
            self.assertTrue(p.exists())
            loaded = config.load_config(p)
            self.assertEqual(loaded["mic"], "Studio Mic")
            self.assertEqual(loaded["hotkey_key"], "alt_r")
            self.assertEqual(loaded["hotkey_mode"], "push")

    def test_retired_hotkey_key_degrades_to_default(self):
        # An old config naming a key no longer in the catalog must degrade gracefully to the
        # default, not crash. The example is "fn": it was removed once we found pynput exposes
        # no Key.function on macOS, so building a listener for it raised AttributeError and
        # killed the app. Existing configs may still name it, and they must heal on load.
        # (This test used to use alt_r. Right ⌥ is back in the catalog as the ONLY
        # push-to-dictate trigger - it does not collide with the hardcoded flag-a-problem
        # gesture, which is on LEFT ⌥.)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps(
                {"mic": "Studio Mic", "hotkey_key": "fn", "hotkey_mode": "push"}))
            loaded = config.load_config(p)
            self.assertEqual(loaded["hotkey_key"], config.DEFAULT_KEY)
            self.assertEqual(loaded["mic"], "Studio Mic")
            # The mode heals too: the default key is toggle-only, so a saved "push" cannot
            # survive alongside it - an unsupported PAIR is as invalid as an unknown key.
            self.assertEqual(loaded["hotkey_mode"], "toggle")

    def test_save_only_known_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            config.save_config({"mic": None, "hotkey_key": "cmd_l",
                                "hotkey_mode": "toggle", "junk": 1}, p)
            data = json.loads(p.read_text())
            # autostart_offered is the one-time latch for enabling auto-start on a new
             # install; it must survive a save or turning auto-start off would not stick.
            self.assertEqual(set(data.keys()),
                             {"mic", "hotkey_key", "hotkey_mode", "autostart_offered"})

    def test_mic_index_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            config.save_config({"mic": 2, "hotkey_key": "cmd_l", "hotkey_mode": "toggle"}, p)
            self.assertEqual(config.load_config(p)["mic"], 2)


class TestDefaults(unittest.TestCase):
    def test_default_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "nope.json"
            self.assertFalse(config.config_exists(p))
            cfg = config.load_config(p)
            self.assertEqual(cfg, config.default_config())
            # the platform default (left ⌘ on macOS, right Ctrl on Win/Linux), toggle, sys-default mic
            self.assertEqual(cfg["hotkey_key"], config.DEFAULT_KEY)
            self.assertEqual(cfg["hotkey_mode"], "toggle")
            self.assertIsNone(cfg["mic"])

    def test_corrupt_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text("{ this is not json")
            self.assertEqual(config.load_config(p), config.default_config())

    def test_partial_and_invalid_fields_healed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            # valid mic, bogus key, bogus mode -> bogus ones revert to defaults
            p.write_text(json.dumps({"mic": "X", "hotkey_key": "bogus", "hotkey_mode": "nope"}))
            cfg = config.load_config(p)
            self.assertEqual(cfg["mic"], "X")
            self.assertEqual(cfg["hotkey_key"], config.DEFAULT_KEY)
            self.assertEqual(cfg["hotkey_mode"], config.DEFAULT_MODE)


class TestMicPicker(unittest.TestCase):
    DEVICES = [(0, "MacBook Air Microphone"), (1, "Studio Mic"), (2, "USB Cam")]

    def test_enter_accepts_recommended_builtin(self):
        out = io.StringIO()
        # Even though the system default points at "Studio Mic" (idx 1), the built-in
        # MacBook mic is recommended; Enter (empty) picks it.
        chosen = config.pick_mic(self.DEVICES, 1, _feed(""), out)
        self.assertEqual(chosen, "MacBook Air Microphone")
        self.assertIn("(recommended)", out.getvalue())

    def test_builtin_recommended_over_continuity_iphone(self):
        # Elias's real case: the iPhone Continuity mic grabs the system-default slot,
        # but the wizard must recommend the built-in MacBook mic as the daily base.
        devices = [(0, "iPhone Elias Microphone"), (1, "MacBook Air Microphone")]
        out = io.StringIO()
        chosen = config.pick_mic(devices, 0, _feed(""), out)  # idx 0 = system default
        self.assertEqual(chosen, "MacBook Air Microphone")
        # the (recommended) tag sits on the MacBook line, not the iPhone line
        macbook_line = [ln for ln in out.getvalue().splitlines() if "MacBook" in ln][0]
        self.assertIn("(recommended)", macbook_line)

    def test_recommends_system_default_when_no_builtin(self):
        # No built-in mic present -> fall back to the system default (idx 1).
        devices = [(0, "USB Cam"), (1, "Studio Mic")]
        chosen = config.pick_mic(devices, 1, _feed(""), io.StringIO())
        self.assertEqual(chosen, "Studio Mic")

    def test_numbered_choice(self):
        out = io.StringIO()
        chosen = config.pick_mic(self.DEVICES, 1, _feed("3"), out)
        self.assertEqual(chosen, "USB Cam")

    def test_reprompts_on_bad_input(self):
        out = io.StringIO()
        chosen = config.pick_mic(self.DEVICES, 0, _feed("9", "abc", "2"), out)
        self.assertEqual(chosen, "Studio Mic")

    def test_no_devices_returns_none(self):
        out = io.StringIO()
        self.assertIsNone(config.pick_mic([], None, _feed(), out))

    def test_no_builtin_no_default_recommends_first(self):
        out = io.StringIO()
        # No built-in mic and no system default -> recommend mic 1; Enter picks it
        devices = [(0, "USB Cam"), (1, "Studio Mic")]
        chosen = config.pick_mic(devices, None, _feed(""), out)
        self.assertEqual(chosen, "USB Cam")


class TestModeAndKeyPickers(unittest.TestCase):
    def test_mode_enter_is_toggle(self):
        out = io.StringIO()
        self.assertEqual(config.pick_mode(_feed(""), out), "toggle")
        self.assertIn("(recommended)", out.getvalue())

    def test_mode_push(self):
        self.assertEqual(config.pick_mode(_feed("2"), io.StringIO()), "push")

    def test_key_enter_is_default(self):
        out = io.StringIO()
        # Enter picks this OS's recommended default (cmd_l on macOS, ctrl_r on Win/Linux)
        self.assertEqual(config.pick_key(_feed(""), out), config.DEFAULT_KEY)
        self.assertIn("(recommended)", out.getvalue())

    def test_key_numbered(self):
        # numbered choice picks that position in this OS's curated subset
        self.assertEqual(config.pick_key(_feed("2"), io.StringIO()), config.CURATED_KEYS[1]["key"])


class TestWizardNoRegression(unittest.TestCase):
    def test_all_defaults_reproduce_today(self):
        """Accepting all recommended defaults (Enter x3) must yield this OS's defaults:
        the platform default trigger (left ⌘ on macOS), toggle mode, recommended mic."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            devices = [(0, "MacBook Air Microphone"), (1, "Studio Mic")]
            cfg = config.run_wizard(devices, default_idx=0,
                                    input_fn=_feed("", "", ""), out=io.StringIO(), path=p)
            self.assertEqual(cfg["hotkey_key"], config.DEFAULT_KEY)
            self.assertEqual(cfg["hotkey_mode"], "toggle")
            self.assertEqual(cfg["mic"], "MacBook Air Microphone")
            # persisted and reloadable
            self.assertEqual(config.load_config(p), {
                "mic": "MacBook Air Microphone",
                "hotkey_key": config.DEFAULT_KEY,
                "hotkey_mode": "toggle",
                "autostart_offered": False,   # the wizard does not consume the one-time offer
            })

    def test_wizard_custom_choices(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            devices = [(0, "Mic A"), (1, "Mic B"), (2, "Mic C")]
            # mic 3, mode push (2), key = 2nd in this OS's curated subset (cmd_r on macOS)
            cfg = config.run_wizard(devices, default_idx=0,
                                    input_fn=_feed("3", "2", "2"), out=io.StringIO(), path=p)
            self.assertEqual(cfg["mic"], "Mic C")
            self.assertEqual(cfg["hotkey_mode"], "push")
            self.assertEqual(cfg["hotkey_key"], config.CURATED_KEYS[1]["key"])

    def test_wizard_no_save(self):
        cfg = config.run_wizard([(0, "Mic A")], 0,
                                input_fn=_feed("", "", ""), out=io.StringIO(), save=False)
        self.assertEqual(cfg["mic"], "Mic A")


class TestMicPrecedence(unittest.TestCase):
    """Exercises the REAL precedence helper main() uses (config.resolve_mic_spec):
    --mic / DUM_MIC (flag/env) > config > built-in."""

    BUILTIN = "MacBook Air"

    def _resolve(self, flag_mic, env_mic, cfg_mic, builtin):
        return config.resolve_mic_spec(flag_mic, env_mic, cfg_mic, builtin)

    def test_flag_wins_over_everything(self):
        self.assertEqual(self._resolve("1", "EnvMic", "CfgMic", self.BUILTIN), "1")

    def test_env_wins_over_config(self):
        self.assertEqual(self._resolve(None, "EnvMic", "CfgMic", self.BUILTIN), "EnvMic")

    def test_config_wins_over_builtin(self):
        self.assertEqual(self._resolve(None, None, "CfgMic", self.BUILTIN), "CfgMic")

    def test_builtin_when_nothing_set(self):
        self.assertEqual(self._resolve(None, None, None, self.BUILTIN), self.BUILTIN)
        self.assertEqual(self._resolve(None, "", "", self.BUILTIN), self.BUILTIN)


class TestTriggerCatalog(unittest.TestCase):
    """The catalog must only contain triggers that can actually fire.

    A `fn` entry shipped here with pynput name "function". pynput exposes no Key.function on
    macOS, so building the listener raised AttributeError and killed the app the moment anyone
    selected it - a crash reachable straight from the settings menu. test_every_pynput_name
    is the guard; it fails on any catalog entry the keyboard backend cannot resolve.
    """

    def test_every_pynput_name_resolves(self):
        try:
            from pynput import keyboard
        except Exception:
            self.skipTest("pynput unavailable")
        for entry in config._ALL_KEYS:
            with self.subTest(key=entry["key"]):
                self.assertIsNotNone(
                    getattr(keyboard.Key, entry["pynput"], None),
                    f"{entry['key']!r} maps to pynput {entry['pynput']!r}, which does not exist"
                    " - selecting this trigger would crash the app")

    def test_double_tap_keys_never_offer_push(self):
        # "Double-tap and then hold" is not a gesture; offering it produced a trigger that
        # silently never fired.
        for entry in config._ALL_KEYS:
            if entry["gesture"] == "double":
                self.assertNotIn("push", entry.get("modes", ()),
                                 f"{entry['key']} is a double-tap key and cannot do push")

    def test_triggers_are_all_valid_combinations(self):
        for t in config.triggers("darwin"):
            entry = config.key_descriptor(t["key"])
            self.assertIn(t["mode"], entry["modes"])
            self.assertIn(t["group"], ("tap", "hold"))
            self.assertTrue(t["label"])

    def test_every_toggle_trigger_is_actually_a_double_tap(self):
        """The label must match what the listener really does.

        live.run_double_tap_toggle hard-wires toggle mode to a DOUBLE tap: the catalog's
        `gesture` field shapes the label and nothing else. A "Press right ⌥" entry therefore
        told the user a single press would work while silently requiring two - it looked
        broken. Any toggle trigger that does not say "Double-tap" is lying.
        """
        for tr in config.triggers("darwin"):
            if tr["mode"] == "toggle":
                self.assertTrue(tr["label"].startswith("Double-tap"),
                                f"{tr['label']!r} is a toggle trigger but does not promise a "
                                "double-tap, which is all the listener implements")

    def test_an_invalid_key_mode_pair_heals(self):
        """Key and mode are validated independently, so a config can hold a PAIR the catalog
        no longer offers - e.g. alt_r + "toggle" after right ⌥ became push-only. Such a config
        still drives the app but matches nothing in the menu, leaving no option selected."""
        import tempfile, json, pathlib
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.json"
            p.write_text(json.dumps({"mic": None, "hotkey_key": "alt_r",
                                     "hotkey_mode": "toggle"}))
            cfg = config.load_config(p)
            supported = config.key_descriptor(cfg["hotkey_key"])["modes"]
            self.assertIn(cfg["hotkey_mode"], supported)

    def test_every_saved_pair_matches_a_menu_entry(self):
        # Whatever load_config returns must correspond to something selectable.
        pairs = {(t["key"], t["mode"]) for t in config.triggers("darwin")}
        for key, _ in ((k["key"], None) for k in config.curated_keys("darwin")):
            for mode in ("toggle", "push"):
                import tempfile, json, pathlib
                with tempfile.TemporaryDirectory() as td:
                    p = pathlib.Path(td) / "c.json"
                    p.write_text(json.dumps({"mic": None, "hotkey_key": key,
                                             "hotkey_mode": mode}))
                    cfg = config.load_config(p)
                    self.assertIn((cfg["hotkey_key"], cfg["hotkey_mode"]), pairs,
                                  f"{key}+{mode} healed to something not in the menu")

    def test_push_is_offered_and_only_on_holdable_keys(self):
        holds = [t for t in config.triggers("darwin") if t["group"] == "hold"]
        self.assertTrue(holds, "push-to-dictate must be reachable")
        for t in holds:
            self.assertEqual(config.key_descriptor(t["key"])["gesture"], "single")

    def test_holds_are_listed_first(self):
        # Push-to-dictate leads the menu - it is the headline trigger.
        groups = [t["group"] for t in config.triggers("darwin")]
        self.assertEqual(groups[0], "hold")
        self.assertEqual(groups, sorted(groups, key=lambda g: g != "hold"))

    def test_every_platform_has_at_least_one_trigger(self):
        for plat in ("darwin", "win32", "linux"):
            self.assertTrue(config.triggers(plat), f"no triggers offered on {plat}")

    def test_a_removed_key_heals_to_the_default(self):
        # Existing users may have "fn" saved from the old catalog. It must not survive load.
        import tempfile, json, pathlib
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.json"
            p.write_text(json.dumps({"mic": None, "hotkey_key": "fn", "hotkey_mode": "push"}))
            cfg = config.load_config(p)
            self.assertNotEqual(cfg["hotkey_key"], "fn",
                                "a config naming the removed fn key must heal, not crash later")
            self.assertIn(cfg["hotkey_key"], {k["key"] for k in config._ALL_KEYS})


if __name__ == "__main__":
    unittest.main()
