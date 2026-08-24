#!/usr/bin/env python3
"""Regression tests for the pinned OAI NR UE radio measurement patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-oai-ue-radio-measurements.py"
SPEC = importlib.util.spec_from_file_location("oai_ue_radio_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


SOURCE = '''#include "PHY/NR_UE_ESTIMATION/nr_estimation.h"

uint32_t nr_ue_calculate_ssb_rsrp(const NR_DL_FRAME_PARMS *fp,
                                  const c16_t rxdataF[][fp->ofdm_symbol_size],
                                  int ssb_start_subcarrier)
{
  return 1;
}

// Send SSB RSRP measurement to MAC
void nr_ue_ssb_rsrp_measurements(PHY_VARS_NR_UE *ue,
                                 int ssb_index,
                                 const UE_nr_rxtx_proc_t *proc,
                                 const c16_t rxdataF[ue->frame_parms.nb_antennas_rx][ue->frame_parms.ofdm_symbol_size])
{
  const NR_DL_FRAME_PARMS *fp = &ue->frame_parms;
  uint32_t rsrp_avg = 1;
  ue->measurements.ssb_sinr_dB[ssb_index] = 1.0;

  LOG_D(PHY,
        "[UE %d] ssb %d SS-RSRP: %d dBm/RE (%f dB/RE), SS-SINR: %f dB\\n",
        ue->Mod_id);
}
'''


class PatchOaiUeRadioMeasurementsTest(unittest.TestCase):
    def test_adds_complete_export_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nr_ue_measurements.c"
            path.write_text(SOURCE)

            self.assertTrue(PATCH_MODULE.patch_file(path))
            result = path.read_text()
            self.assertFalse(PATCH_MODULE.patch_file(path))

            self.assertEqual(path.read_text(), result)
            self.assertEqual(result.count("UE_RADIO_V1"), 1)
            self.assertEqual(result.count("UE_RADIO_DEBUG_V1"), 1)
            self.assertIn("#include <stdint.h>", result)
            self.assertIn("#include <time.h>", result)
            self.assertIn("UE_RADIO_SSB_RBS * (double)rsrp_avg", result)
            self.assertIn("utc_second != ue_radio_second.utc_second", result)
            self.assertIn("ssb_index == fp->ssb_index", result)
            self.assertIn("ss_rsrq_db=%.3f", result)
            self.assertIn("rsrp_digital_power_linear=%.9f", result)
            self.assertIn("rsrp_db_per_re_unquantized=%.6f", result)
            self.assertIn("ss_rsrp_dbm_integer=%d", result)
            self.assertIn("rsrp_avg,\n                           rsrp_db_per_re", result)

    def test_ss_rsrq_reference_value_for_uniform_power(self) -> None:
        resource_blocks = 20
        resource_elements = resource_blocks * 12
        ratio = resource_blocks / resource_elements
        self.assertAlmostEqual(10.0 * __import__("math").log10(ratio), -10.791812, places=6)

    def test_rejects_partial_existing_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nr_ue_measurements.c"
            path.write_text(SOURCE + "\n// UE_RADIO_V1\n")
            with self.assertRaisesRegex(RuntimeError, "partial UE radio patch"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
