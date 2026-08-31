from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "bin" / "run-phase3h-dynamic-staircase.py"
PLAN = REPOSITORY / "etc" / "phase3h-dynamic-staircase-plan.csv"
SPEC = importlib.util.spec_from_file_location("phase3h_dynamic_staircase", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunPhase3hDynamicStaircaseTest(unittest.TestCase):
    def test_frozen_plan_has_three_complete_counterbalanced_sequences(self) -> None:
        sequences = MODULE.load_staircase_plan(PLAN)
        self.assertEqual(len(sequences), 3)
        self.assertTrue(all(len(sequence) == 9 for sequence in sequences))
        self.assertEqual(
            {row["state_id"] for sequence in sequences for row in sequence[1:-1]},
            set("ABCDEFG"),
        )
        for state_id in "ABCDEFG":
            positions = {
                row["position"]
                for sequence in sequences
                for row in sequence
                if row["state_id"] == state_id
            }
            self.assertEqual(len(positions), 3)

    def test_plan_checksum_matches_research_protocol(self) -> None:
        self.assertEqual(MODULE.SUPPORT.sha256(PLAN), MODULE.EXPECTED_PLAN_SHA256)

    def test_failure_window_starts_after_attachment_stabilization(self) -> None:
        self.assertGreaterEqual(MODULE.POST_ATTACH_STABILIZATION_SECONDS, 5.0)

    def test_final_anchor_receives_full_settling_interval(self) -> None:
        self.assertEqual(
            MODULE.ANCHOR_END_SETTLING_SECONDS,
            MODULE.ANCHOR_START_SETTLING_SECONDS,
        )

    def test_segment_join_records_dynamic_gain_and_noise(self) -> None:
        segment = {
            "sequence_index": 1,
            "repetition": 1,
            "oai_rng_seed": 45001,
            "segment_index": 2,
            "segment_type": "validation",
            "state_id": "A",
            "position": 1,
            "gain_db": -14.0,
            "noise_power_db": -34.0,
            "expected_relative_rsrp_db": -4.18,
            "expected_sinr_db": 20.35,
        }
        logs = (
            "UE_RADIO_DEBUG_V1 utc_second=103 rsrp_digital_power_linear=10 "
            "rsrp_db_per_re_unquantized=37 ss_rsrp_dbm_integer=-40 ss_sinr_db=20\n"
            "RFSIM_CHANNEL_DEBUG_V1 utc_second=103 model=rfsimu_channel_enB0 "
            "channel_snapshot_id=static-0 channel_snapshot_timestamp_ns=100 "
            "tap_energy_linear=1 tap_fingerprint_fnv1a64=abc channel_length=1 "
            "nb_taps=1 nb_tx=1 nb_rx=1 oai_rng_seed=45001 "
            "applied_gain_db=-14 noise_power_db=-34\n"
        )
        rows, diagnostics = MODULE.build_segment_telemetry(
            "s1", segment, logs, 103.0, 104.0
        )
        self.assertEqual(diagnostics["matched_usable_rows"], 1)
        self.assertEqual(rows[0]["state_id"], "A")
        self.assertEqual(rows[0]["commanded_gain_db"], -14.0)
        self.assertEqual(rows[0]["applied_gain_db"], "-14")
        self.assertEqual(rows[0]["commanded_noise_power_db"], -34.0)
        self.assertEqual(rows[0]["applied_noise_power_db"], "-34")

    def test_tampered_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "plan.csv"
            altered.write_text(PLAN.read_text().replace("45003", "45004"))
            with self.assertRaisesRegex(MODULE.ValidationError, "checksum mismatch"):
                MODULE.load_staircase_plan(altered)

    def test_runtime_identity_arguments_are_mandatory(self) -> None:
        actions = {action.dest: action.required for action in MODULE.parser()._actions}
        for name in (
            "expected_debug_image_id",
            "expected_profile_revision",
            "expected_runner_sha256",
            "expected_plan_sha256",
            "expected_compose_sha256",
            "expected_channel_config_sha256",
            "expected_ue_config_sha256",
        ):
            self.assertTrue(actions[name])


if __name__ == "__main__":
    unittest.main()
