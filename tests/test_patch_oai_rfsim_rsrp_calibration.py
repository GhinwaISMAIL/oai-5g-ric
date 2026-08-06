#!/usr/bin/env python3
"""Regression tests for the pinned OAI RFsim RSRP scale patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-oai-rfsim-rsrp-calibration.py"
DOCKERFILE = REPOSITORY / "etc" / "Dockerfile.nrUE-radio"
SPEC = importlib.util.spec_from_file_location("oai_rfsim_rsrp_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


SOURCE = r'''#define RFSIMU_PROP_DELAY "prop_delay"

#define RFSIMULATOR_PARAMS_DESC { \
  DOUBLEPARAM(RFSIMU_PROP_DELAY,        "<propagation delay in ms>\n",              simOpt, NULL,                             0.0),                   \
};

typedef struct {
  double prop_delay_ms;
} rfsimulator_state_t;

static void rfsimulator_readconfig(rfsimulator_state_t *rfsimulator)
{
  rfsimulator->prop_delay_ms = *(gpd(rfsimuParam, sizeofArray(rfsimuParams), RFSIMU_PROP_DELAY)->dblptr);
}

int device_init(openair0_device_t *device, openair0_config_t *openair0_cfg)
{
  openair0_cfg->rx_gain[0] = 0;
}
'''


class PatchOaiRfsimRsrpCalibrationTest(unittest.TestCase):
    def test_runtime_image_copies_patched_rfsimulator_library(self) -> None:
        dockerfile = DOCKERFILE.read_text()
        self.assertIn(
            "/oai-ran/cmake_targets/ran_build/build/librfsimulator.so",
            dockerfile,
        )
        self.assertIn("/usr/local/lib/librfsimulator.so", dockerfile)

    def test_adds_scale_offset_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulator.cpp"
            path.write_text(SOURCE)

            self.assertTrue(PATCH_MODULE.patch_file(path))
            result = path.read_text()
            self.assertFalse(PATCH_MODULE.patch_file(path))

            self.assertEqual(path.read_text(), result)
            self.assertIn('"rsrp_offset_dB"', result)
            self.assertIn("openair0_cfg->rx_gain[0] = -rfsimulator->rsrp_offset_dB;", result)

    def test_rejects_partial_existing_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulator.cpp"
            path.write_text(SOURCE + "\n#define RFSIMU_RSRP_OFFSET 1\n")
            with self.assertRaisesRegex(RuntimeError, "partial RFsim RSRP calibration patch"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
