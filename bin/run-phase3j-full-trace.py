#!/usr/bin/env python3
"""Run one frozen Phase 3J complete Test 1 replay execution."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PHASE3I_SCRIPT = Path(__file__).with_name("run-phase3i-short-trace.py")
PHASE3I_SPEC = importlib.util.spec_from_file_location(
    "phase3j_phase3i_support", PHASE3I_SCRIPT
)
if PHASE3I_SPEC is None or PHASE3I_SPEC.loader is None:
    raise RuntimeError(f"cannot load Phase 3I support: {PHASE3I_SCRIPT}")
PHASE3I = importlib.util.module_from_spec(PHASE3I_SPEC)
PHASE3I_SPEC.loader.exec_module(PHASE3I)
SUPPORT = PHASE3I.SUPPORT
BASE = PHASE3I.BASE
ValidationError = PHASE3I.ValidationError

RESEARCH_REVISION = "79debf091e75ddfa7ee398bd89ce03e97c493411"
RESEARCH_PROTOCOL_SHA256 = (
    "2471fa5b7614cd681b45f9ab9fec000c916606d2386deae41080c8fb0bd861c1"
)
EXPECTED_COMMANDS_SHA256 = (
    "c25fb55ea78294e7e7f44a9dddf6b985ba50881d09d6f394fbbc96bd074fcc76"
)
EXECUTION_SEEDS = {1: 47001, 2: 47002, 3: 47003}
TARGET_ROWS = 305
MINIMUM_TRACE_ROWS = 299
EXPECTED_CLIPPED_INDICES = (72, 101, 102, 107, 124, 125, 139, 141)
OUTPUT_PATH: Path | None = None


def load_commands(path: Path) -> list[dict[str, Any]]:
    SUPPORT.require_hash(path, EXPECTED_COMMANDS_SHA256)
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != PHASE3I.COMMAND_FIELDS:
            raise ValidationError(f"unexpected command columns: {reader.fieldnames}")
        raw = list(reader)
    rows: list[dict[str, Any]] = []
    for row_number, row in enumerate(raw, start=1):
        try:
            parsed = {
                "command_index": int(row["command_index"]),
                "trace_row_index": int(row["trace_row_index"]),
                "trace_time_bin": int(row["trace_time_bin"]),
                "trace_t_s": float(row["trace_t_s"]),
                "target_relative_rsrp_db": float(row["target_relative_rsrp_db"]),
                "target_sinr_db": float(row["target_sinr_db"]),
                "projected_relative_rsrp_db": float(
                    row["projected_relative_rsrp_db"]
                ),
                "projected_sinr_db": float(row["projected_sinr_db"]),
                "commanded_gain_db": float(row["commanded_gain_db"]),
                "commanded_noise_power_db": float(row["commanded_noise_power_db"]),
                "clipped": row["clipped"].strip().lower() == "true",
                "clipping_distance_scaled": float(row["clipping_distance_scaled"]),
                "triangle_index": int(row["triangle_index"]),
                "vertex_0": int(row["vertex_0"]),
                "vertex_1": int(row["vertex_1"]),
                "vertex_2": int(row["vertex_2"]),
                "barycentric_0": float(row["barycentric_0"]),
                "barycentric_1": float(row["barycentric_1"]),
                "barycentric_2": float(row["barycentric_2"]),
            }
        except (TypeError, ValueError) as error:
            raise ValidationError(f"invalid command row {row_number}: {row}") from error
        numeric = [value for key, value in parsed.items() if key != "clipped"]
        if not all(math.isfinite(float(value)) for value in numeric):
            raise ValidationError(f"non-finite command row {row_number}")
        rows.append(parsed)
    if len(rows) != TARGET_ROWS:
        raise ValidationError(f"the Phase 3J command trace must contain {TARGET_ROWS} rows")
    if [row["command_index"] for row in rows] != list(range(TARGET_ROWS)):
        raise ValidationError("the Phase 3J command indices changed")
    if [row["trace_row_index"] for row in rows] != list(range(TARGET_ROWS)):
        raise ValidationError("the complete Test 1 trace row order changed")
    clipped = tuple(row["command_index"] for row in rows if row["clipped"])
    if clipped != EXPECTED_CLIPPED_INDICES:
        raise ValidationError(f"the frozen clipped command indices changed: {clipped}")
    if max(row["clipping_distance_scaled"] for row in rows) > 0.75:
        raise ValidationError("the complete trace exceeds the development support gate")
    if any(
        not -18.0 <= row["commanded_gain_db"] <= 0.0
        or not -35.0 <= row["commanded_noise_power_db"] <= -17.0
        for row in rows
    ):
        raise ValidationError("a command exceeds the validated control envelope")
    return rows


def make_output(root: Path) -> Path:
    global OUTPUT_PATH
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output = root / f"phase3j-full-trace-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    OUTPUT_PATH = output
    return output


def configure_execution(execution_number: int) -> int:
    if execution_number not in EXECUTION_SEEDS:
        raise ValidationError("execution number must be 1, 2, or 3")
    seed = EXECUTION_SEEDS[execution_number]
    PHASE3I.RESEARCH_REVISION = RESEARCH_REVISION
    PHASE3I.RESEARCH_PROTOCOL_SHA256 = RESEARCH_PROTOCOL_SHA256
    PHASE3I.EXPECTED_COMMANDS_SHA256 = EXPECTED_COMMANDS_SHA256
    PHASE3I.OAI_RNG_SEED = seed
    PHASE3I.MINIMUM_TRACE_ROWS = MINIMUM_TRACE_ROWS
    PHASE3I.PING_INTERVAL_COMMANDS = 25
    PHASE3I.load_commands = load_commands
    PHASE3I.make_output = make_output
    PHASE3I.__file__ = __file__
    return seed


def normalize_output(output: Path, execution_number: int, seed: int) -> None:
    renames = {
        "phase3i-ue.log": "phase3j-ue.log",
        "phase3i-gnb.log": "phase3j-gnb.log",
        "phase3i-command-events.json": "phase3j-command-events.json",
        "phase3i-anchor-windows.json": "phase3j-anchor-windows.json",
        "phase3i-ping-checks.json": "phase3j-ping-checks.json",
        "phase3i-log-analysis.json": "phase3j-log-analysis.json",
        "phase3i_short_trace_telemetry.csv": "phase3j_full_trace_telemetry.csv",
        "phase3i_anchor_telemetry.csv": "phase3j_anchor_telemetry.csv",
    }
    for source_name, destination_name in renames.items():
        source = output / source_name
        if source.exists():
            source.rename(output / destination_name)
    state_path = output / "execution_state.json"
    if not state_path.is_file():
        return
    state = json.loads(state_path.read_text())
    state.update(
        {
            "stage": "phase_3j_complete_test1_development_fidelity_and_repeatability",
            "evaluation_status": "development_not_independent_final_validation",
            "execution_number": execution_number,
            "execution_id": f"phase3j-test1-execution-{execution_number}",
            "oai_rng_seed": seed,
            "target_rows": TARGET_ROWS,
            "minimum_paired_rows": MINIMUM_TRACE_ROWS,
            "clipped_command_rows": len(EXPECTED_CLIPPED_INDICES),
            "primary_kpi_alignment_seconds": 0,
            "channel_verification_alignment_seconds": 1,
            "commands_adapted_during_execution": False,
            "translator_update_authorized": False,
            "test6_accessed": False,
            "final_test6_accessed": False,
            "independent_final_validation": False,
        }
    )
    BASE.write_json(state_path, state)


def parser() -> argparse.ArgumentParser:
    root = PHASE3I.parser()
    root.description = "Run one frozen Phase 3J complete Test 1 replay execution."
    root.add_argument("--execution-number", type=int, choices=(1, 2, 3), required=True)
    root.set_defaults(
        commands="/local/repository/etc/phase3j-full-trace-commands.csv",
        override_file="/local/upv-phase3j-full-trace-v1/ue.override.yaml",
        output_root="/local/logs/upv-phase3j-full-trace-v1",
    )
    return root


def execute(args: argparse.Namespace) -> int:
    global OUTPUT_PATH
    OUTPUT_PATH = None
    seed = configure_execution(args.execution_number)
    result = PHASE3I.execute(args)
    if OUTPUT_PATH is None:
        raise ValidationError("the Phase 3J output directory was not created")
    normalize_output(OUTPUT_PATH, args.execution_number, seed)
    print(f"PHASE3J_OUTPUT={OUTPUT_PATH}", flush=True)
    return result


def main() -> int:
    BASE.install_signal_handlers()
    try:
        return execute(parser().parse_args())
    except (OSError, ValidationError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
