#!/usr/bin/env python3
"""Regression tests for the pinned OAI RFsim debug telemetry patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-oai-rfsim-debug-telemetry.py"
SPEC = importlib.util.spec_from_file_location("oai_rfsim_debug_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


SOURCE = '''#include <numeric>

static bool flushInput(rfsimulator_state_t *t, int timeout, bool first_time);

static void read_channel(buffer_t *ptr)
{
      if (ptr->channel_model != NULL) { // apply a channel model
        rxAddInput(ptr->channel_model);
      }
}
'''


class PatchOaiRfsimDebugTelemetryTest(unittest.TestCase):
    def test_adds_complete_export_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulator.cpp"
            path.write_text(SOURCE)

            self.assertTrue(PATCH_MODULE.patch_file(path))
            result = path.read_text()
            self.assertFalse(PATCH_MODULE.patch_file(path))

            self.assertEqual(path.read_text(), result)
            self.assertEqual(result.count("RFSIM_CHANNEL_DEBUG_V1"), 1)
            self.assertIn("#include <cmath>", result)
            self.assertIn("#include <ctime>", result)
            self.assertIn("rfsim_channel_tap_energy", result)
            self.assertIn("channel_snapshot_id=static-%lld", result)
            self.assertIn("channel_snapshot_timestamp_ns=%lld", result)
            self.assertIn("tap_energy_linear=%.17g", result)
            self.assertIn("applied_gain_db=%.9f", result)
            self.assertIn("noise_power_db=%.9f", result)
            self.assertIn("log_rfsim_channel_debug(ptr->channel_model);", result)

    def test_rejects_partial_existing_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulator.cpp"
            path.write_text(SOURCE + "\n// RFSIM_CHANNEL_DEBUG_V1\n")
            with self.assertRaisesRegex(RuntimeError, "partial RFsim debug telemetry patch"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
