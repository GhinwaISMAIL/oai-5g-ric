from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "bin" / "run-phase3c-scalar-replay.py"
SPEC = importlib.util.spec_from_file_location("phase3c_scalar_replay", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunPhase3cScalarReplayTest(unittest.TestCase):
    def test_frozen_envelope_and_override_are_narrow(self) -> None:
        self.assertEqual(MODULE.ENVELOPE, (
            ("baseline", 0.0),
            ("descent", -2.0),
            ("nadir", -4.0),
            ("return", -2.0),
            ("recovery", 0.0),
        ))
        self.assertEqual(MODULE.REPLAYS, 2)
        self.assertEqual(
            MODULE.OVERRIDE_TEXT,
            "services:\n"
            "  oai-nr-ue1:\n"
            "    image: oai-nr-ue-phase3c-debug:70508eb\n",
        )

    def test_marker_parser_keeps_latest_row_per_second(self) -> None:
        logs = """
[PHY] UE_RADIO_DEBUG_V1 utc_second=10 rsrp_digital_power_linear=1
[PHY] UE_RADIO_DEBUG_V1 utc_second=10 rsrp_digital_power_linear=2
[HW] RFSIM_CHANNEL_DEBUG_V1 utc_second=10 applied_gain_db=-2
"""
        rows = MODULE.parse_marker_lines(logs, "UE_RADIO_DEBUG_V1")
        self.assertEqual(
            rows,
            {10: {"utc_second": "10", "rsrp_digital_power_linear": "2"}},
        )

    def test_build_telemetry_rows_joins_channel_and_ue_by_utc_second(self) -> None:
        logs = (
            "UE_RADIO_DEBUG_V1 utc_second=103 "
            "rsrp_digital_power_linear=10 "
            "rsrp_db_per_re_unquantized=20.25 "
            "ss_rsrp_dbm_integer=-35 ss_sinr_db=18.5\n"
            "RFSIM_CHANNEL_DEBUG_V1 utc_second=103 "
            "channel_snapshot_id=static-0 "
            "channel_snapshot_timestamp_ns=100 "
            "tap_energy_linear=1 applied_gain_db=-2 noise_power_db=-30\n"
        )
        segments = [
            {
                "nominal_start_s": 10.0,
                "applied_epoch": 100.0,
                "commanded_gain_db": -2.0,
            }
        ]
        checks = [{"epoch": 103.4, "attached": True}]
        rows, diagnostics = MODULE.build_telemetry_rows(
            "local-1", logs, segments, checks
        )
        self.assertEqual(diagnostics["matched_rows"], 1)
        self.assertEqual(rows[0]["replay_id"], "local-1")
        self.assertEqual(rows[0]["t_s"], "13.500000")
        self.assertEqual(rows[0]["commanded_gain_db"], "-2.000000000")
        self.assertEqual(rows[0]["channel_snapshot_id"], "static-0")
        self.assertEqual(rows[0]["attached"], "true")

    def test_nearest_attachment_rejects_stale_check(self) -> None:
        self.assertFalse(MODULE.nearest_attachment(
            20.0, [{"epoch": 17.0, "attached": True}]
        ))


if __name__ == "__main__":
    unittest.main()
