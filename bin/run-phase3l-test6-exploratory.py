#!/usr/bin/env python3
"""Run the frozen Phase 3L post hoc Test 6 exploratory replay."""

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
    "phase3l_phase3i_support", PHASE3I_SCRIPT
)
if PHASE3I_SPEC is None or PHASE3I_SPEC.loader is None:
    raise RuntimeError(f"cannot load Phase 3I support: {PHASE3I_SCRIPT}")
PHASE3I = importlib.util.module_from_spec(PHASE3I_SPEC)
PHASE3I_SPEC.loader.exec_module(PHASE3I)
SUPPORT = PHASE3I.SUPPORT
BASE = PHASE3I.BASE
ValidationError = PHASE3I.ValidationError

RESEARCH_REVISION = "173936baf3ecc3720620434f80e944df2b95dca1"
RESEARCH_PROTOCOL_SHA256 = (
    "be6dc98df9bd0985168a79dfc52665bb431039b78c58d838a065eb13264f3f85"
)
EXECUTION_PATCH_SHA256 = (
    "9da4e23cc031f00b580685f463dc31c37f5c939b0e02b2db59471f08429c666d"
)
EXPECTED_COMMANDS_SHA256 = (
    "f79d244e8c054a95ee895f83f0655dc6136b694c1c8f2db5417043a1cf2bbedc"
)
EXECUTION_SEEDS = {1: 48001}
TARGET_ROWS = 297
MINIMUM_TRACE_ROWS = 292
CONTROL_ECHO_ABS_TOL_DB = 5e-6
EXPECTED_CLIPPED_INDICES = (
    14,
    16,
    103,
    110,
    120,
    122,
    123,
    124,
    125,
    126,
    127,
    128,
    143,
    144,
    145,
    146,
    147,
    150,
    172,
    256,
    263,
)
EXPECTED_MAXIMUM_CLIPPING_DISTANCE_SCALED = 1.06265833759
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
        raise ValidationError(f"the Phase 3L command trace must contain {TARGET_ROWS} rows")
    if [row["command_index"] for row in rows] != list(range(TARGET_ROWS)):
        raise ValidationError("the Phase 3L command indices changed")
    if [row["trace_row_index"] for row in rows] != list(range(TARGET_ROWS)):
        raise ValidationError("the complete Test 6 trace row order changed")
    clipped = tuple(row["command_index"] for row in rows if row["clipped"])
    if clipped != EXPECTED_CLIPPED_INDICES:
        raise ValidationError(f"the frozen clipped command indices changed: {clipped}")
    maximum_distance = max(row["clipping_distance_scaled"] for row in rows)
    if not math.isclose(
        maximum_distance,
        EXPECTED_MAXIMUM_CLIPPING_DISTANCE_SCALED,
        abs_tol=1e-11,
        rel_tol=0.0,
    ):
        raise ValidationError("the disclosed Test 6 clipping distance changed")
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
    output = root / f"phase3l-test6-exploratory-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    OUTPUT_PATH = output
    return output


def configure_execution(execution_number: int) -> int:
    if execution_number not in EXECUTION_SEEDS:
        raise ValidationError("execution number must be 1")
    seed = EXECUTION_SEEDS[execution_number]
    PHASE3I.RESEARCH_REVISION = RESEARCH_REVISION
    PHASE3I.RESEARCH_PROTOCOL_SHA256 = RESEARCH_PROTOCOL_SHA256
    PHASE3I.EXPECTED_COMMANDS_SHA256 = EXPECTED_COMMANDS_SHA256
    PHASE3I.OAI_RNG_SEED = seed
    PHASE3I.MINIMUM_TRACE_ROWS = MINIMUM_TRACE_ROWS
    PHASE3I.PING_INTERVAL_COMMANDS = 25
    PHASE3I.CONTROL_ECHO_ABS_TOL_DB = CONTROL_ECHO_ABS_TOL_DB
    PHASE3I.load_commands = load_commands
    PHASE3I.make_output = make_output
    PHASE3I.__file__ = __file__
    return seed


def normalize_output(output: Path, execution_number: int, seed: int) -> None:
    renames = {
        "phase3i-ue.log": "phase3l-ue.log",
        "phase3i-gnb.log": "phase3l-gnb.log",
        "phase3i-command-events.json": "phase3l-command-events.json",
        "phase3i-anchor-windows.json": "phase3l-anchor-windows.json",
        "phase3i-ping-checks.json": "phase3l-ping-checks.json",
        "phase3i-log-analysis.json": "phase3l-log-analysis.json",
        "phase3i_short_trace_telemetry.csv": "phase3l_test6_telemetry.csv",
        "phase3i_anchor_telemetry.csv": "phase3l_anchor_telemetry.csv",
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
            "stage": "phase_3l_posthoc_test6_exploratory_replay",
            "evaluation_status": "posthoc_exploratory_not_confirmatory_validation",
            "execution_number": execution_number,
            "execution_id": "phase3l-test6-exploratory-execution-1",
            "oai_rng_seed": seed,
            "target_rows": TARGET_ROWS,
            "minimum_paired_rows": MINIMUM_TRACE_ROWS,
            "clipped_command_rows": len(EXPECTED_CLIPPED_INDICES),
            "maximum_clipping_distance_scaled": (
                EXPECTED_MAXIMUM_CLIPPING_DISTANCE_SCALED
            ),
            "primary_kpi_alignment_seconds": 0,
            "channel_verification_alignment_seconds": 1,
            "control_echo_abs_tolerance_db": CONTROL_ECHO_ABS_TOL_DB,
            "execution_patch_sha256": EXECUTION_PATCH_SHA256,
            "control_application_verification_source": "immediate_persistent_telnet_show",
            "channel_snapshot_purpose": "static_channel_identity_and_tap_invariants_only",
            "channel_snapshot_control_match_required": False,
            "commands_adapted_during_execution": False,
            "translator_update_authorized": False,
            "test6_accessed": True,
            "final_test6_accessed": True,
            "independent_final_validation": False,
            "exploratory_replay": True,
            "frozen_v1_support_gate_passed": False,
            "confirmatory_support_pass_claimed": False,
            "threshold_specific_xapp_behavior_claimed": False,
        }
    )
    BASE.write_json(state_path, state)


def parser() -> argparse.ArgumentParser:
    root = PHASE3I.parser()
    root.description = "Run the frozen Phase 3L post hoc Test 6 exploratory replay."
    root.add_argument("--execution-number", type=int, choices=(1,), required=True)
    root.set_defaults(
        commands="/local/repository/etc/phase3l-test6-exploratory-commands.csv",
        override_file="/local/upv-phase3l-test6-exploratory-v1/ue.override.yaml",
        output_root="/local/logs/upv-phase3l-test6-exploratory-v1",
    )
    return root


def execute(args: argparse.Namespace) -> int:
    global OUTPUT_PATH
    OUTPUT_PATH = None
    seed = configure_execution(args.execution_number)
    result = PHASE3I.execute(args)
    if OUTPUT_PATH is None:
        raise ValidationError("the Phase 3L output directory was not created")
    normalize_output(OUTPUT_PATH, args.execution_number, seed)
    print(f"PHASE3L_OUTPUT={OUTPUT_PATH}", flush=True)
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
