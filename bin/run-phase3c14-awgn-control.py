#!/usr/bin/env python3
"""Run the frozen five-execution AWGN scalar-transfer control.

Each execution recreates UE1 with an explicit process seed, verifies the
identity AWGN channel, replays the frozen scalar envelope, and records the
same radio and channel telemetry used by the static TDL-B pilot.  The gNB is
never replaced or restarted, and the original UE is restored on every exit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BASE_SCRIPT = Path(__file__).with_name("run-phase3c-scalar-replay.py")
BASE_SPEC = importlib.util.spec_from_file_location("phase3c_scalar_base", BASE_SCRIPT)
if BASE_SPEC is None or BASE_SPEC.loader is None:
    raise RuntimeError(f"cannot load scalar replay support: {BASE_SCRIPT}")
BASE = importlib.util.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(BASE)

PARSER_SCRIPT = Path(__file__).with_name("phase3c_log_parser.py")
PARSER_SPEC = importlib.util.spec_from_file_location(
    "phase3c14_log_parser", PARSER_SCRIPT
)
if PARSER_SPEC is None or PARSER_SPEC.loader is None:
    raise RuntimeError(f"cannot load log parser: {PARSER_SCRIPT}")
PARSER = importlib.util.module_from_spec(PARSER_SPEC)
PARSER_SPEC.loader.exec_module(PARSER)
parse_replay_logs = PARSER.parse_replay_logs

OAI_REVISION = "70508ebaf52f2aae420566d380c6537f2efb9f0c"
CHANNEL_FAMILY = "AWGN"
RNG_SEEDS = (32001, 32002, 32003, 32004, 32005)
ENVELOPE = BASE.ENVELOPE
SEGMENT_SECONDS = BASE.SEGMENT_SECONDS
SETTLING_SECONDS = BASE.SETTLING_SECONDS
FIXED_NOISE_DB = BASE.FIXED_NOISE_DB
DEBUG_IMAGE = "oai-nr-ue-phase3c14-awgn:70508eb"
ORIGINAL_IMAGE = "ghinwa555/oai-nr-ue-chan:v4"
UE_CONTAINER = BASE.UE_CONTAINER
GNB_CONTAINER = BASE.GNB_CONTAINER
UE_SERVICE = BASE.UE_SERVICE
KEY_VALUE = re.compile(r"([A-Za-z0-9_]+)=([^\s]+)")

TELEMETRY_FIELDS = (
    "replay_id",
    "oai_rng_seed",
    "t_s",
    "commanded_gain_db",
    "applied_gain_db",
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
    "noise_power_db",
    "rsrp_digital_power_linear",
    "rsrp_db_per_re_unquantized",
    "ss_rsrp_dbm_integer",
    "ss_sinr_db",
    "attached",
)

ReplayError = BASE.ReplayError


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ReplayError(f"missing or unsafe frozen input: {path}")
    observed = sha256(path)
    if observed != expected:
        raise ReplayError(
            f"checksum mismatch for {path}: expected {expected}, observed {observed}"
        )


def override_text(image: str, seed: int) -> str:
    return (
        "services:\n"
        f"  {UE_SERVICE}:\n"
        f"    image: {image}\n"
        "    environment:\n"
        f'      OAI_RNGSEED: "{seed}"\n'
    )


def channel_command(
    helper: Path,
    operation: str,
    parameter: str,
    value: float | None = None,
) -> dict[str, Any]:
    command = [
        "python3",
        str(helper),
        operation,
        "--cell",
        "1",
        "--direction",
        "dl",
        "--ue",
        "1",
        "--parameter",
        parameter,
    ]
    if value is not None:
        command.extend(("--value", str(value)))
    try:
        result = json.loads(BASE.run_command(*command))
    except json.JSONDecodeError as error:
        raise ReplayError(f"channel helper returned invalid JSON: {error}") from error
    if (
        result.get("model_index") != 0
        or result.get("model_name") != "rfsimu_channel_enB0"
    ):
        raise ReplayError(f"unexpected active channel identity: {result}")
    if result.get("model_type") != CHANNEL_FAMILY:
        raise ReplayError(f"the Phase 3C14 control requires AWGN, observed: {result}")
    if operation == "set" and result.get("verified") is not True:
        raise ReplayError(f"channel write was not verified: {result}")
    return result


def parse_marker_lines(log_text: str, marker: str) -> dict[int, dict[str, str]]:
    parsed: dict[int, dict[str, str]] = {}
    for line in log_text.splitlines():
        position = line.find(marker)
        if position < 0:
            continue
        fields = dict(KEY_VALUE.findall(line[position + len(marker) :]))
        if "utc_second" in fields:
            parsed[int(fields["utc_second"])] = fields
    return parsed


def build_telemetry_rows(
    replay_id: str,
    rng_seed: int,
    log_text: str,
    segments: list[dict[str, Any]],
    attachment_checks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ue_rows = parse_marker_lines(log_text, "UE_RADIO_DEBUG_V1")
    channel_rows = parse_marker_lines(log_text, "RFSIM_CHANNEL_DEBUG_V1")
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
    outside_segments = 0
    for utc_second, ue in sorted(ue_rows.items()):
        sample_epoch = utc_second + 0.5
        segment = next(
            (
                item
                for item in segments
                if float(item["applied_epoch"])
                <= sample_epoch
                < float(item["applied_epoch"]) + SEGMENT_SECONDS
            ),
            None,
        )
        if segment is None:
            outside_segments += 1
            continue
        channel = channel_rows.get(utc_second)
        if channel is None:
            missing_channel += 1
            continue
        missing = required_channel - set(channel)
        if missing:
            raise ReplayError(f"incomplete RFsim channel telemetry: {sorted(missing)}")
        if channel["oai_rng_seed"] != str(rng_seed):
            raise ReplayError(
                f"RNG seed mismatch: expected {rng_seed}, observed {channel['oai_rng_seed']}"
            )
        relative_time = float(segment["nominal_start_s"]) + (
            sample_epoch - float(segment["applied_epoch"])
        )
        row = {
            "replay_id": replay_id,
            "oai_rng_seed": str(rng_seed),
            "t_s": f"{relative_time:.6f}",
            "commanded_gain_db": f"{float(segment['commanded_gain_db']):.9f}",
            "applied_gain_db": f"{float(channel['applied_gain_db']):.9f}",
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
            "noise_power_db": channel["noise_power_db"],
            "rsrp_digital_power_linear": ue["rsrp_digital_power_linear"],
            "rsrp_db_per_re_unquantized": ue["rsrp_db_per_re_unquantized"],
            "ss_rsrp_dbm_integer": ue["ss_rsrp_dbm_integer"],
            "ss_sinr_db": ue["ss_sinr_db"],
            "attached": str(
                BASE.nearest_attachment(sample_epoch, attachment_checks)
            ).lower(),
        }
        numeric_fields = {
            "oai_rng_seed",
            "t_s",
            "commanded_gain_db",
            "applied_gain_db",
            "channel_snapshot_timestamp_ns",
            "tap_energy_linear",
            "channel_length",
            "nb_taps",
            "nb_tx",
            "nb_rx",
            "noise_power_db",
            "rsrp_digital_power_linear",
            "rsrp_db_per_re_unquantized",
            "ss_rsrp_dbm_integer",
            "ss_sinr_db",
        }
        if not all(math.isfinite(float(row[field])) for field in numeric_fields):
            raise ReplayError(f"non-finite telemetry row: {row}")
        rows.append(row)
    return rows, {
        "ue_debug_seconds": len(ue_rows),
        "channel_debug_seconds": len(channel_rows),
        "matched_rows": len(rows),
        "missing_channel_rows": missing_channel,
        "outside_segment_rows": outside_segments,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TELEMETRY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def log_suffix(current: str, prefix: str, label: str) -> str:
    if not current.startswith(prefix):
        raise ReplayError(f"{label} log stream was not append-only")
    return current[len(prefix) :]


def run_one_replay(
    replay_number: int,
    rng_seed: int,
    compose_file: Path,
    override_file: Path,
    channel_helper: Path,
    debug_image: str,
    expected_debug_image_id: str,
    output: Path,
    attach_timeout_seconds: float,
    expected_gnb_restart_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    replay_id = f"awgn-{replay_number}"
    override_file.write_text(override_text(debug_image, rng_seed))
    BASE.compose(
        compose_file,
        override_file,
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        UE_SERVICE,
    )
    observed_image = BASE.docker_inspect("{{.Image}}", UE_CONTAINER)
    if observed_image != expected_debug_image_id:
        raise ReplayError(f"debug UE image mismatch: {observed_image}")
    environment = BASE.docker_inspect(
        "{{range .Config.Env}}{{println .}}{{end}}", UE_CONTAINER
    ).splitlines()
    if environment.count(f"OAI_RNGSEED={rng_seed}") != 1:
        raise ReplayError(f"container does not have exactly one OAI_RNGSEED={rng_seed}")
    BASE.wait_attached(attach_timeout_seconds)
    identity = channel_command(channel_helper, "show", "ploss")
    channel_command(channel_helper, "set", "noise_power_dB", FIXED_NOISE_DB)
    BASE.wait_for_markers(15.0)
    ue_log_prefix = BASE.run_command("docker", "logs", UE_CONTAINER)
    gnb_log_prefix = BASE.run_command("docker", "logs", GNB_CONTAINER)

    attachment_checks: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for index, (label, gain_db) in enumerate(ENVELOPE):
        result = channel_command(channel_helper, "set", "ploss", gain_db)
        segments.append(
            {
                "segment_index": index,
                "segment_label": label,
                "nominal_start_s": index * SEGMENT_SECONDS,
                "analysis_start_s": index * SEGMENT_SECONDS + SETTLING_SECONDS,
                "commanded_gain_db": gain_db,
                "applied_epoch": float(result["applied_epoch"]),
                "observed_gain_db": float(result["observed"]),
            }
        )
        deadline = time.monotonic() + SEGMENT_SECONDS
        while time.monotonic() < deadline:
            attached = BASE.is_attached()
            attachment_checks.append({"epoch": time.time(), "attached": attached})
            if not attached:
                raise ReplayError(f"attachment lost during {replay_id} segment {label}")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(1.0, remaining))

    time.sleep(2.0)
    log_text = BASE.run_command("docker", "logs", UE_CONTAINER)
    gnb_log_text = BASE.run_command("docker", "logs", GNB_CONTAINER)
    ue_observation_log = log_suffix(log_text, ue_log_prefix, "UE")
    gnb_observation_log = log_suffix(gnb_log_text, gnb_log_prefix, "gNB")
    raw_log = output / f"{replay_id}-ue.log"
    raw_log.write_text(log_text + "\n")
    raw_gnb_log = output / f"{replay_id}-gnb.log"
    raw_gnb_log.write_text(gnb_log_text + "\n")
    BASE.write_json(output / f"{replay_id}-control.json", segments)
    BASE.write_json(output / f"{replay_id}-attachment.json", attachment_checks)
    rows, diagnostics = build_telemetry_rows(
        replay_id, rng_seed, log_text, segments, attachment_checks
    )
    write_csv(output / f"{replay_id}-telemetry.csv", rows)
    if len(rows) < len(ENVELOPE) * 5:
        raise ReplayError(
            f"{replay_id} produced only {len(rows)} matched telemetry rows"
        )
    fingerprints = {row["tap_fingerprint_fnv1a64"] for row in rows}
    if len(fingerprints) != 1:
        raise ReplayError(f"{replay_id} did not retain one AWGN identity fingerprint")
    model_names = {row["channel_model_name"] for row in rows}
    if model_names != {"rfsimu_channel_enB0"}:
        raise ReplayError(f"unexpected RFsim model names in {replay_id}: {model_names}")
    dimensions = {(int(row["channel_length"]), int(row["nb_taps"])) for row in rows}
    if dimensions != {(1, 1)}:
        raise ReplayError(
            f"{replay_id} does not expose the 1x1 AWGN identity path: {dimensions}"
        )
    tap_energies = {float(row["tap_energy_linear"]) for row in rows}
    if len(tap_energies) != 1 or not math.isclose(next(iter(tap_energies)), 1.0):
        raise ReplayError(
            f"{replay_id} AWGN tap energy is not fixed at one: {tap_energies}"
        )
    log_result = parse_replay_logs(
        log_text,
        gnb_log_text,
        ue_failure_text=ue_observation_log,
        gnb_failure_text=gnb_observation_log,
    )
    failure_marker_counts = log_result["failure_marker_counts"]
    critical_failure_count = sum(
        count for domain in failure_marker_counts.values() for count in domain.values()
    )
    ue_restart_count = int(BASE.docker_inspect("{{.RestartCount}}", UE_CONTAINER))
    gnb_restart_count = int(BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER))
    gnb_health = BASE.docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER)
    return rows, {
        "replay_id": replay_id,
        "oai_rng_seed": rng_seed,
        "debug_image_id": observed_image,
        "channel_identity": identity,
        "segments": segments,
        "diagnostics": diagnostics,
        "tap_fingerprint_fnv1a64": next(iter(fingerprints)),
        "channel_dimensions": {"channel_length": 1, "nb_taps": 1},
        "failure_marker_counts": failure_marker_counts,
        "critical_failure_count": critical_failure_count,
        "ue_restart_count": ue_restart_count,
        "gnb_restart_count": gnb_restart_count,
        "gnb_health": gnb_health,
        "operational_runtime_pass": ue_restart_count == 0
        and gnb_restart_count == expected_gnb_restart_count
        and gnb_health == "healthy",
        "raw_log_sha256": sha256(raw_log),
        "raw_gnb_log_sha256": sha256(raw_gnb_log),
        "continuous_attachment": all(item["attached"] for item in attachment_checks),
    }


def make_output(root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output = root / f"phase3c14-awgn-control-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def execute(args: argparse.Namespace) -> int:
    if hasattr(sys, "geteuid") and sys.geteuid() != 0:
        raise ReplayError("run this command as root")
    compose_file = Path(args.compose_file).resolve()
    channel_config = Path(args.channel_config).resolve()
    ue_config = Path(args.ue_config).resolve()
    channel_helper = Path(args.channel_helper).resolve()
    override_file = Path(args.override_file).resolve()
    output = make_output(Path(args.output_root).resolve())
    print(f"OUTPUT_DIR={output}", flush=True)

    profile_root = Path(__file__).resolve().parents[1]
    profile_revision = BASE.run_command(
        "git", "-C", str(profile_root), "rev-parse", "HEAD"
    )
    if profile_revision != args.expected_profile_revision:
        raise ReplayError(
            f"profile revision mismatch: expected {args.expected_profile_revision}, "
            f"observed {profile_revision}"
        )
    runner_sha256 = sha256(Path(__file__).resolve())
    if runner_sha256 != args.expected_runner_sha256:
        raise ReplayError(
            f"runner checksum mismatch: expected {args.expected_runner_sha256}, "
            f"observed {runner_sha256}"
        )
    require_hash(compose_file, args.expected_compose_sha256)
    require_hash(channel_config, args.expected_channel_config_sha256)
    require_hash(ue_config, args.expected_ue_config_sha256)
    if not channel_helper.is_file() or channel_helper.is_symlink():
        raise ReplayError(f"missing or unsafe channel helper: {channel_helper}")
    if BASE.image_id(args.debug_image) != args.expected_debug_image_id:
        raise ReplayError("pinned Phase 3C14 debug image ID is unavailable")
    debug_image_revision_label = BASE.run_command(
        "docker",
        "image",
        "inspect",
        "-f",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        args.debug_image,
    )
    if debug_image_revision_label != OAI_REVISION:
        raise ReplayError(
            "debug image revision-label mismatch: "
            f"expected {OAI_REVISION}, observed {debug_image_revision_label}"
        )
    if BASE.docker_inspect("{{.Config.Image}}", UE_CONTAINER) != ORIGINAL_IMAGE:
        raise ReplayError("the live UE is not using the frozen original image")
    original_image_id = BASE.docker_inspect("{{.Image}}", UE_CONTAINER)
    original_gnb_restart_count = int(
        BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER)
    )
    if BASE.docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER) != "healthy":
        raise ReplayError("gNB is not healthy before the control")

    override_file.parent.mkdir(parents=True, exist_ok=True)
    mutated = False
    error: str | None = None
    rollback: dict[str, Any] = {"attempted": False, "passed": False}
    summaries: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    try:
        mutated = True
        for replay_number, rng_seed in enumerate(RNG_SEEDS, start=1):
            rows, summary = run_one_replay(
                replay_number,
                rng_seed,
                compose_file,
                override_file,
                channel_helper,
                args.debug_image,
                args.expected_debug_image_id,
                output,
                args.attach_timeout_seconds,
                original_gnb_restart_count,
            )
            all_rows.extend(rows)
            summaries.append(summary)
            if summary["critical_failure_count"] != 0:
                raise ReplayError(
                    f"critical radio failure marker observed during {summary['replay_id']}"
                )
            if not summary["operational_runtime_pass"]:
                raise ReplayError(
                    f"container restart or gNB health failure during {summary['replay_id']}"
                )
        fingerprints = {summary["tap_fingerprint_fnv1a64"] for summary in summaries}
        if len(fingerprints) != 1:
            raise ReplayError(
                "AWGN executions did not retain one common identity fingerprint"
            )
        write_csv(output / "awgn_scalar_telemetry.csv", all_rows)
    except (KeyboardInterrupt, OSError, ReplayError, subprocess.SubprocessError) as exc:
        error = str(exc)
    finally:
        if mutated:
            rollback["attempted"] = True
            try:
                require_hash(compose_file, args.expected_compose_sha256)
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
                gnb_restarts = int(
                    BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER)
                )
                rollback.update(
                    {
                        "restored_image_id": restored_id,
                        "expected_image_id": original_image_id,
                        "gnb_restart_count_before": original_gnb_restart_count,
                        "gnb_restart_count_after": gnb_restarts,
                        "passed": restored_id == original_image_id
                        and gnb_restarts == original_gnb_restart_count
                        and BASE.is_attached(),
                    }
                )
                if not rollback["passed"]:
                    raise ReplayError(f"rollback verification failed: {rollback}")
            except (OSError, ReplayError, subprocess.SubprocessError) as rollback_error:
                rollback["error"] = str(rollback_error)
                error = (
                    f"{error + '; ' if error else ''}rollback failed: {rollback_error}"
                )

    state = {
        "schema_version": 1,
        "stage": "phase_3c14_awgn_execution_control",
        "execution_completed": error is None and len(summaries) == len(RNG_SEEDS),
        "error": error,
        "oai_revision": OAI_REVISION,
        "profile_revision": profile_revision,
        "runner_sha256": runner_sha256,
        "channel_family": CHANNEL_FAMILY,
        "rng_seeds": list(RNG_SEEDS),
        "compose_sha256": sha256(compose_file),
        "channel_config_sha256": sha256(channel_config),
        "ue_config_sha256": sha256(ue_config),
        "debug_image": args.debug_image,
        "debug_image_id": args.expected_debug_image_id,
        "debug_image_revision_label": debug_image_revision_label,
        "original_image": ORIGINAL_IMAGE,
        "original_image_id": original_image_id,
        "gNB_untouched": True,
        "replays": summaries,
        "common_fingerprint_count": len(
            {summary["tap_fingerprint_fnv1a64"] for summary in summaries}
        ),
        "rollback": rollback,
    }
    BASE.write_json(output / "execution_state.json", state)
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"TELEMETRY={output / 'awgn_scalar_telemetry.csv'}", flush=True)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument(
        "--compose-file", default="/local/repository/etc/docker-compose-cell1.yaml"
    )
    root.add_argument(
        "--channel-config", default="/local/repository/etc/channelmod-cell1.conf"
    )
    root.add_argument("--ue-config", default="/local/repository/etc/nr-ue-cell1-1.conf")
    root.add_argument(
        "--channel-helper", default="/local/repository/bin/channel-cell.py"
    )
    root.add_argument(
        "--override-file", default="/local/phase3c/phase3c14-ue-debug.override.yaml"
    )
    root.add_argument("--output-root", default="/local/logs/phase3c14")
    root.add_argument("--debug-image", default=DEBUG_IMAGE)
    root.add_argument("--expected-debug-image-id", required=True)
    root.add_argument("--expected-profile-revision", required=True)
    root.add_argument("--expected-runner-sha256", required=True)
    root.add_argument("--expected-compose-sha256", required=True)
    root.add_argument("--expected-channel-config-sha256", required=True)
    root.add_argument("--expected-ue-config-sha256", required=True)
    root.add_argument("--attach-timeout-seconds", type=float, default=180.0)
    return root


def main() -> int:
    BASE.install_signal_handlers()
    try:
        return execute(parser().parse_args())
    except (OSError, ReplayError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
