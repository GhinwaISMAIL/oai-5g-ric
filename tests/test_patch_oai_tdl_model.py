#!/usr/bin/env python3
"""Regression tests for the pinned OAI TDL channel-model patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-oai-tdl-model.py"
SPEC = importlib.util.spec_from_file_location("oai_tdl_model_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


SOURCE = (
    "void tdlModel(void) {\n"
    + PATCH_MODULE.OLD_DELAY_BLOCK
    + PATCH_MODULE.OLD_DOPPLER_BLOCK
    + "}\n"
)


class PatchOaiTdlModelTest(unittest.TestCase):
    def test_allocates_per_channel_delays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "random_channel.c"
            path.write_text(SOURCE)

            self.assertTrue(PATCH_MODULE.patch_file(path))
            result = path.read_text()
            self.assertFalse(PATCH_MODULE.patch_file(path))

            self.assertEqual(path.read_text(), result)
            self.assertIn("CHANMODEL_FREE_DELAY", result)
            self.assertIn("chan_desc->delays[i] = tdl_delays[i] * DS_TDL;", result)
            self.assertNotIn("tdl_delays[i] *= DS_TDL", result)
            self.assertIn("Doppler_phase_cur", result)
            self.assertIn("calloc(nb_rx", result)

    def test_completes_partially_patched_live_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "random_channel.c"
            path.write_text(SOURCE.replace(PATCH_MODULE.OLD_DELAY_BLOCK, PATCH_MODULE.NEW_DELAY_BLOCK))

            self.assertTrue(PATCH_MODULE.patch_file(path))
            result = path.read_text()
            self.assertIn(PATCH_MODULE.NEW_DELAY_BLOCK, result)
            self.assertIn(PATCH_MODULE.NEW_DOPPLER_BLOCK, result)
            self.assertFalse(PATCH_MODULE.patch_file(path))

    def test_rejects_mixed_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "random_channel.c"
            path.write_text(SOURCE + PATCH_MODULE.NEW_DELAY_BLOCK)

            with self.assertRaisesRegex(RuntimeError, "mixed TDL model patch"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
