from __future__ import annotations

import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "bin" / "run-phase3g-bounded-response.py"
PLAN = REPOSITORY / "etc" / "phase3g-bounded-response-plan.csv"
SPEC = importlib.util.spec_from_file_location("phase3g_bounded_response", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunPhase3gBoundedResponseTest(unittest.TestCase):
    def test_frozen_plan_has_required_stages_repetitions_and_seeds(self) -> None:
        plan = MODULE.load_execution_plan(PLAN)
        self.assertEqual(len(plan), 45)
        self.assertEqual(
            Counter(row["stage"] for row in plan),
            {
                "gain_safety": 4,
                "noise_safety": 2,
                "factorial": 27,
                "boundary": 12,
            },
        )
        self.assertEqual(len({row["oai_rng_seed"] for row in plan}), 45)
        self.assertEqual(
            [row["stage"] for row in plan[:6]],
            ["gain_safety"] * 4 + ["noise_safety"] * 2,
        )

    def test_plan_checksum_matches_research_protocol(self) -> None:
        self.assertEqual(MODULE.SUPPORT.sha256(PLAN), MODULE.EXPECTED_PLAN_SHA256)

    def test_telemetry_join_records_both_controls(self) -> None:
        plan_row = {
            "execution_index": 7,
            "stage": "factorial",
            "stage_position": 1,
            "repetition": 1,
            "gain_db": -8.0,
            "noise_power_db": -25.0,
            "oai_rng_seed": 44007,
        }
        logs = (
            "UE_RADIO_DEBUG_V1 utc_second=103 rsrp_digital_power_linear=10 "
            "rsrp_db_per_re_unquantized=20 ss_rsrp_dbm_integer=-35 ss_sinr_db=18\n"
            "RFSIM_CHANNEL_DEBUG_V1 utc_second=103 model=rfsimu_channel_enB0 "
            "channel_snapshot_id=static-0 channel_snapshot_timestamp_ns=100 "
            "tap_energy_linear=1 tap_fingerprint_fnv1a64=abc channel_length=1 "
            "nb_taps=1 nb_tx=1 nb_rx=1 oai_rng_seed=44007 "
            "applied_gain_db=-8 noise_power_db=-25\n"
        )
        rows, diagnostics = MODULE.build_telemetry_rows(
            plan_row, "test", logs, 103.0, 104.0
        )
        self.assertEqual(diagnostics["matched_usable_rows"], 1)
        self.assertEqual(rows[0]["commanded_gain_db"], -8.0)
        self.assertEqual(rows[0]["applied_gain_db"], "-8")
        self.assertEqual(rows[0]["commanded_noise_power_db"], -25.0)
        self.assertEqual(rows[0]["applied_noise_power_db"], "-25")

    def test_tampered_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "plan.csv"
            altered.write_text(PLAN.read_text().replace("44045", "44046"))
            with self.assertRaisesRegex(MODULE.ValidationError, "checksum mismatch"):
                MODULE.load_execution_plan(altered)

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
