from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "bin" / "run-rfsim-noise-validation.py"
SPEC = importlib.util.spec_from_file_location("rfsim_noise_validation", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunRfsimNoiseValidationTest(unittest.TestCase):
    def test_protocol_order_and_execution_count_are_frozen(self) -> None:
        self.assertEqual(
            MODULE.STATE_ORDER,
            (
                (-60.0, -30.0, -20.0, -40.0, -25.0),
                (-25.0, -60.0, -40.0, -20.0, -30.0),
                (-20.0, -40.0, -25.0, -30.0, -60.0),
            ),
        )
        self.assertEqual(len(MODULE.EXECUTION_PLAN), 15)
        observed = [row[2] for row in MODULE.EXECUTION_PLAN]
        for state in (-60.0, -40.0, -30.0, -25.0, -20.0):
            self.assertEqual(observed.count(state), 3)
        self.assertEqual(len({row[3] for row in MODULE.EXECUTION_PLAN}), 15)

    def test_derived_config_changes_only_minus30_noise_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.conf"
            destination = root / "derived.conf"
            source.write_text(
                "ploss_dB = 0;\n"
                "noise_power_dB = -30;\n"
                "noise_power_dB = -30;\n"
            )
            substitutions = MODULE.derive_attach_config(source, destination)
            self.assertEqual(substitutions, 2)
            self.assertEqual(destination.read_text().count("-60"), 2)
            self.assertIn("ploss_dB = 0;", destination.read_text())

    def test_override_pins_image_seed_and_attach_config(self) -> None:
        text = MODULE.override_text("debug:image", 41007, Path("/tmp/attach.conf"))
        self.assertIn("image: debug:image", text)
        self.assertIn('OAI_RNGSEED: "41007"', text)
        self.assertIn(
            "/tmp/attach.conf:/opt/oai-nr-ue/etc/channelmod_rfsimu.conf:ro", text
        )

    def test_telemetry_join_requires_commanded_noise_and_seed(self) -> None:
        logs = (
            "UE_RADIO_DEBUG_V1 utc_second=103 rsrp_digital_power_linear=10 "
            "rsrp_db_per_re_unquantized=20 ss_rsrp_dbm_integer=-35 ss_sinr_db=18\n"
            "RFSIM_CHANNEL_DEBUG_V1 utc_second=103 model=rfsimu_channel_enB0 "
            "channel_snapshot_id=static-0 channel_snapshot_timestamp_ns=100 "
            "tap_energy_linear=1 tap_fingerprint_fnv1a64=abc channel_length=1 "
            "nb_taps=1 nb_tx=1 nb_rx=1 oai_rng_seed=41001 "
            "applied_gain_db=0 noise_power_db=-60\n"
        )
        rows, diagnostics = MODULE.build_telemetry_rows(
            "r1-p1-n60", 1, 1, 41001, -60.0, logs, 103.0, 104.0
        )
        self.assertEqual(diagnostics["matched_usable_rows"], 1)
        self.assertEqual(rows[0]["applied_noise_power_db"], "-60")
        self.assertEqual(rows[0]["channel_family"], "AWGN")

    def test_runtime_identity_arguments_are_mandatory(self) -> None:
        actions = {action.dest: action.required for action in MODULE.parser()._actions}
        for name in (
            "expected_debug_image_id",
            "expected_profile_revision",
            "expected_runner_sha256",
            "expected_compose_sha256",
            "expected_channel_config_sha256",
            "expected_ue_config_sha256",
        ):
            self.assertTrue(actions[name])


if __name__ == "__main__":
    unittest.main()
