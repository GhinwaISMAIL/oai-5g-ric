#!/usr/bin/env python3
"""Run the frozen Phase 3C scalar transfer replay on one RFsim UE.

This runner is deliberately narrow: it replaces only UE1 with the pinned debug
image, executes the five frozen downlink gain plateaus twice, captures raw logs,
builds the evaluator CSV, and restores the original UE image in a ``finally``
block.  The gNB is never replaced or restarted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

COMPOSE_SHA256 = "db5aade37a4613a95c3f9682cdddf3bc5bc73d74f398c004105547c80b8d0260"
CHANNEL_CONFIG_SHA256 = "8814d9dd7f05ae96093a4f2a327e176f638a0fff8030136a844d1e0950179d72"
UE_CONFIG_SHA256 = "d7f10f47440e67a9395391b11797473dc24a63c90d2faad9292c216fc3a6734e"
DEBUG_IMAGE = "oai-nr-ue-phase3c-debug:70508eb"
DEBUG_IMAGE_ID = "sha256:8969f8c64578c18624bf8e367f7ea032725d2ae8eba9f6b27013bc3e198f632e"
ORIGINAL_IMAGE = "ghinwa555/oai-nr-ue-chan:v4"
UE_CONTAINER = "ric5g-ue-cell1-1"
GNB_CONTAINER = "ric5g-gnb-cell1"
UE_SERVICE = "oai-nr-ue1"
OVERRIDE_TEXT = f"""services:
  {UE_SERVICE}:
    image: {DEBUG_IMAGE}
