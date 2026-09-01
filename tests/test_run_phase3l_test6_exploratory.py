from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "bin" / "run-phase3l-test6-exploratory.py"
COMMANDS = REPOSITORY / "etc" / "phase3l-test6-exploratory-commands.csv"
SPEC = importlib.util.spec_from_file_location("phase3l_test6_exploratory", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunPhase3lTest6ExploratoryTest(unittest.TestCase):
    def test_frozen_commands_preserve_disclosed_projection(self) -> None:
        commands = MODULE.load_commands(COMMANDS)
        self.assertEqual(len(commands), 297)
        self.assertEqual([row["command_index"] for row in commands], list(range(297)))
        self.assertEqual(
            tuple(row["command_index"] for row in commands if row["clipped"]),
            MODULE.EXPECTED_CLIPPED_INDICES,
        )
        self.assertAlmostEqual(
            max(row["clipping_distance_scaled"] for row in commands),
            MODULE.EXPECTED_MAXIMUM_CLIPPING_DISTANCE_SCALED,
            places=11,
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
            altered.write_text(COMMANDS.read_text().replace("10.15", "10.16", 1))
            with self.assertRaisesRegex(MODULE.ValidationError, "checksum mismatch"):
                MODULE.load_commands(altered)

    def test_execution_uses_first_predeclared_test6_seed(self) -> None:
        self.assertEqual(MODULE.configure_execution(1), 48001)
        self.assertEqual(MODULE.PHASE3I.OAI_RNG_SEED, 48001)
        self.assertEqual(MODULE.PHASE3I.MINIMUM_TRACE_ROWS, 292)
        self.assertEqual(MODULE.PHASE3I.PING_INTERVAL_COMMANDS, 25)
        self.assertEqual(MODULE.PHASE3I.CONTROL_ECHO_ABS_TOL_DB, 5e-6)
        with self.assertRaisesRegex(MODULE.ValidationError, "must be 1"):
            MODULE.configure_execution(2)

    def test_output_normalization_preserves_exploratory_status(self) -> None:
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
            MODULE.normalize_output(output, 1, 48001)
            state = json.loads((output / "execution_state.json").read_text())
            self.assertFalse((output / "phase3i-ue.log").exists())
            self.assertTrue((output / "phase3l-ue.log").is_file())
            self.assertTrue((output / "phase3l_test6_telemetry.csv").is_file())
            self.assertEqual(state["stage"], "phase_3l_posthoc_test6_exploratory_replay")
            self.assertEqual(
                state["evaluation_status"],
                "posthoc_exploratory_not_confirmatory_validation",
            )
            self.assertEqual(state["target_rows"], 297)
            self.assertEqual(state["minimum_paired_rows"], 292)
            self.assertTrue(state["test6_accessed"])
            self.assertTrue(state["exploratory_replay"])
            self.assertFalse(state["frozen_v1_support_gate_passed"])
            self.assertFalse(state["confirmatory_support_pass_claimed"])
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
