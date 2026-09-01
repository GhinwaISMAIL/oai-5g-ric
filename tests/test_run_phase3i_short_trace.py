from __future__ import annotations

import importlib.util
import math
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

    def test_control_echo_tolerance_covers_float32_round_trip_only(self) -> None:
        self.assertEqual(MODULE.CONTROL_ECHO_ABS_TOL_DB, 5e-6)
        self.assertTrue(
            math.isclose(
                -34.036278,
                -34.036279572,
                rel_tol=0.0,
                abs_tol=MODULE.CONTROL_ECHO_ABS_TOL_DB,
            )
        )
        self.assertFalse(
            math.isclose(
                -34.03627,
                -34.036279572,
                rel_tol=0.0,
                abs_tol=MODULE.CONTROL_ECHO_ABS_TOL_DB,
            )
        )

    def test_persistent_channel_session_is_used_for_the_timed_trace(self) -> None:
        source = SCRIPT.read_text()
        self.assertIn(
            "with PersistentChannelSession(channel_helper) as channel_session", source
        )
        self.assertIn("channel_rows.get(utc_second + 1)", source)
        self.assertTrue(math.isclose(MODULE.COMMAND_INTERVAL_SECONDS, 1.0))

    def test_persistent_channel_session_sets_and_verifies_both_controls(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self.sent: list[bytes] = []
                self.responses = [
                    b"modified\nsoftmodem_5Gue> ",
                    b"modified\nsoftmodem_5Gue> ",
                    b"show output\nsoftmodem_5Gue> ",
                ]

            def sendall(self, value: bytes) -> None:
                self.sent.append(value)

            def settimeout(self, _value: float) -> None:
                pass

            def recv(self, _size: int) -> bytes:
                return self.responses.pop(0)

        class FakeHelper:
            @staticmethod
            def model_identity(_output: str, _index: int) -> tuple[str, str]:
                return "rfsimu_channel_enB0", "AWGN"

            @staticmethod
            def observed_value(_output: str, _index: int, parameter: str) -> float:
                return -8.5 if parameter == "ploss" else -34.036278

        session = object.__new__(MODULE.PersistentChannelSession)
        session.helper = FakeHelper()
        session.index = 0
        session.sock = FakeSocket()
        result = session.set_controls(-8.5, -34.036279572)
        self.assertEqual(
            session.sock.sent,
            [
                b"channelmod modify 0 ploss -8.5\n",
                b"channelmod modify 0 noise_power_dB -34.036279572\n",
                b"channelmod show current\n",
            ],
        )
        self.assertTrue(result["gain"]["verified"])
        self.assertTrue(result["noise"]["verified"])

    def test_persistent_channel_session_rejects_material_control_mismatch(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self.responses = [
                    b"modified\nsoftmodem_5Gue> ",
                    b"modified\nsoftmodem_5Gue> ",
                    b"show output\nsoftmodem_5Gue> ",
                ]

            def sendall(self, _value: bytes) -> None:
                pass

            def settimeout(self, _value: float) -> None:
                pass

            def recv(self, _size: int) -> bytes:
                return self.responses.pop(0)

        class FakeHelper:
            @staticmethod
            def model_identity(_output: str, _index: int) -> tuple[str, str]:
                return "rfsimu_channel_enB0", "AWGN"

            @staticmethod
            def observed_value(_output: str, _index: int, parameter: str) -> float:
                return -8.5 if parameter == "ploss" else -34.03627

        session = object.__new__(MODULE.PersistentChannelSession)
        session.helper = FakeHelper()
        session.index = 0
        session.sock = FakeSocket()
        with self.assertRaisesRegex(MODULE.ValidationError, "noise verification failed"):
            session.set_controls(-8.5, -34.036279572)

    def test_trace_uses_immediate_ack_when_async_snapshot_has_next_command(self) -> None:
        event = {
            "command_index": 103,
            "trace_row_index": 103,
            "trace_time_bin": 103,
            "trace_t_s": 103.0,
            "target_relative_rsrp_db": -3.0,
            "target_sinr_db": 18.0,
            "projected_relative_rsrp_db": -3.0,
            "projected_sinr_db": 18.0,
            "commanded_gain_db": -13.542553956,
            "commanded_noise_power_db": -28.280106729,
            "clipped": False,
            "scheduled_epoch": 1000.0,
            "command_complete_epoch": 1000.12,
            "command_completion_lateness_seconds": 0.12,
            "sample_utc_second": 1000,
            "gain_result": {
                "model_index": 0,
                "model_name": "rfsimu_channel_enB0",
                "model_type": "AWGN",
                "parameter": "ploss",
                "requested": -13.542553956,
                "observed": -13.542554,
                "verified": True,
                "applied_epoch": 1000.12,
            },
            "noise_result": {
                "model_index": 0,
                "model_name": "rfsimu_channel_enB0",
                "model_type": "AWGN",
                "parameter": "noise_power_dB",
                "requested": -28.280106729,
                "observed": -28.280107,
                "verified": True,
                "applied_epoch": 1000.12,
            },
        }
        logs = (
            "UE_RADIO_DEBUG_V1 utc_second=1000 emitted_epoch_us=1001001000 "
            "rsrp_digital_power_linear=10 rsrp_db_per_re_unquantized=37 "
            "ss_rsrp_dbm_integer=-40 ss_sinr_db=20\n"
            "RFSIM_CHANNEL_DEBUG_V1 utc_second=1001 emitted_epoch_us=1001002000 "
            "model=rfsimu_channel_enB0 channel_snapshot_id=static-0 "
            "channel_snapshot_timestamp_ns=100 tap_energy_linear=1 "
            "tap_fingerprint_fnv1a64=abc channel_length=1 nb_taps=1 nb_tx=1 "
            f"nb_rx=1 oai_rng_seed={MODULE.OAI_RNG_SEED} "
            "applied_gain_db=-8.400771832 "
            "noise_power_db=-31.069929123\n"
        )
        rows = MODULE.build_trace_telemetry([event], logs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["applied_gain_db"], -13.542554)
        self.assertEqual(rows[0]["applied_noise_power_db"], -28.280107)
        self.assertEqual(rows[0]["channel_snapshot_applied_gain_db"], "-8.400771832")
        self.assertEqual(rows[0]["channel_snapshot_noise_power_db"], "-31.069929123")


if __name__ == "__main__":
    unittest.main()
