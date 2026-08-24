from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "phase3c_log_parser.py"
SPEC = importlib.util.spec_from_file_location("phase3c_log_parser", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
FIXTURES = ROOT / "tests" / "fixtures"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_healthy_fixture_passes_and_reports_noncritical_dtx() -> None:
    result = MODULE.parse_replay_logs(
        _text("phase3c_ue_healthy.log"),
        _text("phase3c_gnb_healthy.log"),
    )

    assert result["log_gate_pass"] is True
    assert result["reported_ulsch_dtx_counter_max"] == 2
    assert result["failure_marker_counts"]["gnb"]["pusch_ul_failure"] == 0


def test_ue_failure_fixture_fails_closed() -> None:
    result = MODULE.parse_replay_logs(
        _text("phase3c_ue_failure.log"),
        _text("phase3c_gnb_healthy.log"),
    )

    assert result["log_gate_pass"] is False
    assert result["gate_results"]["ue_zero_pbch_decode_error"] is False
    assert result["gate_results"]["ue_zero_radio_link_failure"] is False


def test_gnb_pusch_failure_fixture_fails_closed() -> None:
    result = MODULE.parse_replay_logs(
        _text("phase3c_ue_healthy.log"),
        _text("phase3c_gnb_failure.log"),
    )

    assert result["log_gate_pass"] is False
    assert result["gate_results"]["gnb_zero_pusch_ul_failure"] is False


def test_missing_required_marker_fails_closed() -> None:
    result = MODULE.parse_replay_logs(
        "[PHY] Initial sync: pbch decoded sucessfully, ssb index 0\n",
        _text("phase3c_gnb_healthy.log"),
    )

    assert result["log_gate_pass"] is False
    assert result["gate_results"]["ue_required_registration_accept"] is False


def test_unhandled_gnb_rlf_warning_is_classified_but_still_fails() -> None:
    gnb = _text("phase3c_gnb_healthy.log") + (
        "\n[RLC] max RETX reached on SRB 1"
        "\n[RLC] UE 1234: RLF detected, but no callable RLF handler registered\n"
    )
    result = MODULE.parse_replay_logs(_text("phase3c_ue_healthy.log"), gnb)

    assert result["schema_version"] == 2
    assert result["failure_marker_counts"]["gnb"]["rlc_max_retx"] == 1
    assert result["failure_marker_counts"]["gnb"]["unhandled_rlf_indication"] == 1
    assert result["failure_marker_counts"]["gnb"]["radio_link_failure"] == 0
    assert result["log_gate_pass"] is False
