#!/usr/bin/env python3
"""Regression tests for the FlexRIC xApp capture patch and its place in core-setup.sh."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-flexric-xapp-capture.py"
CORE_SETUP = REPOSITORY / "bin" / "core-setup.sh"
SPEC = importlib.util.spec_from_file_location("flexric_xapp_capture_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)

WRAPPER_SOURCE = (
    "void init_db_sqlite3(sqlite3** db, char const* db_filename)\n"
    "{\n"
    '  int const rc_3 = sqlite3_exec(*db, "pragma synchronous=normal" , 0, 0, &err_msg);\n'
    "}\n"
)
MONITOR_SOURCE = "".join(f'  const char* i_{index} = "1_ms";\n' for index in range(4))


class PatchFlexricXappCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.wrapper = Path(directory.name) / "sqlite3_wrapper.c"
        self.monitor = Path(directory.name) / "monitor.c"

    def apply(self) -> None:
        argv = ["patch-flexric-xapp-capture.py", str(self.wrapper), str(self.monitor)]
        with mock.patch.object(sys, "argv", argv):
            PATCH_MODULE.main()

    def test_patches_both_files(self) -> None:
        self.wrapper.write_text(WRAPPER_SOURCE)
        self.monitor.write_text(MONITOR_SOURCE)
        self.apply()
        wrapper_text = self.wrapper.read_text()
        monitor_text = self.monitor.read_text()
        self.assertIn("pragma synchronous=off; pragma wal_autocheckpoint=20000", wrapper_text)
        self.assertNotIn("pragma synchronous=normal", wrapper_text)
        self.assertEqual(monitor_text.count('"10_ms"'), 4)
        self.assertNotIn('"1_ms"', monitor_text)

    def test_second_run_fails_and_changes_nothing(self) -> None:
        self.wrapper.write_text(WRAPPER_SOURCE)
        self.monitor.write_text(MONITOR_SOURCE)
        self.apply()
        wrapper_after = self.wrapper.read_text()
        monitor_after = self.monitor.read_text()
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.wrapper.read_text(), wrapper_after)
        self.assertEqual(self.monitor.read_text(), monitor_after)

    def test_missing_wrapper_text_changes_nothing(self) -> None:
        self.wrapper.write_text("int unrelated;\n")
        self.monitor.write_text(MONITOR_SOURCE)
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.wrapper.read_text(), "int unrelated;\n")
        self.assertEqual(self.monitor.read_text(), MONITOR_SOURCE)

    def test_missing_monitor_interval_leaves_wrapper_unchanged(self) -> None:
        three = "".join(f'  const char* i_{index} = "1_ms";\n' for index in range(3))
        self.wrapper.write_text(WRAPPER_SOURCE)
        self.monitor.write_text(three)
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.wrapper.read_text(), WRAPPER_SOURCE)
        self.assertEqual(self.monitor.read_text(), three)

    def test_wrong_argument_count_exits(self) -> None:
        with mock.patch.object(sys, "argv", ["patch-flexric-xapp-capture.py"]):
            with self.assertRaises(SystemExit):
                PATCH_MODULE.main()


class CoreSetupOrderTest(unittest.TestCase):
    def test_capture_patch_runs_after_the_other_patches_and_is_checked_after_install(self) -> None:
        text = CORE_SETUP.read_text()
        order = [
            "patch-flexric-iapp-log.py",
            "patch-flexric-xapp-capture.py",
            "make install || exit 1",
            "for needle in 'pragma synchronous=off' '10_ms'",
        ]
        positions = [text.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
