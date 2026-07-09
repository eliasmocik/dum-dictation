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
            cfg = {"mic": "Studio Mic", "hotkey_key": "cmd_r", "hotkey_mode": "push"}
            config.save_config(cfg, p)
            self.assertTrue(p.exists())
            loaded = config.load_config(p)
            self.assertEqual(loaded["mic"], "Studio Mic")
            self.assertEqual(loaded["hotkey_key"], "cmd_r")
            self.assertEqual(loaded["hotkey_mode"], "push")

    def test_retired_hotkey_key_degrades_to_default(self):
        # An old config that saved a now-retired key (alt_r, removed when ⌥ became
        # the report-a-bug-only gesture) must degrade gracefully to the default key,
        # not crash. Other valid fields are preserved.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps(
                {"mic": "Studio Mic", "hotkey_key": "alt_r", "hotkey_mode": "push"}))
            loaded = config.load_config(p)
            self.assertEqual(loaded["hotkey_key"], config.DEFAULT_KEY)
            self.assertEqual(loaded["mic"], "Studio Mic")
            self.assertEqual(loaded["hotkey_mode"], "push")

    def test_save_only_known_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            config.save_config({"mic": None, "hotkey_key": "cmd_l",
                                "hotkey_mode": "toggle", "junk": 1}, p)
            data = json.loads(p.read_text())
            self.assertEqual(set(data.keys()), {"mic", "hotkey_key", "hotkey_mode"})

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


if __name__ == "__main__":
    unittest.main()