"""
ENVELOPE = (
    ("baseline", 0.0),
    ("descent", -2.0),
    ("nadir", -4.0),
    ("return", -2.0),
    ("recovery", 0.0),
)
SEGMENT_SECONDS = 10.0
SETTLING_SECONDS = 3.0
FIXED_NOISE_DB = -30.0
REPLAYS = 2
TELEMETRY_FIELDS = (
    "replay_id",
    "t_s",
    "commanded_gain_db",
    "applied_gain_db",
    "channel_snapshot_id",
    "channel_snapshot_timestamp_ns",
    "tap_energy_linear",
    "noise_power_db",
    "rsrp_digital_power_linear",
    "rsrp_db_per_re_unquantized",
    "ss_rsrp_dbm_integer",
    "ss_sinr_db",
    "attached",
)
KEY_VALUE = re.compile(r"([A-Za-z0-9_]+)=([^\s]+)")


class ReplayError(RuntimeError):
    pass


def run_command(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = result.stdout.strip()
    if check and result.returncode != 0:
        raise ReplayError(f"command failed ({result.returncode}): {' '.join(args)}\n{output}")
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def require_hash(path: Path, expected: str) -> None:
    observed = sha256(path)
    if observed != expected:
        raise ReplayError(f"checksum mismatch for {path}: expected {expected}, observed {observed}")


def docker_inspect(template: str, target: str) -> str:
    return run_command("docker", "inspect", "-f", template, target)


def image_id(reference: str) -> str:
    return run_command("docker", "image", "inspect", "-f", "{{.Id}}", reference)


def is_attached() -> bool:
    result = subprocess.run(
        ("docker", "exec", UE_CONTAINER, "ip", "link", "show", "oaitun_ue1"),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def wait_attached(timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_attached():
            return
        time.sleep(2.0)
    raise ReplayError(f"{UE_CONTAINER} did not attach within {timeout_seconds:.0f} seconds")


def compose(compose_file: Path, override_file: Path | None, *args: str) -> str:
    command = ["docker", "compose", "-f", str(compose_file)]
    if override_file is not None:
        command.extend(("-f", str(override_file)))
    command.extend(args)
    return run_command(*command)


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
        result = json.loads(run_command(*command))
    except json.JSONDecodeError as error:
        raise ReplayError(f"channel helper returned invalid JSON: {error}") from error
    if result.get("model_index") != 0 or result.get("model_name") != "rfsimu_channel_enB0":
        raise ReplayError(f"unexpected active channel identity: {result}")
    if result.get("model_type") != "AWGN":
        raise ReplayError(f"the scalar transfer replay requires AWGN, observed: {result}")
    if operation == "set" and result.get("verified") is not True:
        raise ReplayError(f"channel write was not verified: {result}")
    return result


def wait_for_markers(timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        logs = run_command("docker", "logs", UE_CONTAINER)
        if "UE_RADIO_DEBUG_V1" in logs and "RFSIM_CHANNEL_DEBUG_V1" in logs:
            return
        time.sleep(1.0)
    raise ReplayError("both debug telemetry markers were not observed before replay")


def parse_marker_lines(log_text: str, marker: str) -> dict[int, dict[str, str]]:
    parsed: dict[int, dict[str, str]] = {}
    for line in log_text.splitlines():
        position = line.find(marker)
        if position < 0:
            continue
        fields = dict(KEY_VALUE.findall(line[position + len(marker) :]))
        if "utc_second" not in fields:
            continue
        parsed[int(fields["utc_second"])] = fields
    return parsed


def nearest_attachment(sample_epoch: float, checks: list[dict[str, Any]]) -> bool:
    if not checks:
        return False
    nearest = min(checks, key=lambda item: abs(float(item["epoch"]) - sample_epoch))
    return bool(nearest["attached"]) and abs(float(nearest["epoch"]) - sample_epoch) <= 2.0


def build_telemetry_rows(
    replay_id: str,
    log_text: str,
    segments: list[dict[str, Any]],
    attachment_checks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ue_rows = parse_marker_lines(log_text, "UE_RADIO_DEBUG_V1")
    channel_rows = parse_marker_lines(log_text, "RFSIM_CHANNEL_DEBUG_V1")
    rows: list[dict[str, Any]] = []
    missing_channel = 0
    outside_segments = 0
    for utc_second, ue in sorted(ue_rows.items()):
        sample_epoch = utc_second + 0.5
        segment = next(
            (
                item
                for item in segments
                if float(item["applied_epoch"]) <= sample_epoch
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
        relative_time = float(segment["nominal_start_s"]) + (
            sample_epoch - float(segment["applied_epoch"])
        )
        row = {
            "replay_id": replay_id,
            "t_s": f"{relative_time:.6f}",
            "commanded_gain_db": f"{float(segment['commanded_gain_db']):.9f}",
            "applied_gain_db": f"{float(channel['applied_gain_db']):.9f}",
            "channel_snapshot_id": channel["channel_snapshot_id"],
            "channel_snapshot_timestamp_ns": channel["channel_snapshot_timestamp_ns"],
            "tap_energy_linear": channel["tap_energy_linear"],
            "noise_power_db": channel["noise_power_db"],
            "rsrp_digital_power_linear": ue["rsrp_digital_power_linear"],
            "rsrp_db_per_re_unquantized": ue["rsrp_db_per_re_unquantized"],
            "ss_rsrp_dbm_integer": ue["ss_rsrp_dbm_integer"],
            "ss_sinr_db": ue["ss_sinr_db"],
            "attached": str(nearest_attachment(sample_epoch, attachment_checks)).lower(),
        }
        numeric_values = [
            row[field]
            for field in TELEMETRY_FIELDS
            if field not in {"replay_id", "channel_snapshot_id", "attached"}
        ]
        if not all(math.isfinite(float(value)) for value in numeric_values):
            raise ReplayError(f"non-finite telemetry row: {row}")
        rows.append(row)
    diagnostics = {
        "ue_debug_seconds": len(ue_rows),
        "channel_debug_seconds": len(channel_rows),
        "matched_rows": len(rows),
        "missing_channel_rows": missing_channel,
        "outside_segment_rows": outside_segments,
    }
    return rows, diagnostics


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TELEMETRY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def run_one_replay(
    replay_number: int,
    compose_file: Path,
    override_file: Path,
    channel_helper: Path,
    output: Path,
    attach_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    replay_id = f"local-{replay_number}"
    compose(
        compose_file,
        override_file,
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        UE_SERVICE,
    )
    observed_image = docker_inspect("{{.Image}}", UE_CONTAINER)
    if observed_image != DEBUG_IMAGE_ID:
        raise ReplayError(f"debug UE image mismatch: {observed_image}")
    wait_attached(attach_timeout_seconds)
    channel_command(channel_helper, "show", "ploss")
    channel_command(channel_helper, "set", "noise_power_dB", FIXED_NOISE_DB)
    wait_for_markers(15.0)

    attachment_checks: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for index, (label, gain_db) in enumerate(ENVELOPE):
        result = channel_command(channel_helper, "set", "ploss", gain_db)
        segment = {
            "segment_index": index,
            "segment_label": label,
            "nominal_start_s": index * SEGMENT_SECONDS,
            "analysis_start_s": index * SEGMENT_SECONDS + SETTLING_SECONDS,
            "commanded_gain_db": gain_db,
            "applied_epoch": float(result["applied_epoch"]),
            "observed_gain_db": float(result["observed"]),
        }
        segments.append(segment)
        deadline = time.monotonic() + SEGMENT_SECONDS
        while time.monotonic() < deadline:
            attached = is_attached()
            attachment_checks.append({"epoch": time.time(), "attached": attached})
            if not attached:
                raise ReplayError(f"attachment lost during {replay_id} segment {label}")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(1.0, remaining))

    time.sleep(2.0)
    log_text = run_command("docker", "logs", UE_CONTAINER)
    raw_log = output / f"{replay_id}-ue.log"
    raw_log.write_text(log_text + "\n")
    write_json(output / f"{replay_id}-control.json", segments)
    write_json(output / f"{replay_id}-attachment.json", attachment_checks)
    rows, diagnostics = build_telemetry_rows(
        replay_id,
        log_text,
        segments,
        attachment_checks,
    )
    write_csv(output / f"{replay_id}-telemetry.csv", rows)
    if len(rows) < len(ENVELOPE) * 5:
        raise ReplayError(f"{replay_id} produced only {len(rows)} matched telemetry rows")
    summary = {
        "replay_id": replay_id,
        "debug_image_id": observed_image,
        "segments": segments,
        "diagnostics": diagnostics,
        "raw_log_sha256": sha256(raw_log),
        "continuous_attachment": all(item["attached"] for item in attachment_checks),
    }
    return rows, summary


def make_output(root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output = root / f"phase3c1-scalar-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def install_signal_handlers() -> None:
    def stop(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


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

    require_hash(compose_file, COMPOSE_SHA256)
    require_hash(channel_config, CHANNEL_CONFIG_SHA256)
    require_hash(ue_config, UE_CONFIG_SHA256)
    if not channel_helper.is_file():
        raise ReplayError(f"missing channel helper: {channel_helper}")
    if image_id(DEBUG_IMAGE) != DEBUG_IMAGE_ID:
        raise ReplayError("pinned debug image ID is unavailable")
    if docker_inspect("{{.Config.Image}}", UE_CONTAINER) != ORIGINAL_IMAGE:
        raise ReplayError("the live UE is not using the frozen original image")
    original_image_id = docker_inspect("{{.Image}}", UE_CONTAINER)
    original_gnb_restart_count = int(docker_inspect("{{.RestartCount}}", GNB_CONTAINER))
    if docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER) != "healthy":
        raise ReplayError("gNB is not healthy before the replay")

    override_file.parent.mkdir(parents=True, exist_ok=True)
    if override_file.exists() and override_file.read_text() != OVERRIDE_TEXT:
        raise ReplayError(f"refusing to overwrite a different compose override: {override_file}")
    override_file.write_text(OVERRIDE_TEXT)
    mutated = False
    error: str | None = None
    rollback: dict[str, Any] = {"attempted": False, "passed": False}
    summaries: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    try:
        mutated = True
        for replay_number in range(1, REPLAYS + 1):
            rows, summary = run_one_replay(
                replay_number,
                compose_file,
                override_file,
                channel_helper,
                output,
                args.attach_timeout_seconds,
            )
            all_rows.extend(rows)
            summaries.append(summary)
        write_csv(output / "deterministic_scalar_telemetry.csv", all_rows)
    except (KeyboardInterrupt, OSError, ReplayError, subprocess.SubprocessError) as exc:
        error = str(exc)
    finally:
        if mutated:
            rollback["attempted"] = True
            try:
                require_hash(compose_file, COMPOSE_SHA256)
                compose(
                    compose_file,
                    None,
                    "up",
                    "-d",
                    "--no-deps",
                    "--force-recreate",
                    UE_SERVICE,
                )
                wait_attached(args.attach_timeout_seconds)
                restored_id = docker_inspect("{{.Image}}", UE_CONTAINER)
                gnb_restarts = int(docker_inspect("{{.RestartCount}}", GNB_CONTAINER))
                rollback.update(
                    {
                        "restored_image_id": restored_id,
                        "expected_image_id": original_image_id,
                        "gnb_restart_count_before": original_gnb_restart_count,
                        "gnb_restart_count_after": gnb_restarts,
                        "passed": restored_id == original_image_id
                        and gnb_restarts == original_gnb_restart_count
                        and is_attached(),
                    }
                )
                if not rollback["passed"]:
                    raise ReplayError(f"rollback verification failed: {rollback}")
            except (OSError, ReplayError, subprocess.SubprocessError) as rollback_error:
                rollback["error"] = str(rollback_error)
                error = f"{error + '; ' if error else ''}rollback failed: {rollback_error}"

    state = {
        "schema_version": 1,
        "execution_completed": error is None and len(summaries) == REPLAYS,
        "error": error,
        "compose_sha256": sha256(compose_file),
        "channel_config_sha256": sha256(channel_config),
        "ue_config_sha256": sha256(ue_config),
        "debug_image": DEBUG_IMAGE,
        "debug_image_id": DEBUG_IMAGE_ID,
        "original_image": ORIGINAL_IMAGE,
        "original_image_id": original_image_id,
        "gNB_untouched": True,
        "replays": summaries,
        "rollback": rollback,
    }
    write_json(output / "execution_state.json", state)
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"TELEMETRY={output / 'deterministic_scalar_telemetry.csv'}", flush=True)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--compose-file", default="/local/repository/etc/docker-compose-cell1.yaml")
    root.add_argument("--channel-config", default="/local/repository/etc/channelmod-cell1.conf")
    root.add_argument("--ue-config", default="/local/repository/etc/nr-ue-cell1-1.conf")
    root.add_argument("--channel-helper", default="/local/repository/bin/channel-cell.py")
    root.add_argument("--override-file", default="/local/phase3c/phase3c1-ue-debug.override.yaml")
    root.add_argument("--output-root", default="/local/logs/phase3c1")
    root.add_argument("--attach-timeout-seconds", type=float, default=180.0)
    return root


def main() -> int:
    install_signal_handlers()
    try:
        return execute(parser().parse_args())
    except (OSError, ReplayError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
