#!/usr/bin/env python3
"""Regression tests for the pinned OAI RFsim noise-scaling patch."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-oai-rfsim-noise-scaling.py"
SPEC = importlib.util.spec_from_file_location("oai_rfsim_noise_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


SOURCE = r'''void rxAddInput(void)
{
  const double noise_per_sample = pow(10, channelDesc->noise_power_dB / 10.0) * 256;
  LOG_D(HW,
        "Input power %f, output power: %f, channel path loss %f, noise coeff: %f \n",
        input_power,
        output_power,
        channelDesc->path_loss_dB,
        10 * log10(noise_per_sample));
}
'''


class PatchOaiRfsimNoiseScalingTest(unittest.TestCase):
    def test_power_db_round_trip(self) -> None:
        for noise_db in (0.0, -5.0, -10.0, -20.0, -30.0, 3.0):
            rms = PATCH_MODULE.noise_rms(noise_db)
            self.assertAlmostEqual(PATCH_MODULE.relative_power_db(rms), noise_db)
            self.assertAlmostEqual(
                math.pow(rms / PATCH_MODULE.REFERENCE_RMS, 2.0),
                math.pow(10.0, noise_db / 10.0),
            )

    def test_legacy_command_mapping(self) -> None:
        for legacy_db in (-30.0, -20.0, -10.0, -5.0, 0.0):
            legacy_rms = PATCH_MODULE.REFERENCE_RMS * math.pow(10.0, legacy_db / 10.0)
            corrected_db = PATCH_MODULE.legacy_equivalent_corrected_db(legacy_db)
            self.assertAlmostEqual(PATCH_MODULE.noise_rms(corrected_db), legacy_rms)

    def test_patches_source_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apply_channelmod.c"
            path.write_text(SOURCE)

            self.assertTrue(PATCH_MODULE.patch_file(path))
            result = path.read_text()
            self.assertFalse(PATCH_MODULE.patch_file(path))

            self.assertEqual(path.read_text(), result)
            self.assertIn(PATCH_MODULE.MARKER, result)
            self.assertIn("noise_power_dB / 20.0", result)
            self.assertNotIn("noise_power_dB / 10.0", result)
            self.assertIn("noise power: %f dB", result)
            self.assertIn("channelDesc->noise_power_dB);", result)

    def test_rejects_partial_existing_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apply_channelmod.c"
            path.write_text(SOURCE + f"\n// {PATCH_MODULE.MARKER}\n")
            with self.assertRaisesRegex(RuntimeError, "partial RFsim noise scaling patch"):
                PATCH_MODULE.patch_file(path)

    def test_image_builds_apply_the_patch(self) -> None:
        radio_build = (REPOSITORY / "bin" / "build-ue-radio-image.sh").read_text()
        vrtsim_build = (REPOSITORY / "bin" / "build-phase3c-vrtsim-images.sh").read_text()
        for script in (radio_build, vrtsim_build):
            self.assertIn("radio/rfsimulator/apply_channelmod.c", script)
            self.assertIn("patch-oai-rfsim-noise-scaling.py", script)


if __name__ == "__main__":
    unittest.main()
