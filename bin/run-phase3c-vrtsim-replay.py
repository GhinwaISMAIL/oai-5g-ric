#!/usr/bin/env python3
"""Run the frozen Phase 3C one-gNB/one-UE CIRDB safety replay."""

from __future__ import annotations

import argparse
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

from phase3c_log_parser import parse_replay_logs

COMPOSE_SHA256 = "db5aade37a4613a95c3f9682cdddf3bc5bc73d74f398c004105547c80b8d0260"
TRACE_PROFILES = {
    "tdl_b_full_cycle": {
        "binary_sha256": "cb5b85bfdfe12768462b482c21145fe43618468a0bff9248beb5c9b5dea82550",
        "sidecar_sha256": "03b5031e81ea721501692e73e8ea908a57ae98e1814cee43887908da3dbf292c",
        "snapshots": 150_000,
    },
    "identity_l8_control": {
        "binary_sha256": "ae5140b4f95bf59256e1f82bc650f9391c3b06f5b7adf63bd92497a9c7c5bfc2",
        "sidecar_sha256": "51e3157cb5c6aaa00a06b5bd00680c0acc51fa4ae7a1694ba0b0d671063c3cba",
        "snapshots": 1,
    },
}
OAI_REVISION = "70508ebaf52f2aae420566d380c6537f2efb9f0c"
UE_IMAGE = "oai-nr-ue-phase3c-vrtsim:70508eb"
GNB_IMAGE = "oai-gnb-phase3c-vrtsim:70508eb"
UE_CONTAINER = "ric5g-ue-cell1-1"
GNB_CONTAINER = "ric5g-gnb-cell1"
UE_SERVICE = "oai-nr-ue1"
GNB_SERVICE = "oai-gnb"
REPETITIONS = 2
OBSERVATION_SECONDS = 330.0
MINIMUM_TELEMETRY_SECONDS = 300.0
TRACE_SNAPSHOTS = 150_000
MAX_SKIPPED_FRACTION = 0.01
MAX_CONSECUTIVE_SKIPPED = 10
MAX_PING_LOSS_FRACTION = 0.05
DN_IP = "192.168.72.135"
DN_SUBNET = "192.168.72.128/26"
KEY_VALUE = re.compile(r"([A-Za-z0-9_]+)=([^\s]+)")
PING_SUMMARY = re.compile(
    r"(\d+) packets transmitted,\s*(\d+) (?:packets )?received,.*?(\d+(?:\.\d+)?)% packet loss",
    re.DOTALL,
)


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
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ReplayError(f"missing or unsafe frozen input: {path}")
    observed = sha256(path)
    if observed != expected:
        raise ReplayError(f"checksum mismatch for {path}: expected {expected}, observed {observed}")


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def docker_inspect(template: str, target: str) -> str:
    return run_command("docker", "inspect", "-f", template, target)


def image_inspect(template: str, target: str) -> str:
    return run_command("docker", "image", "inspect", "-f", template, target)


def compose(compose_file: Path, override_file: Path | None, *args: str) -> str:
    command = ["docker", "compose", "-f", str(compose_file)]
    if override_file is not None:
        command.extend(("-f", str(override_file)))
    command.extend(args)
    return run_command(*command)


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


def wait_gnb_healthy(timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER) == "healthy":
            return
        time.sleep(2.0)
    raise ReplayError(f"{GNB_CONTAINER} was not healthy within {timeout_seconds:.0f} seconds")


