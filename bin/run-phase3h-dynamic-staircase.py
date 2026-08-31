#!/usr/bin/env python3
"""Run three complete dynamic gain/noise staircase sequences."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

SUPPORT_SCRIPT = Path(__file__).with_name("run-rfsim-noise-validation.py")
SUPPORT_SPEC = importlib.util.spec_from_file_location("phase3h_staircase_support", SUPPORT_SCRIPT)
if SUPPORT_SPEC is None or SUPPORT_SPEC.loader is None:
    raise RuntimeError(f"cannot load execution support: {SUPPORT_SCRIPT}")
SUPPORT = importlib.util.module_from_spec(SUPPORT_SPEC)
SUPPORT_SPEC.loader.exec_module(SUPPORT)
BASE = SUPPORT.BASE
PARSER = SUPPORT.PARSER

OAI_REVISION = "70508ebaf52f2aae420566d380c6537f2efb9f0c"
RESEARCH_REVISION = "631525b7df8e6b86483e3518b69e2a5d30892456"
RESEARCH_PROTOCOL_SHA256 = (
    "194dfbc0ebf8a08fc994e84ece4cbad16d065f621f0526bbd5f29d68c3b03fa6"
)
EXPECTED_PLAN_SHA256 = (
    "8505f2f3137d08f94eaefe016827950b8782b4b6b9452ec4466425787959fc7f"
)
DEBUG_IMAGE = "oai-nr-ue-rfsim-phase3h:70508eb"
ORIGINAL_IMAGE = SUPPORT.ORIGINAL_IMAGE
CHANNEL_FAMILY = "AWGN"
ATTACH_GAIN_DB = 0.0
ATTACH_NOISE_DB = -60.0
ANCHOR_GAIN_DB = -10.0
ANCHOR_NOISE_DB = -25.0
STATE_SETTLING_SECONDS = 5.0
STATE_USABLE_SECONDS = 10.0
ANCHOR_START_SETTLING_SECONDS = 5.0
ANCHOR_END_SETTLING_SECONDS = 5.0
ANCHOR_USABLE_SECONDS = 10.0
ANCHOR_RESET_SECONDS = 3.0
POST_ATTACH_STABILIZATION_SECONDS = 5.0
MINIMUM_SEGMENT_ROWS = 7
UE_CONTAINER = BASE.UE_CONTAINER
GNB_CONTAINER = BASE.GNB_CONTAINER
UE_SERVICE = BASE.UE_SERVICE
ValidationError = BASE.ReplayError

PLAN_FIELDS = (
    "sequence_index",
    "repetition",
    "oai_rng_seed",
    "segment_index",
    "segment_type",
    "state_id",
    "position",
    "gain_db",
    "noise_power_db",
    "expected_relative_rsrp_db",
    "expected_sinr_db",
)
TELEMETRY_FIELDS = (
    "sequence_index",
    "sequence_id",
    "repetition",
    "oai_rng_seed",
    "segment_index",
    "segment_type",
    "state_id",
    "position",
    "commanded_gain_db",
    "applied_gain_db",
    "commanded_noise_power_db",
    "applied_noise_power_db",
    "expected_relative_rsrp_db",
    "expected_sinr_db",
    "utc_second",
    "channel_family",
    "channel_model_name",
    "channel_snapshot_id",
    "channel_snapshot_timestamp_ns",
    "tap_energy_linear",
    "tap_fingerprint_fnv1a64",
    "channel_length",
    "nb_taps",
    "nb_tx",
    "nb_rx",
    "rsrp_digital_power_linear",
    "rsrp_db_per_re_unquantized",
    "ss_rsrp_dbm_integer",
    "ss_sinr_db",
    "attached",
)


def load_staircase_plan(path: Path) -> list[list[dict[str, Any]]]:
    SUPPORT.require_hash(path, EXPECTED_PLAN_SHA256)
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != PLAN_FIELDS:
            raise ValidationError(f"unexpected staircase-plan columns: {reader.fieldnames}")
        raw_rows = list(reader)
    rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(raw_rows, start=1):
        try:
            parsed = {
                "sequence_index": int(row["sequence_index"]),
                "repetition": int(row["repetition"]),
                "oai_rng_seed": int(row["oai_rng_seed"]),
                "segment_index": int(row["segment_index"]),
                "segment_type": row["segment_type"],
                "state_id": row["state_id"],
                "position": int(row["position"]),
                "gain_db": float(row["gain_db"]),
                "noise_power_db": float(row["noise_power_db"]),
                "expected_relative_rsrp_db": float(row["expected_relative_rsrp_db"]),
                "expected_sinr_db": float(row["expected_sinr_db"]),
            }
        except (TypeError, ValueError) as error:
            raise ValidationError(f"invalid staircase-plan row {row_index}: {row}") from error
        numeric = (
            parsed["gain_db"],
            parsed["noise_power_db"],
            parsed["expected_relative_rsrp_db"],
            parsed["expected_sinr_db"],
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValidationError(f"non-finite staircase-plan row {row_index}")
        rows.append(parsed)
    if len(rows) != 27:
        raise ValidationError("the staircase plan must contain 27 segments")
    sequences: list[list[dict[str, Any]]] = []
    for sequence_index in (1, 2, 3):
        sequence = [row for row in rows if row["sequence_index"] == sequence_index]
        sequence.sort(key=lambda row: row["segment_index"])
        if len(sequence) != 9 or [row["segment_index"] for row in sequence] != list(
            range(1, 10)
        ):
            raise ValidationError(f"sequence {sequence_index} is incomplete")
        if [row["segment_type"] for row in sequence] != [
            "anchor_start",
            *("validation" for _ in range(7)),
            "anchor_end",
        ]:
            raise ValidationError(f"sequence {sequence_index} has invalid segment types")
        if sequence[0]["state_id"] != "anchor" or sequence[-1]["state_id"] != "anchor":
            raise ValidationError(f"sequence {sequence_index} has invalid anchor rows")
        if {
            (row["gain_db"], row["noise_power_db"])
            for row in (sequence[0], sequence[-1])
        } != {(ANCHOR_GAIN_DB, ANCHOR_NOISE_DB)}:
            raise ValidationError(f"sequence {sequence_index} changed the anchor")
        validation = sequence[1:-1]
        if Counter(row["state_id"] for row in validation) != Counter(
            {state_id: 1 for state_id in "ABCDEFG"}
        ):
            raise ValidationError(f"sequence {sequence_index} does not visit A through G once")
        if len({row["oai_rng_seed"] for row in sequence}) != 1:
            raise ValidationError(f"sequence {sequence_index} changes its RNG seed")
        sequences.append(sequence)
    if len({sequence[0]["oai_rng_seed"] for sequence in sequences}) != 3:
        raise ValidationError("sequence RNG seeds are not unique")
    position_sets = {
        state_id: {
            row["position"]
            for sequence in sequences
            for row in sequence
            if row["state_id"] == state_id
        }
        for state_id in "ABCDEFG"
    }
    if not all(len(positions) == 3 for positions in position_sets.values()):
        raise ValidationError("the validation-state positions are not counterbalanced")
    return sequences


def write_telemetry(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TELEMETRY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _sleep_with_attachment_check(seconds: float, label: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not BASE.is_attached():
            raise ValidationError(f"attachment lost during {label}")
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def _collect_window(seconds: float, label: str) -> dict[str, Any]:
    start_epoch = time.time()
    attachment_checks: list[dict[str, Any]] = []
    ping_checks: list[dict[str, Any]] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        attached = BASE.is_attached()
        passed = SUPPORT.ping_once() if attached else False
        now = time.time()
        attachment_checks.append({"epoch": now, "attached": attached})
        ping_checks.append({"epoch": now, "passed": passed})
        if not attached:
            raise ValidationError(f"attachment lost during {label}")
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return {
        "usable_start_epoch": start_epoch,
        "usable_end_epoch": time.time(),
        "attachment_checks": attachment_checks,
        "ping_checks": ping_checks,
    }


def _set_controls(channel_helper: Path, gain_db: float, noise_db: float) -> dict[str, Any]:
    gain = SUPPORT.channel_command(channel_helper, "set", "ploss", gain_db)
    noise = SUPPORT.channel_command(channel_helper, "set", "noise_power_dB", noise_db)
    return {"gain": gain, "noise": noise}


def build_segment_telemetry(
    sequence_id: str,
    segment: dict[str, Any],
    log_text: str,
    usable_start_epoch: float,
    usable_end_epoch: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ue_rows = SUPPORT.SUPPORT.parse_marker_lines(log_text, "UE_RADIO_DEBUG_V1")
    channel_rows = SUPPORT.SUPPORT.parse_marker_lines(log_text, "RFSIM_CHANNEL_DEBUG_V1")
    required_channel = {
        "model",
        "channel_snapshot_id",
        "channel_snapshot_timestamp_ns",
        "tap_energy_linear",
        "tap_fingerprint_fnv1a64",
        "channel_length",
        "nb_taps",
        "nb_tx",
        "nb_rx",
        "oai_rng_seed",
        "applied_gain_db",
        "noise_power_db",
    }
    rows: list[dict[str, Any]] = []
    missing_channel = 0
    for utc_second, ue in sorted(ue_rows.items()):
        sample_epoch = utc_second + 0.5
        if not usable_start_epoch <= sample_epoch < usable_end_epoch:
            continue
        channel = channel_rows.get(utc_second)
        if channel is None:
            missing_channel += 1
            continue
        missing = required_channel - set(channel)
        if missing:
            raise ValidationError(f"incomplete RFsim channel telemetry: {sorted(missing)}")
        if channel["oai_rng_seed"] != str(segment["oai_rng_seed"]):
            raise ValidationError(
                f"RNG seed mismatch: expected {segment['oai_rng_seed']}, "
                f"observed {channel['oai_rng_seed']}"
            )
        row = {
            "sequence_index": segment["sequence_index"],
            "sequence_id": sequence_id,
            "repetition": segment["repetition"],
            "oai_rng_seed": segment["oai_rng_seed"],
            "segment_index": segment["segment_index"],
            "segment_type": segment["segment_type"],
            "state_id": segment["state_id"],
            "position": segment["position"],
            "commanded_gain_db": segment["gain_db"],
            "applied_gain_db": channel["applied_gain_db"],
            "commanded_noise_power_db": segment["noise_power_db"],
            "applied_noise_power_db": channel["noise_power_db"],
            "expected_relative_rsrp_db": segment["expected_relative_rsrp_db"],
            "expected_sinr_db": segment["expected_sinr_db"],
            "utc_second": utc_second,
            "channel_family": CHANNEL_FAMILY,
            "channel_model_name": channel["model"],
            "channel_snapshot_id": channel["channel_snapshot_id"],
            "channel_snapshot_timestamp_ns": channel["channel_snapshot_timestamp_ns"],
            "tap_energy_linear": channel["tap_energy_linear"],
            "tap_fingerprint_fnv1a64": channel["tap_fingerprint_fnv1a64"],
            "channel_length": channel["channel_length"],
            "nb_taps": channel["nb_taps"],
            "nb_tx": channel["nb_tx"],
            "nb_rx": channel["nb_rx"],
            "rsrp_digital_power_linear": ue["rsrp_digital_power_linear"],
            "rsrp_db_per_re_unquantized": ue["rsrp_db_per_re_unquantized"],
            "ss_rsrp_dbm_integer": ue["ss_rsrp_dbm_integer"],
            "ss_sinr_db": ue["ss_sinr_db"],
            "attached": True,
        }
        string_fields = {
            "sequence_id",
            "segment_type",
            "state_id",
            "channel_family",
            "channel_model_name",
            "channel_snapshot_id",
            "tap_fingerprint_fnv1a64",
            "attached",
        }
        if not all(
            math.isfinite(float(row[field]))
            for field in set(TELEMETRY_FIELDS) - string_fields
        ):
            raise ValidationError(f"non-finite telemetry row: {row}")
        rows.append(row)
    return rows, {
        "ue_debug_seconds": len(ue_rows),
        "channel_debug_seconds": len(channel_rows),
        "matched_usable_rows": len(rows),
        "missing_channel_rows": missing_channel,
    }


def run_sequence(
    sequence: list[dict[str, Any]],
    *,
    compose_file: Path,
    override_file: Path,
    attach_config: Path,
    channel_helper: Path,
    debug_image: str,
    expected_debug_image_id: str,
    output: Path,
    attach_timeout_seconds: float,
    expected_gnb_restart_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first = sequence[0]
    sequence_id = (
        f"s{first['sequence_index']}-r{first['repetition']}-seed{first['oai_rng_seed']}"
    )
    override_file.write_text(
        SUPPORT.override_text(debug_image, first["oai_rng_seed"], attach_config)
    )
    BASE.compose(
        compose_file,
        override_file,
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        UE_SERVICE,
    )
    if BASE.docker_inspect("{{.Image}}", UE_CONTAINER) != expected_debug_image_id:
        raise ValidationError(f"debug UE image mismatch during {sequence_id}")
    environment = BASE.docker_inspect(
        "{{range .Config.Env}}{{println .}}{{end}}", UE_CONTAINER
    ).splitlines()
    expected_seed = f"OAI_RNGSEED={first['oai_rng_seed']}"
    if environment.count(expected_seed) != 1:
        raise ValidationError(f"container does not have one {expected_seed}")
    mounted_config = BASE.docker_inspect(
        "{{range .Mounts}}{{if eq .Destination "
        '"/opt/oai-nr-ue/etc/channelmod_rfsimu.conf"}}'
        "{{.Source}}{{end}}{{end}}",
        UE_CONTAINER,
    )
    if Path(mounted_config).resolve() != attach_config:
        raise ValidationError(f"unexpected attached channel config: {mounted_config}")
    BASE.wait_attached(attach_timeout_seconds)
    attach_gain = SUPPORT.channel_command(channel_helper, "show", "ploss")
    attach_noise = SUPPORT.channel_command(channel_helper, "show", "noise_power_dB")
    if not math.isclose(float(attach_gain["observed"]), ATTACH_GAIN_DB, abs_tol=1e-6):
        raise ValidationError(f"UE did not attach at zero gain: {attach_gain}")
    if not math.isclose(float(attach_noise["observed"]), ATTACH_NOISE_DB, abs_tol=1e-6):
        raise ValidationError(f"UE did not attach at -60 dB noise: {attach_noise}")
    BASE.wait_for_markers(15.0)
    _sleep_with_attachment_check(
        POST_ATTACH_STABILIZATION_SECONDS,
        f"{sequence_id} post-attachment stabilization",
    )
    ue_log_prefix = BASE.run_command("docker", "logs", UE_CONTAINER)
    gnb_log_prefix = BASE.run_command("docker", "logs", GNB_CONTAINER)

    windows: list[dict[str, Any]] = []
    command_results: list[dict[str, Any]] = []
    operation_error: BaseException | None = None
    try:
        anchor_start = sequence[0]
        command_results.append(
            {
                "segment_index": anchor_start["segment_index"],
                **_set_controls(
                    channel_helper, anchor_start["gain_db"], anchor_start["noise_power_db"]
                ),
            }
        )
        _sleep_with_attachment_check(
            ANCHOR_START_SETTLING_SECONDS, f"{sequence_id} anchor start settling"
        )
        windows.append(
            {
                "segment": anchor_start,
                **_collect_window(ANCHOR_USABLE_SECONDS, f"{sequence_id} anchor start"),
            }
        )
        for segment in sequence[1:-1]:
            command_results.append(
                {
                    "segment_index": segment["segment_index"],
                    **_set_controls(
                        channel_helper, segment["gain_db"], segment["noise_power_db"]
                    ),
                }
            )
            _sleep_with_attachment_check(
                STATE_SETTLING_SECONDS,
                f"{sequence_id} state {segment['state_id']} settling",
            )
            windows.append(
                {
                    "segment": segment,
                    **_collect_window(
                        STATE_USABLE_SECONDS,
                        f"{sequence_id} state {segment['state_id']}",
                    ),
                }
            )
            command_results.append(
                {
                    "segment_index": f"{segment['segment_index']}-anchor-reset",
                    **_set_controls(channel_helper, ANCHOR_GAIN_DB, ANCHOR_NOISE_DB),
                }
            )
            _sleep_with_attachment_check(
                ANCHOR_RESET_SECONDS,
                f"{sequence_id} anchor reset after {segment['state_id']}",
            )
        anchor_end = sequence[-1]
        _sleep_with_attachment_check(
            ANCHOR_END_SETTLING_SECONDS,
            f"{sequence_id} anchor end settling",
        )
        windows.append(
            {
                "segment": anchor_end,
                **_collect_window(ANCHOR_USABLE_SECONDS, f"{sequence_id} anchor end"),
            }
        )
    except (KeyboardInterrupt, OSError, ValidationError, subprocess.SubprocessError) as error:
        operation_error = error
    finally:
        try:
            _set_controls(channel_helper, ATTACH_GAIN_DB, ATTACH_NOISE_DB)
        except (OSError, ValidationError, subprocess.SubprocessError) as reset_error:
            if operation_error is None:
                operation_error = reset_error

    time.sleep(2.0)
    ue_log_text = BASE.run_command("docker", "logs", UE_CONTAINER)
    gnb_log_text = BASE.run_command("docker", "logs", GNB_CONTAINER)
    ue_log_path = output / f"{sequence_id}-ue.log"
    gnb_log_path = output / f"{sequence_id}-gnb.log"
    ue_log_path.write_text(ue_log_text + "\n")
    gnb_log_path.write_text(gnb_log_text + "\n")
    BASE.write_json(output / f"{sequence_id}-windows.json", windows)
    BASE.write_json(output / f"{sequence_id}-commands.json", command_results)
    if operation_error is not None:
        BASE.write_json(
            output / f"{sequence_id}-summary.json",
            {"sequence_id": sequence_id, "valid": False, "error": str(operation_error)},
        )
        raise ValidationError(f"{sequence_id} failed: {operation_error}")

    rows: list[dict[str, Any]] = []
    segment_diagnostics: list[dict[str, Any]] = []
    attachment_checks: list[dict[str, Any]] = []
    ping_checks: list[dict[str, Any]] = []
    for window in windows:
        segment = window["segment"]
        segment_rows, diagnostics = build_segment_telemetry(
            sequence_id,
            segment,
            ue_log_text,
            window["usable_start_epoch"],
            window["usable_end_epoch"],
        )
        if len(segment_rows) < MINIMUM_SEGMENT_ROWS:
            raise ValidationError(
                f"{sequence_id} segment {segment['segment_index']} produced "
                f"only {len(segment_rows)} paired rows"
            )
        rows.extend(segment_rows)
        segment_diagnostics.append({"segment_index": segment["segment_index"], **diagnostics})
        attachment_checks.extend(window["attachment_checks"])
        ping_checks.extend(window["ping_checks"])
    write_telemetry(output / f"{sequence_id}-telemetry.csv", rows)
    identities = {
        (row["channel_model_name"], row["channel_length"], row["nb_taps"])
        for row in rows
    }
    if identities != {("rfsimu_channel_enB0", "1", "1")}:
        raise ValidationError(f"{sequence_id} did not retain the AWGN identity path")
    if any(
        not math.isclose(float(row["tap_energy_linear"]), 1.0, abs_tol=1e-9)
        for row in rows
    ):
        raise ValidationError(f"{sequence_id} AWGN tap energy changed")
    if any(
        not math.isclose(
            float(row["applied_gain_db"]), float(row["commanded_gain_db"]), abs_tol=1e-6
        )
        or not math.isclose(
            float(row["applied_noise_power_db"]),
            float(row["commanded_noise_power_db"]),
            abs_tol=1e-6,
        )
        for row in rows
    ):
        raise ValidationError(f"{sequence_id} applied controls differ from commands")

    ue_observation_log = SUPPORT.SUPPORT.log_suffix(ue_log_text, ue_log_prefix, "UE")
    gnb_observation_log = SUPPORT.SUPPORT.log_suffix(gnb_log_text, gnb_log_prefix, "gNB")
    log_result = PARSER.parse_replay_logs(
        ue_log_text,
        gnb_log_text,
        ue_failure_text=ue_observation_log,
        gnb_failure_text=gnb_observation_log,
    )
    failures = log_result["failure_marker_counts"]
    critical_failure_count = sum(
        count for domain in failures.values() for count in domain.values()
    )
    ping_fraction = sum(item["passed"] for item in ping_checks) / len(ping_checks)
    ue_restart_count = int(BASE.docker_inspect("{{.RestartCount}}", UE_CONTAINER))
    gnb_restart_count = int(BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER))
    gnb_health = BASE.docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER)
    if ping_fraction < 0.9:
        raise ValidationError(f"{sequence_id} ping fraction {ping_fraction:.3f} is below 0.9")
    if critical_failure_count != 0:
        raise ValidationError(f"critical radio failure during {sequence_id}: {failures}")
    if ue_restart_count != 0 or gnb_restart_count != expected_gnb_restart_count:
        raise ValidationError(f"container restart during {sequence_id}")
    if gnb_health != "healthy":
        raise ValidationError(f"gNB is not healthy after {sequence_id}")
    summary = {
        "sequence_index": first["sequence_index"],
        "sequence_id": sequence_id,
        "repetition": first["repetition"],
        "oai_rng_seed": first["oai_rng_seed"],
        "valid": True,
        "segments": len(windows),
        "validation_states": 7,
        "paired_radio_samples": len(rows),
        "ping_success_fraction": ping_fraction,
        "continuous_attachment": all(item["attached"] for item in attachment_checks),
        "failure_marker_counts": failures,
        "critical_failure_count": critical_failure_count,
        "ue_restart_count": ue_restart_count,
        "gnb_restart_count": gnb_restart_count,
        "gnb_health": gnb_health,
        "segment_diagnostics": segment_diagnostics,
        "ue_log_sha256": SUPPORT.sha256(ue_log_path),
        "gnb_log_sha256": SUPPORT.sha256(gnb_log_path),
    }
    BASE.write_json(output / f"{sequence_id}-summary.json", summary)
    return rows, summary


def make_output(root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output = root / f"phase3h-dynamic-staircase-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def execute(args: argparse.Namespace) -> int:
    if hasattr(sys, "geteuid") and sys.geteuid() != 0:
        raise ValidationError("run this command as root")
    profile_root = Path(args.profile_root).resolve()
    compose_file = Path(args.compose_file).resolve()
    channel_config = Path(args.channel_config).resolve()
    ue_config = Path(args.ue_config).resolve()
    channel_helper = Path(args.channel_helper).resolve()
    plan_file = Path(args.execution_plan).resolve()
    override_file = Path(args.override_file).resolve()
    output = make_output(Path(args.output_root).resolve())
    print(f"OUTPUT_DIR={output}", flush=True)
    if args.expected_plan_sha256 != EXPECTED_PLAN_SHA256:
        raise ValidationError("runtime plan checksum does not match the frozen protocol")
    sequences = load_staircase_plan(plan_file)
    profile_revision = BASE.run_command("git", "-C", str(profile_root), "rev-parse", "HEAD")
    if profile_revision != args.expected_profile_revision:
        raise ValidationError(
            f"profile revision mismatch: expected {args.expected_profile_revision}, "
            f"observed {profile_revision}"
        )
    runner_sha256 = SUPPORT.sha256(Path(__file__).resolve())
    if runner_sha256 != args.expected_runner_sha256:
        raise ValidationError(
            f"runner checksum mismatch: expected {args.expected_runner_sha256}, "
            f"observed {runner_sha256}"
        )
    SUPPORT.require_hash(compose_file, args.expected_compose_sha256)
    SUPPORT.require_hash(channel_config, args.expected_channel_config_sha256)
    SUPPORT.require_hash(ue_config, args.expected_ue_config_sha256)
    if not channel_helper.is_file() or channel_helper.is_symlink():
        raise ValidationError(f"missing or unsafe channel helper: {channel_helper}")
    if BASE.image_id(args.debug_image) != args.expected_debug_image_id:
        raise ValidationError("corrected image ID is unavailable")
    image_revision = BASE.run_command(
        "docker",
        "image",
        "inspect",
        "-f",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        args.debug_image,
    )
    if image_revision != OAI_REVISION:
        raise ValidationError(f"corrected image revision label mismatch: {image_revision}")
    if BASE.docker_inspect("{{.Config.Image}}", UE_CONTAINER) != ORIGINAL_IMAGE:
        raise ValidationError("live UE is not using the frozen rollback image")
    original_image_id = BASE.docker_inspect("{{.Image}}", UE_CONTAINER)
    original_gnb_restart_count = int(
        BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER)
    )
    if BASE.docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER) != "healthy":
        raise ValidationError("gNB is not healthy before the staircase campaign")

    attach_config = output / "channelmod-attach-minus60.conf"
    substitutions = SUPPORT.derive_attach_config(channel_config, attach_config)
    override_file.parent.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    error: str | None = None
    failed_sequence: int | None = None
    rollback: dict[str, Any] = {"attempted": False, "passed": False}
    mutated = False
    try:
        mutated = True
        for sequence in sequences:
            failed_sequence = sequence[0]["sequence_index"]
            rows, summary = run_sequence(
                sequence,
                compose_file=compose_file,
                override_file=override_file,
                attach_config=attach_config,
                channel_helper=channel_helper,
                debug_image=args.debug_image,
                expected_debug_image_id=args.expected_debug_image_id,
                output=output,
                attach_timeout_seconds=args.attach_timeout_seconds,
                expected_gnb_restart_count=original_gnb_restart_count,
            )
            all_rows.extend(rows)
            summaries.append(summary)
            failed_sequence = None
            print(
                f"PASS sequence={summary['sequence_id']} segments={summary['segments']} "
                f"paired={summary['paired_radio_samples']} "
                f"ping={summary['ping_success_fraction']:.3f}",
                flush=True,
            )
    except (KeyboardInterrupt, OSError, ValidationError, subprocess.SubprocessError) as exc:
        error = str(exc)
    finally:
        if all_rows:
            write_telemetry(output / "phase3h_dynamic_staircase_telemetry.csv", all_rows)
        if mutated:
            rollback["attempted"] = True
            try:
                BASE.compose(
                    compose_file,
                    None,
                    "up",
                    "-d",
                    "--no-deps",
                    "--force-recreate",
                    UE_SERVICE,
                )
                BASE.wait_attached(args.attach_timeout_seconds)
                restored_id = BASE.docker_inspect("{{.Image}}", UE_CONTAINER)
                gnb_restarts = int(BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER))
                rollback.update(
                    {
                        "restored_image_id": restored_id,
                        "expected_image_id": original_image_id,
                        "gnb_restart_count_before": original_gnb_restart_count,
                        "gnb_restart_count_after": gnb_restarts,
                        "attached": BASE.is_attached(),
                        "passed": restored_id == original_image_id
                        and gnb_restarts == original_gnb_restart_count
                        and BASE.is_attached(),
                    }
                )
                if not rollback["passed"]:
                    raise ValidationError(f"rollback verification failed: {rollback}")
            except (OSError, ValidationError, subprocess.SubprocessError) as rollback_error:
                rollback["error"] = str(rollback_error)
                error = f"{error + '; ' if error else ''}rollback failed: {rollback_error}"
    execution_state = {
        "schema_version": 1,
        "stage": "phase_3h_dynamic_staircase_translation_validation",
        "execution_completed": error is None and len(summaries) == 3,
        "error": error,
        "failed_sequence": failed_sequence,
        "research_revision": RESEARCH_REVISION,
        "research_protocol_sha256": RESEARCH_PROTOCOL_SHA256,
        "oai_revision": OAI_REVISION,
        "profile_revision": profile_revision,
        "runner_sha256": runner_sha256,
        "execution_plan_sha256": SUPPORT.sha256(plan_file),
        "compose_sha256": SUPPORT.sha256(compose_file),
        "channel_config_sha256": SUPPORT.sha256(channel_config),
        "ue_config_sha256": SUPPORT.sha256(ue_config),
        "attach_config_sha256": SUPPORT.sha256(attach_config),
        "attach_config_noise_substitutions": substitutions,
        "debug_image": args.debug_image,
        "debug_image_id": args.expected_debug_image_id,
        "debug_image_revision_label": image_revision,
        "original_image": ORIGINAL_IMAGE,
        "original_image_id": original_image_id,
        "planned_sequence_count": 3,
        "completed_sequence_count": len(summaries),
        "planned_segment_count": 27,
        "sequence_independent_unit": "complete_staircase_after_clean_ue_recreation",
        "individual_radio_samples_are_independent_repetitions": False,
        "execution_plan": [row for sequence in sequences for row in sequence],
        "sequences": summaries,
        "rollback": rollback,
        "gNB_untouched": True,
        "final_test6_accessed": False,
        "abc_authorized": False,
        "short_trace_replay_authorized": False,
        "full_trace_replay_authorized": False,
    }
    BASE.write_json(output / "execution_state.json", execution_state)
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"TELEMETRY={output / 'phase3h_dynamic_staircase_telemetry.csv'}", flush=True)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--profile-root", default="/local/repository")
    root.add_argument(
        "--compose-file", default="/local/repository/etc/docker-compose-cell1.yaml"
    )
    root.add_argument(
        "--channel-config", default="/local/repository/etc/channelmod-cell1.conf"
    )
    root.add_argument("--ue-config", default="/local/repository/etc/nr-ue-cell1-1.conf")
    root.add_argument("--channel-helper", default="/local/repository/bin/channel-cell.py")
    root.add_argument(
        "--execution-plan",
        default="/local/repository/etc/phase3h-dynamic-staircase-plan.csv",
    )
    root.add_argument(
        "--override-file", default="/local/upv-phase3h-staircase-v1/ue.override.yaml"
    )
    root.add_argument(
        "--output-root", default="/local/logs/upv-phase3h-dynamic-staircase-v1"
    )
    root.add_argument("--debug-image", default=DEBUG_IMAGE)
    root.add_argument("--expected-debug-image-id", required=True)
    root.add_argument("--expected-profile-revision", required=True)
    root.add_argument("--expected-runner-sha256", required=True)
    root.add_argument("--expected-plan-sha256", required=True)
    root.add_argument("--expected-compose-sha256", required=True)
    root.add_argument("--expected-channel-config-sha256", required=True)
    root.add_argument("--expected-ue-config-sha256", required=True)
    root.add_argument("--attach-timeout-seconds", type=float, default=180.0)
    return root


def main() -> int:
    BASE.install_signal_handlers()
    try:
        return execute(parser().parse_args())
    except (OSError, ValidationError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
