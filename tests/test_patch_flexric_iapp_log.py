#!/usr/bin/env python3
"""Regression tests for the pinned FlexRIC iApp file-log patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-flexric-iapp-log.py"
SPEC = importlib.util.spec_from_file_location("flexric_iapp_log_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


class PatchFlexricIappLogTest(unittest.TestCase):
    def test_redirects_log_to_dev_null_and_is_idempotent(self) -> None:
        source = (
            '#include <stdio.h>\n\n'
            'const char* file_path = "log.txt";\n'
            'static FILE* fp = NULL;\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stdout.c"
            path.write_text(source)

            self.assertTrue(PATCH_MODULE.patch_file(path))
            first_result = path.read_text()
            self.assertFalse(PATCH_MODULE.patch_file(path))

            self.assertEqual(path.read_text(), first_result)
            self.assertIn(PATCH_MODULE.DISABLED, first_result)
            self.assertNotIn(PATCH_MODULE.ORIGINAL, first_result)

    def test_rejects_unknown_source_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stdout.c"
            path.write_text('const char* file_path = "somewhere-else";\n')

            with self.assertRaisesRegex(RuntimeError, "expected exactly one"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
