from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "run-phase3c-vrtsim-replay.py"
sys.path.insert(0, str(ROOT / "bin"))
SPEC = importlib.util.spec_from_file_location("phase3c_vrtsim_replay", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _healthy_logs(seconds: int = 331) -> tuple[str, str]:
    ue_lines = [
        "Initial sync: pbch decoded sucessfully, ssb index 0",
        "4-Step RA procedure succeeded",
        "State = NR_RRC_CONNECTED",
        "Received Registration Accept with result 3GPP",
    ]
    ue_lines.extend("UE_RADIO_DEBUG_V1 value=1" for _ in range(seconds))
    gnb_lines = ["PUSCH Target 200 RSSI thresh 0 Failure 10"]
    for second in range(seconds):
        step = second * 500
        gnb_lines.append(
            "VRTSIM_CIRDB_DEBUG_V1 "
            f"elapsed_second={second} expected_cirdb_step={step} "
            f"current_cirdb_snapshot_index={step % 150000} "
            f"applied_cirdb_updates={step + 1} skipped_cirdb_snapshots=0 "
            "maximum_consecutive_skipped_cirdb_snapshots=0 "
            "current_tap_energy_linear=1.0"
        )
    return "\n".join(ue_lines), "\n".join(gnb_lines)


def test_override_is_one_gnb_one_ue_cirdb_and_shared_ipc(tmp_path: Path) -> None:
    override = MODULE.render_override(tmp_path / "trace", tmp_path / "shared")

    assert "oai-gnb:" in override
    assert "oai-nr-ue1:" in override
    assert override.count("ipc: host") == 2
    assert "--device.name vrtsim" in override
    assert "--vrtsim.cirdb 1" in override
    assert "--vrtsim.cirdb_model_id 1" in override
    assert "--rfsim" not in override


def test_passthrough_override_changes_only_channel_processing(tmp_path: Path) -> None:
    override = MODULE.render_override(
        tmp_path / "trace",
        tmp_path / "shared",
        channel_mode="passthrough",
        server_timescale=1.0,
        gnb_min_rxtxtime=6,
    )

    assert "--device.name vrtsim" in override
    assert override.count("ipc: host") == 2
    assert "--vrtsim.timescale 1" in override
    assert "--gNBs.[0].min_rxtxtime 6" in override
    assert "--vrtsim.cirdb" not in override
    assert "/cirdb" not in override


def test_cirdb_debug_parser_requires_every_field() -> None:
    _, gnb = _healthy_logs(2)
    rows = MODULE.parse_cirdb_debug(gnb)

    assert len(rows) == 2
    assert rows[1]["expected_cirdb_step"] == 500

    with pytest.raises(MODULE.ReplayError, match="incomplete"):
        MODULE.parse_cirdb_debug(
            "VRTSIM_CIRDB_DEBUG_V1 elapsed_second=0 expected_cirdb_step=0"
        )


def test_ping_parser_accepts_iputils_summary() -> None:
    result = MODULE.parse_ping_summary(
        "330 packets transmitted, 329 received, 0.30303% packet loss, time 330000ms"
    )

    assert result["packets_transmitted"] == 330
    assert result["packets_received"] == 329
    assert result["packet_loss_fraction"] == pytest.approx(0.0030303)


def test_replay_evaluator_accepts_complete_healthy_fixture() -> None:
    ue, gnb = _healthy_logs()
    checks = [{"attached": True, "epoch": index} for index in range(330)]
    result = MODULE.evaluate_replay(
        ue_log=ue,
        gnb_log=gnb,
        attachment_checks=checks,
        ping_output=(
            "330 packets transmitted, 329 received, 0.30303% packet loss, time 330000ms"
        ),
    )

    assert result["replay_pass"] is True
    assert result["trace_cycle_coverage_fraction"] == 1.0
    assert all(result["gate_results"].values())


def test_identity_control_uses_frozen_hashes_and_one_snapshot_cycle() -> None:
    profile = MODULE.TRACE_PROFILES["identity_l8_control"]
    ue, gnb = _healthy_logs(31)
    checks = [{"attached": True, "epoch": float(index)} for index in range(31)]
    result = MODULE.evaluate_replay(
        ue_log=ue,
        gnb_log=gnb,
        attachment_checks=checks,
        ping_output="30 packets transmitted, 30 received, 0% packet loss, time 30000ms",
        minimum_telemetry_seconds=20.0,
        trace_snapshots=profile["snapshots"],
    )

    assert profile == {
        "binary_sha256": "ae5140b4f95bf59256e1f82bc650f9391c3b06f5b7adf63bd92497a9c7c5bfc2",
        "sidecar_sha256": "51e3157cb5c6aaa00a06b5bd00680c0acc51fa4ae7a1694ba0b0d671063c3cba",
        "snapshots": 1,
    }
    assert result["replay_pass"] is True
    assert result["trace_cycle_coverage_fraction"] == 1.0


def test_replay_evaluator_rejects_pusch_failure() -> None:
    ue, gnb = _healthy_logs()
    gnb += "\nDetected UL Failure on PUSCH after 10 PUSCH DTX, stopping scheduling"
    checks = [{"attached": True, "epoch": index} for index in range(330)]
    result = MODULE.evaluate_replay(
        ue_log=ue,
        gnb_log=gnb,
        attachment_checks=checks,
        ping_output="330 packets transmitted, 330 received, 0% packet loss, time 330000ms",
    )

    assert result["replay_pass"] is False
    assert result["gate_results"]["log_markers"] is False


def test_replay_evaluator_rejects_snapshot_gap() -> None:
    ue, gnb = _healthy_logs()
    gnb = gnb.replace(
        "maximum_consecutive_skipped_cirdb_snapshots=0",
        "maximum_consecutive_skipped_cirdb_snapshots=11",
    )
    checks = [{"attached": True, "epoch": index} for index in range(330)]
    result = MODULE.evaluate_replay(
        ue_log=ue,
        gnb_log=gnb,
        attachment_checks=checks,
        ping_output="330 packets transmitted, 330 received, 0% packet loss, time 330000ms",
    )

    assert result["replay_pass"] is False
    assert result["gate_results"]["maximum_consecutive_skipped"] is False


def test_passthrough_evaluator_does_not_require_cirdb_rows() -> None:
    ue, _ = _healthy_logs(31)
    gnb = "PUSCH Target 200 RSSI thresh 0 Failure 10"
    checks = [
        {"attached": True, "epoch": float(index)} for index in range(31)
    ]
    result = MODULE.evaluate_replay(
        ue_log=ue,
        gnb_log=gnb,
        attachment_checks=checks,
        ping_output="30 packets transmitted, 30 received, 0% packet loss, time 30000ms",
        require_cirdb=False,
        minimum_telemetry_seconds=20.0,
    )

    assert result["replay_pass"] is True
    assert result["cirdb_debug_rows"] == 0
    assert "trace_cycle_coverage" not in result["gate_results"]


def test_passthrough_evaluator_records_startup_warning_but_gates_observation() -> None:
    ue, _ = _healthy_logs(31)
    startup_gnb = (
        "PUSCH Target 200 RSSI thresh 0 Failure 10"
        "\nmax RETX reached on SRB 1"
        "\nUE 1234: RLF detected, but no callable RLF handler registered"
    )
    checks = [{"attached": True, "epoch": float(index)} for index in range(31)]
    result = MODULE.evaluate_replay(
        ue_log=ue,
        gnb_log=startup_gnb,
        startup_ue_log=ue,
        startup_gnb_log=startup_gnb,
        observation_ue_log="\n".join(
            "UE_RADIO_DEBUG_V1 value=1" for _ in range(31)
        ),
        observation_gnb_log="clean observation window",
        attachment_checks=checks,
        ping_output="30 packets transmitted, 30 received, 0% packet loss, time 30000ms",
        require_cirdb=False,
        minimum_telemetry_seconds=20.0,
    )

    assert result["replay_pass"] is True
    assert result["log_parser"]["failure_window"] == "provided_observation_suffix"
    assert result["log_parser"]["log_gate_pass"] is True
    assert result["startup_log_diagnostics"]["log_gate_pass"] is False


def test_legacy_evaluator_labels_full_log_failure_window() -> None:
    ue, _ = _healthy_logs(31)
    result = MODULE.evaluate_replay(
        ue_log=ue,
        gnb_log="PUSCH Target 200 RSSI thresh 0 Failure 10",
        attachment_checks=[
            {"attached": True, "epoch": float(index)} for index in range(31)
        ],
        ping_output="30 packets transmitted, 30 received, 0% packet loss, time 30000ms",
        require_cirdb=False,
        minimum_telemetry_seconds=20.0,
    )

    assert result["log_parser"]["failure_window"] == "full_log"


def test_log_suffix_fails_closed_if_stream_is_not_append_only() -> None:
    assert MODULE._log_suffix("startup\nobservation", "startup\n", "UE") == (
        "observation"
    )
    with pytest.raises(MODULE.ReplayError, match="not append-only"):
        MODULE._log_suffix("rotated log", "startup log", "UE")


def test_attachment_failure_captures_logs_before_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MODULE, "compose", lambda *args: "")
    monkeypatch.setattr(MODULE, "wait_gnb_healthy", lambda timeout: None)

    def fail_attachment(timeout: float) -> None:
        raise MODULE.ReplayError(f"attachment failed after {timeout}")

    monkeypatch.setattr(MODULE, "wait_attached", fail_attachment)

    def fake_run_command(*args: str, check: bool = True) -> str:
        if args[-1] == MODULE.UE_CONTAINER:
            return "UE startup failure evidence"
        if args[-1] == MODULE.GNB_CONTAINER:
            return "gNB timing failure evidence"
        return ""

    monkeypatch.setattr(MODULE, "run_command", fake_run_command)
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(MODULE.ReplayError, match="attachment failed"):
        MODULE.run_one_replay(
            1,
            tmp_path / "compose.yaml",
            tmp_path / "override.yaml",
            tmp_path / "shared",
            output,
            180.0,
        )

    assert (output / "vrtsim-1-ue.log").read_text() == (
        "UE startup failure evidence\n"
    )
    assert (output / "vrtsim-1-gnb.log").read_text() == (
        "gNB timing failure evidence\n"
    )
    assert (output / "vrtsim-1-ping.log").read_text() == "\n"
    assert (output / "vrtsim-1-attachment.json").is_file()
