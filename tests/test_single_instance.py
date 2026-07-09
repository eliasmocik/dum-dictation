#!/usr/bin/env python3
"""Unit tests for the single-instance guard (single_instance.py).

Runs anywhere fcntl exists (macOS + Linux) - no mic/hotkey/GUI. Covers:
  * acquire() succeeds on a free lock and stamps the holder pid
  * a SECOND acquire on the same path is refused with AlreadyRunning
    (this is what stops two robots fighting over the mic + hotkey)
  * release() frees it so a later launch can acquire again
  * a losing contender does NOT blank the holder's pid (no O_TRUNC race)
  * context-manager form releases on exit
"""
import os
import tempfile
import unittest
from pathlib import Path

from single_instance import SingleInstance, AlreadyRunning


class TestSingleInstance(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.lock = Path(self._dir.name) / "dum.lock"

    def tearDown(self):
        self._dir.cleanup()

    def test_acquire_stamps_pid(self):
        si = SingleInstance(self.lock)
        si.acquire()
        try:
            self.assertEqual(self.lock.read_text().strip(), str(os.getpid()))
        finally:
            si.release()

    def test_second_acquire_is_refused(self):
        first = SingleInstance(self.lock).acquire()
        try:
            with self.assertRaises(AlreadyRunning) as ctx:
                SingleInstance(self.lock).acquire()
            # the friendly error should name the holder pid
            self.assertEqual(ctx.exception.holder_pid, os.getpid())
        finally:
            first.release()

    def test_release_allows_reacquire(self):
        first = SingleInstance(self.lock).acquire()
        first.release()
        second = SingleInstance(self.lock).acquire()  # must not raise
        second.release()

    def test_loser_does_not_blank_holder_pid(self):
        first = SingleInstance(self.lock).acquire()
        try:
            with self.assertRaises(AlreadyRunning):
                SingleInstance(self.lock).acquire()
            # the holder's pid must survive a losing contender's open()
            self.assertEqual(self.lock.read_text().strip(), str(os.getpid()))
        finally:
            first.release()

    def test_context_manager_releases(self):
        with SingleInstance(self.lock):
            with self.assertRaises(AlreadyRunning):
                SingleInstance(self.lock).acquire()
        # left the with-block => lock free again
        SingleInstance(self.lock).acquire().release()


if __name__ == "__main__":
    unittest.main()
