#!/usr/bin/env python3
"""Fail-closed parser for the Phase 3C time-varying replay logs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

UE_REQUIRED = {
    "pbch_initial_success": re.compile(r"Initial sync: pbch decoded sucessfully"),
    "random_access_success": re.compile(r"4-Step RA procedure succeeded"),
    "rrc_connected": re.compile(r"State = NR_RRC_CONNECTED"),
    "registration_accept": re.compile(r"Received Registration Accept with result"),
}
GNB_REQUIRED = {
    "pusch_failure_threshold_configured": re.compile(
        r"PUSCH Target .* RSSI thresh .* Failure"
    ),
}
UE_FAILURES = {
    "pbch_decode_error": re.compile(r"Error decoding PBCH!", re.IGNORECASE),
    "random_access_failure": re.compile(r"RA (?:Procedure|procedure) failed"),
    "radio_link_failure": re.compile(r"RLF detected|radio link failure", re.IGNORECASE),
    "lost_sync": re.compile(r"LOST SYNC|out of sync", re.IGNORECASE),
}
GNB_FAILURES = {
    "pusch_ul_failure": re.compile(r"Detected UL Failure on PUSCH after"),
    "random_access_failure": re.compile(r"RA (?:Procedure|procedure) failed"),
    "rlc_max_retx": re.compile(r"max RETX reached", re.IGNORECASE),
    "unhandled_rlf_indication": re.compile(
        r"RLF detected, but no callable RLF handler registered", re.IGNORECASE
    ),
    "radio_link_failure": re.compile(
        r"RLF detected(?!,\s*but no callable RLF handler registered)|radio link failure",
        re.IGNORECASE,
    ),
}
ULSCH_DTX_COUNTER = re.compile(r"ulsch_DTX\s+(\d+)")


class LogParserError(RuntimeError):
    pass


def _counts(text: str, patterns: dict[str, re.Pattern[str]]) -> dict[str, int]:
    return {name: len(pattern.findall(text)) for name, pattern in patterns.items()}


def parse_replay_logs(
    ue_text: str,
    gnb_text: str,
    *,
    ue_failure_text: str | None = None,
    gnb_failure_text: str | None = None,
) -> dict[str, Any]:
    failure_ue_text = ue_text if ue_failure_text is None else ue_failure_text
    failure_gnb_text = gnb_text if gnb_failure_text is None else gnb_failure_text
    ue_required = _counts(ue_text, UE_REQUIRED)
    gnb_required = _counts(gnb_text, GNB_REQUIRED)
    ue_failures = _counts(failure_ue_text, UE_FAILURES)
    gnb_failures = _counts(failure_gnb_text, GNB_FAILURES)
    dtx_values = [
        int(value) for value in ULSCH_DTX_COUNTER.findall(failure_gnb_text)
    ]
    gates = {
        **{f"ue_required_{name}": count >= 1 for name, count in ue_required.items()},
        **{f"gnb_required_{name}": count >= 1 for name, count in gnb_required.items()},
        **{f"ue_zero_{name}": count == 0 for name, count in ue_failures.items()},
        **{f"gnb_zero_{name}": count == 0 for name, count in gnb_failures.items()},
    }
    gates = {name: bool(value) for name, value in gates.items()}
    return {
        "schema_version": 2,
        "failure_window": (
            "full_log"
            if ue_failure_text is None and gnb_failure_text is None
            else "provided_observation_suffix"
        ),
        "required_marker_counts": {"ue": ue_required, "gnb": gnb_required},
        "failure_marker_counts": {"ue": ue_failures, "gnb": gnb_failures},
        "reported_ulsch_dtx_counter_max": max(dtx_values) if dtx_values else None,
        "reported_ulsch_dtx_counter_observations": len(dtx_values),
        "gate_results": gates,
        "log_gate_pass": all(gates.values()),
        "interpretation": (
            "Critical PUSCH and actual RLF markers are gated at zero. The gNB's "
            "unhandled RLF indication is classified separately from an actual handled "
            "RLF, but it and its RLC max-retransmission cause remain zero-tolerance "
            "safety failures. Individual DTX counters remain diagnostic unless the "
            "configured consecutive-DTX threshold triggers the critical marker."
        ),
    }


def parse_files(ue_log: Path, gnb_log: Path) -> dict[str, Any]:
    for path in (ue_log, gnb_log):
        if not path.is_file() or path.is_symlink():
            raise LogParserError(f"missing or unsafe log fixture: {path}")
    return parse_replay_logs(
        ue_log.read_text(errors="replace"),
        gnb_log.read_text(errors="replace"),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ue-log", type=Path, required=True)
    parser.add_argument("--gnb-log", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = parse_files(args.ue_log, args.gnb_log)
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        if args.output.exists():
            raise LogParserError(f"output already exists: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized, end="")
    if not result["log_gate_pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
