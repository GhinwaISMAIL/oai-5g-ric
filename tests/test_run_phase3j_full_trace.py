from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "bin" / "run-phase3j-full-trace.py"
COMMANDS = REPOSITORY / "etc" / "phase3j-full-trace-commands.csv"
SPEC = importlib.util.spec_from_file_location("phase3j_full_trace", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunPhase3jFullTraceTest(unittest.TestCase):
    def test_frozen_commands_are_complete_bounded_and_supported(self) -> None:
        commands = MODULE.load_commands(COMMANDS)
        self.assertEqual(len(commands), 305)
        self.assertEqual([row["command_index"] for row in commands], list(range(305)))
        self.assertEqual(
            tuple(row["command_index"] for row in commands if row["clipped"]),
            MODULE.EXPECTED_CLIPPED_INDICES,
        )
        self.assertLessEqual(
            max(row["clipping_distance_scaled"] for row in commands), 0.75
        )
        self.assertTrue(all(-18 <= row["commanded_gain_db"] <= 0 for row in commands))
        self.assertTrue(
            all(-35 <= row["commanded_noise_power_db"] <= -17 for row in commands)
        )

    def test_command_checksum_matches_frozen_package(self) -> None:
        self.assertEqual(MODULE.SUPPORT.sha256(COMMANDS), MODULE.EXPECTED_COMMANDS_SHA256)

    def test_tampered_commands_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "commands.csv"
            altered.write_text(COMMANDS.read_text().replace("-10.0", "-10.1", 1))
            with self.assertRaisesRegex(MODULE.ValidationError, "checksum mismatch"):
                MODULE.load_commands(altered)

    def test_execution_number_selects_frozen_seed_and_limits(self) -> None:
        for execution_number, expected_seed in MODULE.EXECUTION_SEEDS.items():
            self.assertEqual(
                MODULE.configure_execution(execution_number), expected_seed
            )
            self.assertEqual(MODULE.PHASE3I.OAI_RNG_SEED, expected_seed)
            self.assertEqual(MODULE.PHASE3I.MINIMUM_TRACE_ROWS, 299)
            self.assertEqual(MODULE.PHASE3I.PING_INTERVAL_COMMANDS, 25)
        with self.assertRaisesRegex(MODULE.ValidationError, "1, 2, or 3"):
            MODULE.configure_execution(4)

    def test_output_normalization_records_development_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "phase3i-ue.log").write_text("ue\n")
            (output / "phase3i_short_trace_telemetry.csv").write_text("a\n")
            (output / "execution_state.json").write_text(
                json.dumps(
                    {
                        "stage": "phase_3i_representative_short_trace_replay",
                        "target_rows": 60,
                        "execution_completed": True,
                    }
                )
            )
            MODULE.normalize_output(output, 2, 47002)
            state = json.loads((output / "execution_state.json").read_text())
            self.assertFalse((output / "phase3i-ue.log").exists())
            self.assertTrue((output / "phase3j-ue.log").is_file())
            self.assertTrue((output / "phase3j_full_trace_telemetry.csv").is_file())
            self.assertEqual(
                state["stage"],
                "phase_3j_complete_test1_development_fidelity_and_repeatability",
            )
            self.assertEqual(state["evaluation_status"], "development_not_independent_final_validation")
            self.assertEqual(state["execution_number"], 2)
            self.assertEqual(state["oai_rng_seed"], 47002)
            self.assertEqual(state["target_rows"], 305)
            self.assertFalse(state["test6_accessed"])
            self.assertFalse(state["translator_update_authorized"])

    def test_runtime_identity_arguments_remain_mandatory(self) -> None:
        actions = {action.dest: action.required for action in MODULE.parser()._actions}
        self.assertTrue(actions["execution_number"])
        for name in (
            "expected_debug_image_id",
            "expected_profile_revision",
            "expected_runner_sha256",
            "expected_commands_sha256",
            "expected_compose_sha256",
            "expected_channel_config_sha256",
            "expected_ue_config_sha256",
        ):
            self.assertTrue(actions[name])


if __name__ == "__main__":
    unittest.main()
