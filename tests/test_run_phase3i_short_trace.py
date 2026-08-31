from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "bin" / "run-phase3i-short-trace.py"
COMMANDS = REPOSITORY / "etc" / "phase3i-short-trace-commands.csv"
SPEC = importlib.util.spec_from_file_location("phase3i_short_trace", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunPhase3iShortTraceTest(unittest.TestCase):
    def test_frozen_commands_are_complete_bounded_and_unclipped(self) -> None:
        commands = MODULE.load_commands(COMMANDS)
        self.assertEqual(len(commands), 60)
        self.assertEqual([row["command_index"] for row in commands], list(range(60)))
        self.assertEqual([row["trace_row_index"] for row in commands], list(range(154, 214)))
        self.assertFalse(any(row["clipped"] for row in commands))
        self.assertTrue(all(-18 <= row["commanded_gain_db"] <= 0 for row in commands))
        self.assertTrue(
            all(-35 <= row["commanded_noise_power_db"] <= -17 for row in commands)
        )

    def test_command_checksum_matches_protocol(self) -> None:
        self.assertEqual(MODULE.SUPPORT.sha256(COMMANDS), MODULE.EXPECTED_COMMANDS_SHA256)

    def test_tampered_commands_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "commands.csv"
            altered.write_text(COMMANDS.read_text().replace("-8.772537277", "-8.7", 1))
            with self.assertRaisesRegex(MODULE.ValidationError, "checksum mismatch"):
                MODULE.load_commands(altered)

    def test_runtime_identity_arguments_are_mandatory(self) -> None:
        actions = {action.dest: action.required for action in MODULE.parser()._actions}
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

    def test_command_timing_is_one_hertz_and_midpoint_bounded(self) -> None:
        self.assertEqual(MODULE.COMMAND_INTERVAL_SECONDS, 1.0)
        self.assertLessEqual(MODULE.MAXIMUM_COMMAND_COMPLETION_LATENESS_SECONDS, 0.5)
        self.assertEqual(MODULE.ANCHOR_SETTLING_SECONDS, 5.0)

    def test_trace_sample_second_is_the_scheduled_second(self) -> None:
        self.assertEqual(MODULE.math.floor(1_700_000_000.75), 1_700_000_000)


if __name__ == "__main__":
    unittest.main()