def render_override(
    trace_dir: Path,
    shared_tmp: Path,
    *,
    channel_mode: str = "cirdb",
    server_timescale: float = 1.0,
    gnb_min_rxtxtime: int = 3,
) -> str:
    if channel_mode not in {"cirdb", "passthrough"}:
        raise ReplayError(f"unsupported channel mode: {channel_mode}")
    channel_options = ""
    trace_volume = ""
    if channel_mode == "cirdb":
        channel_options = (
            " --vrtsim.cirdb 1 --vrtsim.cirdb-path /cirdb"
            " --vrtsim.cirdb_model_id 1 --vrtsim.cirdb_ds_ns 30"
            " --vrtsim.cirdb_speed_mps 1.5"
        )
        trace_volume = f"\n      - {trace_dir}:/cirdb:ro"
    return f'''services:
  {GNB_SERVICE}:
    image: {GNB_IMAGE}
    ipc: host
    environment:
      TZ: Europe/Paris
      ASAN_OPTIONS: detect_leaks=0
      USE_ADDITIONAL_OPTIONS: "-E --telnetsrv --device.name vrtsim --vrtsim.role server --vrtsim.timescale {server_timescale:g}{channel_options} --vrtsim.num_ues 1 --gNBs.[0].min_rxtxtime {gnb_min_rxtxtime} --log_config.global_log_options level,nocolor,time"
    volumes:
      - {shared_tmp}:/tmp{trace_volume}
  {UE_SERVICE}:
    image: {UE_IMAGE}
    ipc: host
    environment:
      TZ: Europe/Paris
      ASAN_OPTIONS: detect_leaks=0
      USE_ADDITIONAL_OPTIONS: "-E --telnetsrv --device.name vrtsim --vrtsim.role client --ue-nb-ant-rx 1 --ue-nb-ant-tx 1 -r 106 --numerology 1 --uicc0.imsi 208990100001100 -C 3319680000 --log_config.global_log_options level,nocolor,time"
    volumes:
      - {shared_tmp}:/tmp
'''


def parse_cirdb_debug(log_text: str) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for line in log_text.splitlines():
        position = line.find("VRTSIM_CIRDB_DEBUG_V1")
        if position < 0:
            continue
        fields = dict(KEY_VALUE.findall(line[position:]))
        required = {
            "elapsed_second",
            "expected_cirdb_step",
            "current_cirdb_snapshot_index",
            "applied_cirdb_updates",
            "skipped_cirdb_snapshots",
            "maximum_consecutive_skipped_cirdb_snapshots",
            "current_tap_energy_linear",
        }
        if not required.issubset(fields):
            raise ReplayError(f"incomplete CIRDB debug row: {line}")
        row = {name: float(fields[name]) for name in required}
        if not all(math.isfinite(value) for value in row.values()):
            raise ReplayError(f"non-finite CIRDB debug row: {line}")
        rows.append(row)
    return rows


