from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "bin" / "run-phase3c13-static-tdlb-pilot.py"
SPEC = importlib.util.spec_from_file_location("phase3c13_static_tdlb", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunPhase3c13StaticTdlbPilotTest(unittest.TestCase):
    def test_protocol_constants_freeze_five_seeded_tdlb_replays(self) -> None:
        self.assertEqual(MODULE.CHANNEL_FAMILY, "TDL_B")
        self.assertEqual(MODULE.TDL_RMS_DELAY_SPREAD_NS, 30)
        self.assertEqual(MODULE.RNG_SEEDS, (31001, 31002, 31003, 31004, 31005))
        self.assertEqual(MODULE.DEBUG_IMAGE, "oai-nr-ue-phase3c13-static:70508eb")
        self.assertEqual(MODULE.ORIGINAL_IMAGE, "ghinwa555/oai-nr-ue-chan:v5")
        self.assertEqual(MODULE.ENVELOPE, (
            ("baseline", 0.0),
            ("descent", -2.0),
            ("nadir", -4.0),
            ("return", -2.0),
            ("recovery", 0.0),
        ))

    def test_runtime_identity_arguments_are_mandatory(self) -> None:
        actions = {action.dest: action.required for action in MODULE.parser()._actions}
        self.assertTrue(actions["expected_profile_revision"])
        self.assertTrue(actions["expected_runner_sha256"])
        self.assertTrue(actions["expected_debug_image_id"])

    def test_override_pins_image_and_rng_seed(self) -> None:
        self.assertEqual(
            MODULE.override_text("debug:image", 31003),
            "services:\n"
            "  oai-nr-ue1:\n"
            "    image: debug:image\n"
            "    environment:\n"
            '      OAI_RNGSEED: "31003"\n',
        )

    def test_telemetry_join_requires_seed_and_tap_fingerprint(self) -> None:
        logs = (
            "UE_RADIO_DEBUG_V1 utc_second=103 "
            "rsrp_digital_power_linear=10 rsrp_db_per_re_unquantized=20.25 "
            "ss_rsrp_dbm_integer=-35 ss_sinr_db=18.5\n"
            "RFSIM_CHANNEL_DEBUG_V1 utc_second=103 model=rfsimu_channel_enB0 "
            "channel_snapshot_id=static-0 channel_snapshot_timestamp_ns=100 "
            "tap_energy_linear=1.25 tap_fingerprint_fnv1a64=0011223344556677 "
            "channel_length=7 nb_taps=23 nb_tx=1 nb_rx=1 oai_rng_seed=31001 "
            "applied_gain_db=-2 noise_power_db=-30\n"
        )
        segments = [{
            "nominal_start_s": 10.0,
            "applied_epoch": 100.0,
            "commanded_gain_db": -2.0,
        }]
        rows, diagnostics = MODULE.build_telemetry_rows(
            "tdlb-1",
            31001,
            logs,
            segments,
            [{"epoch": 103.4, "attached": True}],
        )
        self.assertEqual(diagnostics["matched_rows"], 1)
        self.assertEqual(rows[0]["channel_family"], "TDL_B")
        self.assertEqual(rows[0]["tap_fingerprint_fnv1a64"], "0011223344556677")
        self.assertEqual(rows[0]["channel_length"], "7")
        self.assertEqual(rows[0]["oai_rng_seed"], "31001")

    def test_telemetry_join_rejects_wrong_seed(self) -> None:
        logs = (
            "UE_RADIO_DEBUG_V1 utc_second=103 rsrp_digital_power_linear=10 "
            "rsrp_db_per_re_unquantized=20 ss_rsrp_dbm_integer=-35 ss_sinr_db=18\n"
            "RFSIM_CHANNEL_DEBUG_V1 utc_second=103 model=rfsimu_channel_enB0 "
            "channel_snapshot_id=static-0 channel_snapshot_timestamp_ns=100 "
            "tap_energy_linear=1 tap_fingerprint_fnv1a64=abc channel_length=7 "
            "nb_taps=23 nb_tx=1 nb_rx=1 oai_rng_seed=999 "
            "applied_gain_db=0 noise_power_db=-30\n"
        )
        with self.assertRaisesRegex(MODULE.ReplayError, "RNG seed mismatch"):
            MODULE.build_telemetry_rows(
                "tdlb-1",
                31001,
                logs,
                [{"nominal_start_s": 0, "applied_epoch": 100, "commanded_gain_db": 0}],
                [{"epoch": 103.4, "attached": True}],
            )

    def test_log_suffix_rejects_non_append_only_stream(self) -> None:
        self.assertEqual(MODULE.log_suffix("prefix-new", "prefix", "UE"), "-new")
        with self.assertRaisesRegex(MODULE.ReplayError, "not append-only"):
            MODULE.log_suffix("different", "prefix", "UE")


if __name__ == "__main__":
    unittest.main()
