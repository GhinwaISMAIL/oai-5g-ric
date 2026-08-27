"""Regression tests for fixed-tap channelmod scalar controls."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-oai-channelmod-scalar-control.py"
SPEC = importlib.util.spec_from_file_location(
    "oai_channelmod_scalar_control_patch", PATCH_SCRIPT
)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


SOURCE = (
    PATCH_MODULE.ORIGINAL_HEADER
    + "  int s = 3;\n"
    + PATCH_MODULE.ORIGINAL_SCALAR_BLOCK
    + "    }\n"
    + PATCH_MODULE.ORIGINAL_TAIL
    + "  }\n  return 0;\n}\n"
)


class PatchOaiChannelmodScalarControlTest(unittest.TestCase):
    def test_scalar_controls_do_not_regenerate_taps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "random_channel.c"
            path.write_text(SOURCE)

            self.assertTrue(PATCH_MODULE.patch_file(path))
            result = path.read_text()
            self.assertFalse(PATCH_MODULE.patch_file(path))

            self.assertEqual(path.read_text(), result)
            self.assertEqual(result.count("regenerate_channel = 0;"), 2)
            self.assertIn("if (regenerate_channel)\n      random_channel", result)

    def test_rejects_partial_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "random_channel.c"
            path.write_text(
                SOURCE.replace(
                    PATCH_MODULE.ORIGINAL_HEADER, PATCH_MODULE.PATCHED_HEADER
                )
            )

            with self.assertRaisesRegex(RuntimeError, "partial"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