def parse_vrtsim_runtime_debug(log_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    integer_fields = {
        "elapsed_second",
        "tx_timestamp",
        "current_sample",
        "tx_samples_late",
        "tx_samples_total",
        "rx_samples_late",
        "rx_samples_total",
        "tx_early",
        "rx_early",
    }
    float_fields = {"channel_processing_us", "average_tx_budget_us"}
    required = integer_fields | float_fields | {"role"}
    for line in log_text.splitlines():
        position = line.find("VRTSIM_RUNTIME_DEBUG_V1")
        if position < 0:
            continue
        fields = dict(KEY_VALUE.findall(line[position:]))
        if not required.issubset(fields):
            raise ReplayError(f"incomplete VRTSIM runtime debug row: {line}")
        row: dict[str, Any] = {"role": fields["role"]}
        row.update({name: int(fields[name]) for name in integer_fields})
        row.update({name: float(fields[name]) for name in float_fields})
        if row["role"] not in {"server", "client"}:
            raise ReplayError(f"invalid VRTSIM runtime role: {line}")
        if not all(math.isfinite(row[name]) for name in float_fields):
            raise ReplayError(f"non-finite VRTSIM runtime debug row: {line}")
        rows.append(row)
    return rows


def parse_ping_summary(text: str) -> dict[str, float]:
    match = PING_SUMMARY.search(text)
    if not match:
        raise ReplayError(f"cannot parse ping summary: {text[-500:]}")
    transmitted = int(match.group(1))
    received = int(match.group(2))
    loss_fraction = float(match.group(3)) / 100.0
    if transmitted <= 0 or received > transmitted:
        raise ReplayError("invalid ping counters")
    return {
        "packets_transmitted": transmitted,
        "packets_received": received,
        "packet_loss_fraction": loss_fraction,
    }


def evaluate_replay(
    *,
    ue_log: str,
    gnb_log: str,
    attachment_checks: list[dict[str, Any]],
    ping_output: str,
    startup_ue_log: str | None = None,
    startup_gnb_log: str | None = None,
    observation_ue_log: str | None = None,
    observation_gnb_log: str | None = None,
    require_cirdb: bool = True,
    minimum_telemetry_seconds: float = MINIMUM_TELEMETRY_SECONDS,
    trace_snapshots: int = TRACE_SNAPSHOTS,
) -> dict[str, Any]:
    if (observation_ue_log is None) != (observation_gnb_log is None):
        raise ReplayError("both observation log suffixes must be supplied together")
    if (startup_ue_log is None) != (startup_gnb_log is None):
        raise ReplayError("both startup log prefixes must be supplied together")
    failure_ue_log = ue_log if observation_ue_log is None else observation_ue_log
    failure_gnb_log = gnb_log if observation_gnb_log is None else observation_gnb_log
    if observation_ue_log is None:
        log_result = parse_replay_logs(ue_log, gnb_log)
    else:
        log_result = parse_replay_logs(
            ue_log,
            gnb_log,
            ue_failure_text=observation_ue_log,
            gnb_failure_text=observation_gnb_log,
        )
    startup_result = parse_replay_logs(
        ue_log if startup_ue_log is None else startup_ue_log,
        gnb_log if startup_gnb_log is None else startup_gnb_log,
    )
    cirdb = parse_cirdb_debug(failure_gnb_log) if require_cirdb else []
    if require_cirdb and len(cirdb) < 2:
        raise ReplayError("fewer than two CIRDB debug rows")
    if require_cirdb:
        elapsed = cirdb[-1]["elapsed_second"] - cirdb[0]["elapsed_second"]
        step_span = (
            cirdb[-1]["expected_cirdb_step"] - cirdb[0]["expected_cirdb_step"]
        )
        skipped_delta = (
            cirdb[-1]["skipped_cirdb_snapshots"]
            - cirdb[0]["skipped_cirdb_snapshots"]
        )
        skipped_fraction = skipped_delta / step_span if step_span > 0 else math.inf
        cycle_coverage = min(1.0, step_span / trace_snapshots)
        maximum_gap = max(
            row["maximum_consecutive_skipped_cirdb_snapshots"] for row in cirdb
        )
    else:
        elapsed = (
            attachment_checks[-1]["epoch"] - attachment_checks[0]["epoch"]
            if len(attachment_checks) >= 2
            else 0.0
        )
        step_span = None
        skipped_delta = None
        skipped_fraction = None
        cycle_coverage = None
        maximum_gap = None
    radio_debug_rows = failure_ue_log.count("UE_RADIO_DEBUG_V1")
    ping = parse_ping_summary(ping_output)
    attachment_fraction = (
        sum(bool(row["attached"]) for row in attachment_checks) / len(attachment_checks)
        if attachment_checks
        else 0.0
    )
    gates = {
        "log_markers": bool(log_result["log_gate_pass"]),
        "attachment": attachment_fraction == 1.0,
        "usable_duration": elapsed >= minimum_telemetry_seconds,
        "ue_radio_debug_rows": radio_debug_rows >= int(minimum_telemetry_seconds),
        "ping_loss": ping["packet_loss_fraction"] <= MAX_PING_LOSS_FRACTION,
    }
    if require_cirdb:
        gates.update(
            {
                "cirdb_debug_rows": len(cirdb) >= int(minimum_telemetry_seconds),
                "trace_cycle_coverage": cycle_coverage is not None
                and cycle_coverage >= 0.99,
                "skipped_snapshot_fraction": skipped_fraction is not None
                and skipped_fraction <= MAX_SKIPPED_FRACTION,
                "maximum_consecutive_skipped": maximum_gap is not None
                and maximum_gap <= MAX_CONSECUTIVE_SKIPPED,
                "tap_energy_positive": all(
                    row["current_tap_energy_linear"] > 0 for row in cirdb
                ),
            }
        )
    gates = {name: bool(value) for name, value in gates.items()}
    return {
        "log_parser": log_result,
        "startup_log_diagnostics": startup_result,
        "attachment_checks": len(attachment_checks),
        "attachment_fraction": attachment_fraction,
        "cirdb_debug_rows": len(cirdb),
        "ue_radio_debug_rows": radio_debug_rows,
        "usable_telemetry_duration_s": elapsed,
        "trace_step_span": step_span,
        "trace_cycle_coverage_fraction": cycle_coverage,
        "skipped_snapshot_delta": skipped_delta,
        "skipped_snapshot_fraction": skipped_fraction,
        "maximum_consecutive_skipped_snapshots": maximum_gap,
        "ping": ping,
        "gate_results": gates,
        "replay_pass": all(gates.values()),
    }


def _critical_failure_seen(ue_log: str, gnb_log: str) -> bool:
    result = parse_replay_logs(ue_log, gnb_log)
    return any(
        count > 0
        for domain in result["failure_marker_counts"].values()
        for count in domain.values()
    )


def _log_suffix(current: str, prefix: str, label: str) -> str:
    if not current.startswith(prefix):
        raise ReplayError(f"{label} log stream was not append-only")
    return current[len(prefix) :]


def run_one_replay(
    replay_number: int,
    compose_file: Path,
    override_file: Path,
    shared_tmp: Path,
    output: Path,
    attach_timeout_seconds: float,
    observation_seconds: float = OBSERVATION_SECONDS,
    minimum_telemetry_seconds: float = MINIMUM_TELEMETRY_SECONDS,
    require_cirdb: bool = True,
    stabilization_seconds: float = 0.0,
    trace_snapshots: int = TRACE_SNAPSHOTS,
) -> dict[str, Any]:
    replay_id = f"vrtsim-{replay_number}"
    ping: subprocess.Popen[str] | None = None
    ping_output = ""
    attachment_checks: list[dict[str, Any]] = []
    try:
        (shared_tmp / "vrtsim_connection").unlink(missing_ok=True)
        compose(
            compose_file,
            override_file,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            GNB_SERVICE,
            UE_SERVICE,
        )
        wait_gnb_healthy(attach_timeout_seconds)
        wait_attached(attach_timeout_seconds)
        run_command(
            "docker",
            "exec",
            UE_CONTAINER,
            "ip",
            "route",
            "replace",
            DN_SUBNET,
            "dev",
            "oaitun_ue1",
        )
        stabilization_deadline = time.monotonic() + stabilization_seconds
        while time.monotonic() < stabilization_deadline:
            if not is_attached():
                raise ReplayError(f"attachment lost during {replay_id} stabilization")
            if docker_inspect("{{.State.Status}}", GNB_CONTAINER) != "running":
                raise ReplayError("gNB container stopped during stabilization")
            if docker_inspect("{{.State.Status}}", UE_CONTAINER) != "running":
                raise ReplayError("UE container stopped during stabilization")
            if int(docker_inspect("{{.RestartCount}}", GNB_CONTAINER)) != 0:
                raise ReplayError("gNB container restarted during stabilization")
            if int(docker_inspect("{{.RestartCount}}", UE_CONTAINER)) != 0:
                raise ReplayError("UE container restarted during stabilization")
            time.sleep(
                min(1.0, max(0.0, stabilization_deadline - time.monotonic()))
            )
        observation_start_ue_log = run_command("docker", "logs", UE_CONTAINER)
        observation_start_gnb_log = run_command("docker", "logs", GNB_CONTAINER)
        ping = subprocess.Popen(
            (
                "docker",
                "exec",
                UE_CONTAINER,
                "ping",
                "-I",
                "oaitun_ue1",
                "-i",
                "1",
                "-w",
                str(int(observation_seconds)),
                DN_IP,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + observation_seconds
        while time.monotonic() < deadline:
            attached = is_attached()
            attachment_checks.append({"epoch": time.time(), "attached": attached})
            if not attached:
                raise ReplayError(f"attachment lost during {replay_id}")
            if docker_inspect("{{.State.Status}}", GNB_CONTAINER) != "running":
                raise ReplayError("gNB container stopped")
            if docker_inspect("{{.State.Status}}", UE_CONTAINER) != "running":
                raise ReplayError("UE container stopped")
            if int(docker_inspect("{{.RestartCount}}", GNB_CONTAINER)) != 0:
                raise ReplayError("gNB container restarted")
            if int(docker_inspect("{{.RestartCount}}", UE_CONTAINER)) != 0:
                raise ReplayError("UE container restarted")
            ue_log = run_command("docker", "logs", UE_CONTAINER)
            gnb_log = run_command("docker", "logs", GNB_CONTAINER)
            observation_ue_log = _log_suffix(
                ue_log, observation_start_ue_log, "UE"
            )
            observation_gnb_log = _log_suffix(
                gnb_log, observation_start_gnb_log, "gNB"
            )
            if _critical_failure_seen(observation_ue_log, observation_gnb_log):
                raise ReplayError("critical PBCH/PUSCH/RA/RLF/sync marker observed")
            rows = parse_cirdb_debug(observation_gnb_log)
            if rows and rows[-1]["maximum_consecutive_skipped_cirdb_snapshots"] > (
                MAX_CONSECUTIVE_SKIPPED
            ):
                raise ReplayError("CIRDB consecutive-snapshot gap exceeded the limit")
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        try:
            ping_output, _ = ping.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            ping.terminate()
            ping_output, _ = ping.communicate(timeout=10)
        ue_log = run_command("docker", "logs", UE_CONTAINER)
        gnb_log = run_command("docker", "logs", GNB_CONTAINER)
        observation_ue_log = _log_suffix(ue_log, observation_start_ue_log, "UE")
        observation_gnb_log = _log_suffix(gnb_log, observation_start_gnb_log, "gNB")
        evaluation = evaluate_replay(
            ue_log=ue_log,
            gnb_log=gnb_log,
            attachment_checks=attachment_checks,
            ping_output=ping_output,
            startup_ue_log=observation_start_ue_log,
            startup_gnb_log=observation_start_gnb_log,
            observation_ue_log=observation_ue_log,
            observation_gnb_log=observation_gnb_log,
            require_cirdb=require_cirdb,
            minimum_telemetry_seconds=minimum_telemetry_seconds,
            trace_snapshots=trace_snapshots,
        )
        write_json(output / f"{replay_id}-evaluation.json", evaluation)
        if not evaluation["replay_pass"]:
            raise ReplayError(f"{replay_id} failed: {evaluation['gate_results']}")
        return evaluation
    finally:
        if ping is not None and not ping_output:
            if ping.poll() is None:
                ping.terminate()
            try:
                ping_output, _ = ping.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                ping.kill()
                ping_output, _ = ping.communicate(timeout=10)
        ue_log = run_command("docker", "logs", UE_CONTAINER, check=False)
        gnb_log = run_command("docker", "logs", GNB_CONTAINER, check=False)
        (output / f"{replay_id}-ue.log").write_text(ue_log + "\n")
        (output / f"{replay_id}-gnb.log").write_text(gnb_log + "\n")
        (output / f"{replay_id}-ping.log").write_text(ping_output + "\n")
        write_json(output / f"{replay_id}-attachment.json", attachment_checks)
        write_json(
            output / f"{replay_id}-runtime.json",
            parse_vrtsim_runtime_debug(gnb_log),
        )


def make_output(root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output = root / f"phase3c4-vrtsim-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def execute(args: argparse.Namespace) -> int:
    if hasattr(sys, "geteuid") and sys.geteuid() != 0:
        raise ReplayError("run this command as root")
    if args.server_timescale <= 0:
        raise ReplayError("server timescale must be positive")
    if args.repetitions <= 0:
        raise ReplayError("repetitions must be positive")
    if args.gnb_min_rxtxtime <= 0:
        raise ReplayError("gNB min_rxtxtime must be positive")
    if args.observation_seconds <= 0:
        raise ReplayError("observation seconds must be positive")
    if args.stabilization_seconds < 0:
        raise ReplayError("stabilization seconds must not be negative")
    if not 0 < args.minimum_telemetry_seconds <= args.observation_seconds:
        raise ReplayError(
            "minimum telemetry seconds must be positive and no greater than observation seconds"
        )
    compose_file = Path(args.compose_file).resolve()
    trace_dir = Path(args.trace_dir).resolve()
    override_file = Path(args.override_file).resolve()
    shared_tmp = Path(args.shared_tmp).resolve()
    output = make_output(Path(args.output_root).resolve())
    print(f"OUTPUT_DIR={output}", flush=True)

    require_hash(compose_file, COMPOSE_SHA256)
    trace_profile = TRACE_PROFILES[args.trace_profile]
    if args.channel_mode == "cirdb":
        require_hash(trace_dir / "cir_db.bin", trace_profile["binary_sha256"])
        require_hash(trace_dir / "vrtsim.yaml", trace_profile["sidecar_sha256"])
    for reference, expected_id in (
        (UE_IMAGE, args.expected_ue_image_id),
        (GNB_IMAGE, args.expected_gnb_image_id),
    ):
        if image_inspect("{{.Id}}", reference) != expected_id:
            raise ReplayError(f"image ID mismatch: {reference}")
        if image_inspect("{{.Architecture}}", reference) != "amd64":
            raise ReplayError(f"image architecture mismatch: {reference}")
        if image_inspect('{{index .Config.Labels "org.opencontainers.image.revision"}}', reference) != OAI_REVISION:
            raise ReplayError(f"image revision mismatch: {reference}")

    original_ue_id = docker_inspect("{{.Image}}", UE_CONTAINER)
    original_gnb_id = docker_inspect("{{.Image}}", GNB_CONTAINER)
    original_gnb_restarts = int(docker_inspect("{{.RestartCount}}", GNB_CONTAINER))
    shared_tmp.mkdir(parents=True, exist_ok=True)
    override_file.parent.mkdir(parents=True, exist_ok=True)
    override = render_override(
        trace_dir,
        shared_tmp,
        channel_mode=args.channel_mode,
        server_timescale=args.server_timescale,
        gnb_min_rxtxtime=args.gnb_min_rxtxtime,
    )
    if override_file.exists() and override_file.read_text() != override:
        raise ReplayError(f"refusing to overwrite a different override: {override_file}")
    override_file.write_text(override)

    mutated = False
    error: str | None = None
    evaluations: list[dict[str, Any]] = []
    rollback: dict[str, Any] = {"attempted": False, "passed": False}
    try:
        mutated = True
        for replay_number in range(1, args.repetitions + 1):
            evaluations.append(
                run_one_replay(
                    replay_number,
                    compose_file,
                    override_file,
                    shared_tmp,
                    output,
                    args.attach_timeout_seconds,
                    args.observation_seconds,
                    args.minimum_telemetry_seconds,
                    args.channel_mode == "cirdb",
                    args.stabilization_seconds,
                    trace_profile["snapshots"],
                )
            )
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
                    GNB_SERVICE,
                    UE_SERVICE,
                )
                wait_gnb_healthy(args.attach_timeout_seconds)
                wait_attached(args.attach_timeout_seconds)
                restored_ue = docker_inspect("{{.Image}}", UE_CONTAINER)
                restored_gnb = docker_inspect("{{.Image}}", GNB_CONTAINER)
                restored_restarts = int(docker_inspect("{{.RestartCount}}", GNB_CONTAINER))
                rollback.update({
                    "original_ue_image_id": original_ue_id,
                    "restored_ue_image_id": restored_ue,
                    "original_gnb_image_id": original_gnb_id,
                    "restored_gnb_image_id": restored_gnb,
                    "gnb_restart_count_before": original_gnb_restarts,
                    "gnb_restart_count_after": restored_restarts,
                    "passed": restored_ue == original_ue_id
                    and restored_gnb == original_gnb_id
                    and restored_restarts == original_gnb_restarts
                    and is_attached(),
                })
                if not rollback["passed"]:
                    raise ReplayError(f"rollback verification failed: {rollback}")
            except (OSError, ReplayError, subprocess.SubprocessError) as rollback_error:
                rollback["error"] = str(rollback_error)
                error = f"{error + '; ' if error else ''}rollback failed: {rollback_error}"

    state = {
        "schema_version": 1,
        "execution_completed": error is None and len(evaluations) == args.repetitions,
        "error": error,
        "trace_binary_sha256": sha256(trace_dir / "cir_db.bin")
        if args.channel_mode == "cirdb"
        else None,
        "trace_sidecar_sha256": sha256(trace_dir / "vrtsim.yaml")
        if args.channel_mode == "cirdb"
        else None,
        "ue_image": UE_IMAGE,
        "ue_image_id": args.expected_ue_image_id,
        "gnb_image": GNB_IMAGE,
        "gnb_image_id": args.expected_gnb_image_id,
        "channel_mode": args.channel_mode,
        "trace_profile": args.trace_profile if args.channel_mode == "cirdb" else None,
        "server_timescale": args.server_timescale,
        "gnb_min_rxtxtime": args.gnb_min_rxtxtime,
        "repetitions_required": args.repetitions,
        "observation_seconds": args.observation_seconds,
        "minimum_telemetry_seconds": args.minimum_telemetry_seconds,
        "stabilization_seconds": args.stabilization_seconds,
        "replays": evaluations,
        "rollback": rollback,
    }
    write_json(output / "execution_state.json", state)
    if error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--compose-file", default="/local/repository/etc/docker-compose-cell1.yaml")
    root.add_argument("--trace-dir", default="/local/phase3c/cirdb-v1")
    root.add_argument("--override-file", default="/local/phase3c/phase3c4-vrtsim.override.yaml")
    root.add_argument("--shared-tmp", default="/local/phase3c/vrtsim-tmp")
    root.add_argument("--output-root", default="/local/logs/phase3c4")
    root.add_argument("--attach-timeout-seconds", type=float, default=180.0)
    root.add_argument(
        "--channel-mode", choices=("cirdb", "passthrough"), default="cirdb"
    )
    root.add_argument(
        "--trace-profile",
        choices=tuple(TRACE_PROFILES),
        default="tdl_b_full_cycle",
    )
    root.add_argument("--server-timescale", type=float, default=1.0)
    root.add_argument("--gnb-min-rxtxtime", type=int, default=3)
    root.add_argument("--repetitions", type=int, default=REPETITIONS)
    root.add_argument("--observation-seconds", type=float, default=OBSERVATION_SECONDS)
    root.add_argument("--stabilization-seconds", type=float, default=0.0)
    root.add_argument(
        "--minimum-telemetry-seconds",
        type=float,
        default=MINIMUM_TELEMETRY_SECONDS,
    )
    root.add_argument("--expected-ue-image-id", required=True)
    root.add_argument("--expected-gnb-image-id", required=True)
    return root


def install_signal_handlers() -> None:
    def stop(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def main() -> int:
    install_signal_handlers()
    try:
        return execute(parser().parse_args())
    except (OSError, ReplayError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
