#!/usr/bin/env python3
"""Run the frozen 60-second Phase 3I gain/noise command trace."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TypeVar

PHASE3H_SCRIPT = Path(__file__).with_name("run-phase3h-dynamic-staircase.py")
PHASE3H_SPEC = importlib.util.spec_from_file_location("phase3i_phase3h_support", PHASE3H_SCRIPT)
if PHASE3H_SPEC is None or PHASE3H_SPEC.loader is None:
    raise RuntimeError(f"cannot load Phase 3H support: {PHASE3H_SCRIPT}")
PHASE3H = importlib.util.module_from_spec(PHASE3H_SPEC)
PHASE3H_SPEC.loader.exec_module(PHASE3H)
SUPPORT = PHASE3H.SUPPORT
BASE = PHASE3H.BASE
PARSER = PHASE3H.PARSER
ValidationError = PHASE3H.ValidationError
SessionT = TypeVar("SessionT", bound="PersistentChannelSession")

OAI_REVISION = "70508ebaf52f2aae420566d380c6537f2efb9f0c"
RESEARCH_REVISION = "b36a41e4289c2a2635f25efc5aeba607d1a0d5ce"
RESEARCH_PROTOCOL_SHA256 = (
    "34bdd27b0f5ba4f3c6aa44bb9ae5ce678235072a6fe0f9491d8f0f84895e71d2"
)
EXPECTED_COMMANDS_SHA256 = (
    "92025f840ef690c76ad016ad3c9b22c66474c72758c4baabf00ae140b6afe6bf"
)
DEBUG_IMAGE = "oai-nr-ue-rfsim-phase3h:70508eb"
ORIGINAL_IMAGE = SUPPORT.ORIGINAL_IMAGE
CHANNEL_FAMILY = "AWGN"
OAI_RNG_SEED = 46001
ATTACH_GAIN_DB = 0.0
ATTACH_NOISE_DB = -60.0
ANCHOR_GAIN_DB = -10.0
ANCHOR_NOISE_DB = -25.0
POST_ATTACH_STABILIZATION_SECONDS = 5.0
ANCHOR_SETTLING_SECONDS = 5.0
ANCHOR_USABLE_SECONDS = 10.0
COMMAND_INTERVAL_SECONDS = 1.0
COMMAND_START_LEAD_SECONDS = 2.0
MAXIMUM_COMMAND_COMPLETION_LATENESS_SECONDS = 0.5
MINIMUM_TRACE_ROWS = 55
PING_INTERVAL_COMMANDS = 10
UE_CONTAINER = BASE.UE_CONTAINER
GNB_CONTAINER = BASE.GNB_CONTAINER
UE_SERVICE = BASE.UE_SERVICE

COMMAND_FIELDS = (
    "command_index",
    "trace_row_index",
    "trace_time_bin",
    "trace_t_s",
    "target_relative_rsrp_db",
    "target_sinr_db",
    "projected_relative_rsrp_db",
    "projected_sinr_db",
    "commanded_gain_db",
    "commanded_noise_power_db",
    "clipped",
    "clipping_distance_scaled",
    "triangle_index",
    "vertex_0",
    "vertex_1",
    "vertex_2",
    "barycentric_0",
    "barycentric_1",
    "barycentric_2",
)
TRACE_FIELDS = (
    "command_index",
    "trace_row_index",
    "trace_time_bin",
    "trace_t_s",
    "target_relative_rsrp_db",
    "target_sinr_db",
    "projected_relative_rsrp_db",
    "projected_sinr_db",
    "commanded_gain_db",
    "commanded_noise_power_db",
    "clipped",
    "scheduled_epoch",
    "command_complete_epoch",
    "command_completion_lateness_seconds",
    "sample_utc_second",
    "sample_midpoint_epoch",
    "applied_gain_db",
    "applied_noise_power_db",
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
ANCHOR_FIELDS = (
    "anchor_type",
    "utc_second",
    "rsrp_db_per_re_unquantized",
    "ss_sinr_db",
    "applied_gain_db",
    "applied_noise_power_db",
    "channel_model_name",
    "channel_length",
    "nb_taps",
    "tap_energy_linear",
)


def load_commands(path: Path) -> list[dict[str, Any]]:
    SUPPORT.require_hash(path, EXPECTED_COMMANDS_SHA256)
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != COMMAND_FIELDS:
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
                "projected_relative_rsrp_db": float(row["projected_relative_rsrp_db"]),
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
    if len(rows) != 60 or [row["command_index"] for row in rows] != list(range(60)):
        raise ValidationError("the Phase 3I command trace must contain indices 0 through 59")
    if any(row["clipped"] for row in rows):
        raise ValidationError("the representative short trace must contain no clipped rows")
    if [row["trace_row_index"] for row in rows] != list(range(154, 214)):
        raise ValidationError("the representative trace rows changed")
    if any(
        not -18.0 <= row["commanded_gain_db"] <= 0.0
        or not -35.0 <= row["commanded_noise_power_db"] <= -17.0
        for row in rows
    ):
        raise ValidationError("a command exceeds the validated control envelope")
    return rows


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _wait_until(epoch: float, label: str) -> None:
    while True:
        remaining = epoch - time.time()
        if remaining <= 0:
            return
        if not BASE.is_attached():
            raise ValidationError(f"attachment lost while waiting for {label}")
        time.sleep(min(0.2, remaining))


class PersistentChannelSession:
    def __init__(self, channel_helper: Path) -> None:
        spec = importlib.util.spec_from_file_location(
            "phase3i_channel_helper", channel_helper
        )
        if spec is None or spec.loader is None:
            raise ValidationError(f"cannot load channel helper: {channel_helper}")
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        container, address, index = self.helper.endpoint(1, "dl", 1)
        if index != 0 or not self.helper.container_running(container):
            raise ValidationError("the active downlink channel endpoint is unavailable")
        self.address = address
        self.index = index
        self.sock: socket.socket | None = None

    def __enter__(self: SessionT) -> SessionT:  # noqa: PYI019
        self.sock = socket.create_connection((self.address, 9090), timeout=1.0)
        identity = self._show()
        name, model_type = self.helper.model_identity(identity, self.index)
        if name != "rfsimu_channel_enB0" or model_type != CHANNEL_FAMILY:
            raise ValidationError(
                f"unexpected persistent channel identity: {name} {model_type}"
            )
        return self

    def __exit__(self, *_: object) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _execute(self, command: str) -> str:
        if self.sock is None:
            raise ValidationError("persistent channel session is not connected")
        self.sock.sendall(command.encode() + b"\n")
        chunks: list[bytes] = []
        deadline = time.monotonic() + 0.35
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError as error:
                raise ValidationError(
                    f"channel command prompt timeout: {command}"
                ) from error
            if not chunk:
                raise ValidationError("persistent channel session closed unexpectedly")
            chunks.append(chunk)
            text = b"".join(chunks).decode(errors="replace")
            if text.rstrip().endswith(">"):
                return text
        raise ValidationError(f"channel command prompt timeout: {command}")

    def _show(self) -> str:
        return self._execute("channelmod show current")

    def set_controls(self, gain_db: float, noise_db: float) -> dict[str, Any]:
        self._execute(f"channelmod modify {self.index} ploss {gain_db}")
        self._execute(f"channelmod modify {self.index} noise_power_dB {noise_db}")
        output = self._show()
        name, model_type = self.helper.model_identity(output, self.index)
        observed_gain = self.helper.observed_value(output, self.index, "ploss")
        observed_noise = self.helper.observed_value(
            output, self.index, "noise_power_dB"
        )
        if name != "rfsimu_channel_enB0" or model_type != CHANNEL_FAMILY:
            raise ValidationError(f"unexpected active channel identity: {name} {model_type}")
        if not math.isclose(observed_gain, gain_db, rel_tol=1e-6, abs_tol=1e-6):
            raise ValidationError(
                f"persistent gain verification failed: {gain_db} != {observed_gain}"
            )
        if not math.isclose(observed_noise, noise_db, rel_tol=1e-6, abs_tol=1e-6):
            raise ValidationError(
                f"persistent noise verification failed: {noise_db} != {observed_noise}"
            )
        applied_epoch = time.time()
        return {
            "gain": {
                "model_index": self.index,
                "model_name": name,
                "model_type": model_type,
                "parameter": "ploss",
                "requested": gain_db,
                "observed": observed_gain,
                "verified": True,
                "applied_epoch": applied_epoch,
            },
            "noise": {
                "model_index": self.index,
                "model_name": name,
                "model_type": model_type,
                "parameter": "noise_power_dB",
                "requested": noise_db,
                "observed": observed_noise,
                "verified": True,
                "applied_epoch": applied_epoch,
            },
        }


def run_command_trace(
    commands: list[dict[str, Any]],
    channel_session: PersistentChannelSession,
    command_events: list[dict[str, Any]],
    ping_checks: list[dict[str, Any]],
) -> tuple[float, float]:
    start_epoch = float(math.ceil(time.time()) + int(COMMAND_START_LEAD_SECONDS))
    for command in commands:
        scheduled_epoch = start_epoch + command["command_index"] * COMMAND_INTERVAL_SECONDS
        _wait_until(scheduled_epoch, f"command {command['command_index']}")
        result = channel_session.set_controls(
            command["commanded_gain_db"],
            command["commanded_noise_power_db"],
        )
        completion_epoch = time.time()
        lateness = completion_epoch - scheduled_epoch
        event = {
            **command,
            "scheduled_epoch": scheduled_epoch,
            "command_complete_epoch": completion_epoch,
            "command_completion_lateness_seconds": lateness,
            "sample_utc_second": math.floor(scheduled_epoch),
            "gain_result": result["gain"],
            "noise_result": result["noise"],
        }
        command_events.append(event)
        if lateness > MAXIMUM_COMMAND_COMPLETION_LATENESS_SECONDS:
            raise ValidationError(
                f"command {command['command_index']} completed {lateness:.6f}s late"
            )
        if completion_epoch > math.floor(scheduled_epoch) + 0.5:
            raise ValidationError(
                f"command {command['command_index']} missed its sample midpoint"
            )
        if not BASE.is_attached():
            raise ValidationError(f"attachment lost after command {command['command_index']}")
        if (command["command_index"] + 1) % PING_INTERVAL_COMMANDS == 0:
            ping_checks.append(
                {
                    "command_index": command["command_index"],
                    "epoch": time.time(),
                    "passed": SUPPORT.ping_once(),
                }
            )
    end_epoch = start_epoch + len(commands) * COMMAND_INTERVAL_SECONDS
    _wait_until(end_epoch, "end of command trace")
    return start_epoch, end_epoch


def _parse_debug(log_text: str) -> tuple[dict[int, dict[str, str]], dict[int, dict[str, str]]]:
    ue_rows = SUPPORT.SUPPORT.parse_marker_lines(log_text, "UE_RADIO_DEBUG_V1")
    channel_rows = SUPPORT.SUPPORT.parse_marker_lines(log_text, "RFSIM_CHANNEL_DEBUG_V1")
    return ue_rows, channel_rows


def build_trace_telemetry(
    command_events: list[dict[str, Any]], log_text: str
) -> list[dict[str, Any]]:
    ue_rows, channel_rows = _parse_debug(log_text)
    rows: list[dict[str, Any]] = []
    for event in command_events:
        utc_second = int(event["sample_utc_second"])
        ue = ue_rows.get(utc_second)
        channel = channel_rows.get(utc_second)
        if ue is None or channel is None:
            continue
        required_ue = {
            "rsrp_digital_power_linear",
            "rsrp_db_per_re_unquantized",
            "ss_rsrp_dbm_integer",
            "ss_sinr_db",
        }
        missing_ue = required_ue - set(ue)
        if missing_ue:
            raise ValidationError(f"incomplete UE telemetry: {sorted(missing_ue)}")
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
        missing = required_channel - set(channel)
        if missing:
            raise ValidationError(f"incomplete channel telemetry: {sorted(missing)}")
        if channel["oai_rng_seed"] != str(OAI_RNG_SEED):
            raise ValidationError("trace telemetry RNG seed mismatch")
        applied_gain = float(channel["applied_gain_db"])
        applied_noise = float(channel["noise_power_db"])
        if not math.isclose(applied_gain, event["commanded_gain_db"], abs_tol=1e-6):
            raise ValidationError(f"applied gain mismatch at command {event['command_index']}")
        if not math.isclose(applied_noise, event["commanded_noise_power_db"], abs_tol=1e-6):
            raise ValidationError(f"applied noise mismatch at command {event['command_index']}")
        row = {
            "command_index": event["command_index"],
            "trace_row_index": event["trace_row_index"],
            "trace_time_bin": event["trace_time_bin"],
            "trace_t_s": event["trace_t_s"],
            "target_relative_rsrp_db": event["target_relative_rsrp_db"],
            "target_sinr_db": event["target_sinr_db"],
            "projected_relative_rsrp_db": event["projected_relative_rsrp_db"],
            "projected_sinr_db": event["projected_sinr_db"],
            "commanded_gain_db": event["commanded_gain_db"],
            "commanded_noise_power_db": event["commanded_noise_power_db"],
            "clipped": event["clipped"],
            "scheduled_epoch": event["scheduled_epoch"],
            "command_complete_epoch": event["command_complete_epoch"],
            "command_completion_lateness_seconds": event[
                "command_completion_lateness_seconds"
            ],
            "sample_utc_second": utc_second,
            "sample_midpoint_epoch": utc_second + 0.5,
            "applied_gain_db": channel["applied_gain_db"],
            "applied_noise_power_db": channel["noise_power_db"],
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
            "channel_family",
            "channel_model_name",
            "channel_snapshot_id",
            "tap_fingerprint_fnv1a64",
            "attached",
            "clipped",
        }
        if not all(
            math.isfinite(float(row[field]))
            for field in set(TRACE_FIELDS) - string_fields
        ):
            raise ValidationError(f"non-finite trace telemetry row: {row}")
        rows.append(row)
    return rows


def build_anchor_telemetry(
    anchor_type: str,
    log_text: str,
    start_epoch: float,
    end_epoch: float,
) -> list[dict[str, Any]]:
    ue_rows, channel_rows = _parse_debug(log_text)
    rows: list[dict[str, Any]] = []
    for utc_second, ue in sorted(ue_rows.items()):
        midpoint = utc_second + 0.5
        if not start_epoch <= midpoint < end_epoch:
            continue
        channel = channel_rows.get(utc_second)
        if channel is None:
            continue
        required_ue = {"rsrp_db_per_re_unquantized", "ss_sinr_db"}
        required_channel = {
            "applied_gain_db",
            "noise_power_db",
            "model",
            "channel_length",
            "nb_taps",
            "tap_energy_linear",
        }
        missing_ue = required_ue - set(ue)
        missing_channel = required_channel - set(channel)
        if missing_ue or missing_channel:
            raise ValidationError(
                f"incomplete {anchor_type} telemetry: "
                f"UE={sorted(missing_ue)} channel={sorted(missing_channel)}"
            )
        row = {
            "anchor_type": anchor_type,
            "utc_second": utc_second,
            "rsrp_db_per_re_unquantized": ue["rsrp_db_per_re_unquantized"],
            "ss_sinr_db": ue["ss_sinr_db"],
            "applied_gain_db": channel["applied_gain_db"],
            "applied_noise_power_db": channel["noise_power_db"],
            "channel_model_name": channel["model"],
            "channel_length": channel["channel_length"],
            "nb_taps": channel["nb_taps"],
            "tap_energy_linear": channel["tap_energy_linear"],
        }
        if not math.isclose(float(row["applied_gain_db"]), ANCHOR_GAIN_DB, abs_tol=1e-6):
            raise ValidationError(f"{anchor_type} gain mismatch")
        if not math.isclose(
            float(row["applied_noise_power_db"]), ANCHOR_NOISE_DB, abs_tol=1e-6
        ):
            raise ValidationError(f"{anchor_type} noise mismatch")
        rows.append(row)
    return rows


def make_output(root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output = root / f"phase3i-short-trace-{stamp}"
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
    commands_file = Path(args.commands).resolve()
    override_file = Path(args.override_file).resolve()
    output = make_output(Path(args.output_root).resolve())
    print(f"OUTPUT_DIR={output}", flush=True)
    if args.expected_commands_sha256 != EXPECTED_COMMANDS_SHA256:
        raise ValidationError("runtime command checksum does not match the frozen protocol")
    commands = load_commands(commands_file)
    profile_revision = BASE.run_command("git", "-C", str(profile_root), "rev-parse", "HEAD")
    if profile_revision != args.expected_profile_revision:
        raise ValidationError(
            f"profile revision mismatch: expected {args.expected_profile_revision}, "
            f"observed {profile_revision}"
        )
    runner_sha256 = SUPPORT.sha256(Path(__file__).resolve())
    if runner_sha256 != args.expected_runner_sha256:
        raise ValidationError("short-trace runner checksum mismatch")
    SUPPORT.require_hash(compose_file, args.expected_compose_sha256)
    SUPPORT.require_hash(channel_config, args.expected_channel_config_sha256)
    SUPPORT.require_hash(ue_config, args.expected_ue_config_sha256)
    if not channel_helper.is_file() or channel_helper.is_symlink():
        raise ValidationError(f"missing or unsafe channel helper: {channel_helper}")
    if BASE.image_id(args.debug_image) != args.expected_debug_image_id:
        raise ValidationError("instrumented UE image ID is unavailable")
    image_revision = BASE.run_command(
        "docker",
        "image",
        "inspect",
        "-f",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        args.debug_image,
    )
    if image_revision != OAI_REVISION:
        raise ValidationError(f"instrumented image revision mismatch: {image_revision}")
    if BASE.docker_inspect("{{.Config.Image}}", UE_CONTAINER) != ORIGINAL_IMAGE:
        raise ValidationError("live UE is not using the frozen rollback image")
    original_image_id = BASE.docker_inspect("{{.Image}}", UE_CONTAINER)
    original_gnb_restart_count = int(
        BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER)
    )
    if BASE.docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER) != "healthy":
        raise ValidationError("gNB is not healthy before the short trace")

    attach_config = output / "channelmod-attach-minus60.conf"
    substitutions = SUPPORT.derive_attach_config(channel_config, attach_config)
    override_file.parent.mkdir(parents=True, exist_ok=True)
    override_file.write_text(SUPPORT.override_text(args.debug_image, OAI_RNG_SEED, attach_config))
    error: str | None = None
    rollback: dict[str, Any] = {"attempted": False, "passed": False}
    trace_rows: list[dict[str, Any]] = []
    anchor_rows: list[dict[str, Any]] = []
    command_events: list[dict[str, Any]] = []
    ping_checks: list[dict[str, Any]] = []
    critical_failure_count = 0
    ue_restart_count = -1
    gnb_restart_count_change = -1
    gnb_health = "unknown"
    ue_log_text = ""
    gnb_log_text = ""
    mutated = False
    try:
        mutated = True
        BASE.compose(
            compose_file,
            override_file,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            UE_SERVICE,
        )
        if BASE.docker_inspect("{{.Image}}", UE_CONTAINER) != args.expected_debug_image_id:
            raise ValidationError("instrumented UE image mismatch")
        environment = BASE.docker_inspect(
            "{{range .Config.Env}}{{println .}}{{end}}", UE_CONTAINER
        ).splitlines()
        if environment.count(f"OAI_RNGSEED={OAI_RNG_SEED}") != 1:
            raise ValidationError("the UE container does not have the frozen RNG seed")
        BASE.wait_attached(args.attach_timeout_seconds)
        attach_gain = SUPPORT.channel_command(channel_helper, "show", "ploss")
        attach_noise = SUPPORT.channel_command(channel_helper, "show", "noise_power_dB")
        if not math.isclose(float(attach_gain["observed"]), ATTACH_GAIN_DB, abs_tol=1e-6):
            raise ValidationError("UE did not attach at zero gain")
        if not math.isclose(
            float(attach_noise["observed"]), ATTACH_NOISE_DB, abs_tol=1e-6
        ):
            raise ValidationError("UE did not attach at -60 dB noise")
        BASE.wait_for_markers(15.0)
        PHASE3H._sleep_with_attachment_check(
            POST_ATTACH_STABILIZATION_SECONDS, "post-attachment stabilization"
        )
        ue_log_prefix = BASE.run_command("docker", "logs", UE_CONTAINER)
        gnb_log_prefix = BASE.run_command("docker", "logs", GNB_CONTAINER)

        PHASE3H._set_controls(channel_helper, ANCHOR_GAIN_DB, ANCHOR_NOISE_DB)
        PHASE3H._sleep_with_attachment_check(ANCHOR_SETTLING_SECONDS, "anchor start settling")
        anchor_start = PHASE3H._collect_window(ANCHOR_USABLE_SECONDS, "anchor start")

        with PersistentChannelSession(channel_helper) as channel_session:
            run_command_trace(commands, channel_session, command_events, ping_checks)

        PHASE3H._set_controls(channel_helper, ANCHOR_GAIN_DB, ANCHOR_NOISE_DB)
        PHASE3H._sleep_with_attachment_check(ANCHOR_SETTLING_SECONDS, "anchor end settling")
        anchor_end = PHASE3H._collect_window(ANCHOR_USABLE_SECONDS, "anchor end")
        ping_checks.extend(anchor_start["ping_checks"])
        ping_checks.extend(anchor_end["ping_checks"])

        PHASE3H._set_controls(channel_helper, ATTACH_GAIN_DB, ATTACH_NOISE_DB)
        time.sleep(2.0)
        ue_log_text = BASE.run_command("docker", "logs", UE_CONTAINER)
        gnb_log_text = BASE.run_command("docker", "logs", GNB_CONTAINER)
        ue_log_path = output / "phase3i-ue.log"
        gnb_log_path = output / "phase3i-gnb.log"
        ue_log_path.write_text(ue_log_text + "\n")
        gnb_log_path.write_text(gnb_log_text + "\n")
        BASE.write_json(output / "phase3i-command-events.json", command_events)
        BASE.write_json(output / "phase3i-anchor-windows.json", [anchor_start, anchor_end])
        BASE.write_json(output / "phase3i-ping-checks.json", ping_checks)

        trace_rows = build_trace_telemetry(command_events, ue_log_text)
        anchor_rows.extend(
            build_anchor_telemetry(
                "anchor_start",
                ue_log_text,
                anchor_start["usable_start_epoch"],
                anchor_start["usable_end_epoch"],
            )
        )
        anchor_rows.extend(
            build_anchor_telemetry(
                "anchor_end",
                ue_log_text,
                anchor_end["usable_start_epoch"],
                anchor_end["usable_end_epoch"],
            )
        )
        if len(trace_rows) < MINIMUM_TRACE_ROWS:
            raise ValidationError(f"only {len(trace_rows)} of 60 trace rows were paired")
        if sum(row["anchor_type"] == "anchor_start" for row in anchor_rows) < 7:
            raise ValidationError("insufficient anchor-start telemetry")
        if sum(row["anchor_type"] == "anchor_end" for row in anchor_rows) < 7:
            raise ValidationError("insufficient anchor-end telemetry")
        identities = {
            (row["channel_model_name"], row["channel_length"], row["nb_taps"])
            for row in trace_rows
        }
        if identities != {("rfsimu_channel_enB0", "1", "1")}:
            raise ValidationError("the trace did not retain the AWGN identity path")
        if any(
            not math.isclose(float(row["tap_energy_linear"]), 1.0, abs_tol=1e-9)
            for row in trace_rows
        ):
            raise ValidationError("AWGN tap energy changed during the trace")
        write_csv(output / "phase3i_short_trace_telemetry.csv", TRACE_FIELDS, trace_rows)
        write_csv(output / "phase3i_anchor_telemetry.csv", ANCHOR_FIELDS, anchor_rows)

        ue_observation = SUPPORT.SUPPORT.log_suffix(ue_log_text, ue_log_prefix, "UE")
        gnb_observation = SUPPORT.SUPPORT.log_suffix(gnb_log_text, gnb_log_prefix, "gNB")
        log_result = PARSER.parse_replay_logs(
            ue_log_text,
            gnb_log_text,
            ue_failure_text=ue_observation,
            gnb_failure_text=gnb_observation,
        )
        failures = log_result["failure_marker_counts"]
        critical_failure_count = sum(
            count for domain in failures.values() for count in domain.values()
        )
        ue_restart_count = int(BASE.docker_inspect("{{.RestartCount}}", UE_CONTAINER))
        gnb_restart_count = int(BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER))
        gnb_restart_count_change = gnb_restart_count - original_gnb_restart_count
        gnb_health = BASE.docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER)
        ping_fraction = sum(item["passed"] for item in ping_checks) / len(ping_checks)
        if critical_failure_count != 0:
            raise ValidationError(f"critical radio failure during short trace: {failures}")
        if ue_restart_count != 0 or gnb_restart_count_change != 0:
            raise ValidationError("container restart during the short trace")
        if gnb_health != "healthy":
            raise ValidationError("gNB is not healthy after the short trace")
        if ping_fraction < 0.9:
            raise ValidationError(f"ping fraction {ping_fraction:.3f} is below 0.9")
        BASE.write_json(output / "phase3i-log-analysis.json", log_result)
    except (KeyboardInterrupt, OSError, ValidationError, subprocess.SubprocessError) as exc:
        error = str(exc)
        BASE.write_json(output / "phase3i-command-events.json", command_events)
        BASE.write_json(output / "phase3i-ping-checks.json", ping_checks)
        if mutated:
            for container, path in (
                (UE_CONTAINER, output / "phase3i-ue.log"),
                (GNB_CONTAINER, output / "phase3i-gnb.log"),
            ):
                if path.exists():
                    continue
                try:
                    path.write_text(BASE.run_command("docker", "logs", container) + "\n")
                except (OSError, ValidationError, subprocess.SubprocessError):
                    pass
    finally:
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
                gnb_restarts_after = int(
                    BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER)
                )
                rollback.update(
                    {
                        "restored_image_id": restored_id,
                        "expected_image_id": original_image_id,
                        "gnb_restart_count_before": original_gnb_restart_count,
                        "gnb_restart_count_after": gnb_restarts_after,
                        "attached": BASE.is_attached(),
                        "passed": restored_id == original_image_id
                        and gnb_restarts_after == original_gnb_restart_count
                        and BASE.is_attached(),
                    }
                )
                if not rollback["passed"]:
                    raise ValidationError(f"rollback verification failed: {rollback}")
            except (OSError, ValidationError, subprocess.SubprocessError) as rollback_error:
                rollback["error"] = str(rollback_error)
                error = f"{error + '; ' if error else ''}rollback failed: {rollback_error}"
    ping_fraction = (
        sum(item["passed"] for item in ping_checks) / len(ping_checks) if ping_checks else 0.0
    )
    execution_state = {
        "schema_version": 1,
        "stage": "phase_3i_representative_short_trace_replay",
        "execution_completed": error is None and len(trace_rows) >= MINIMUM_TRACE_ROWS,
        "error": error,
        "research_revision": RESEARCH_REVISION,
        "research_protocol_sha256": RESEARCH_PROTOCOL_SHA256,
        "oai_revision": OAI_REVISION,
        "profile_revision": profile_revision,
        "runner_sha256": runner_sha256,
        "commands_sha256": SUPPORT.sha256(commands_file),
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
        "target_rows": 60,
        "paired_trace_rows": len(trace_rows),
        "anchor_rows": len(anchor_rows),
        "ping_success_fraction": ping_fraction,
        "critical_failure_count": critical_failure_count,
        "ue_restart_count": ue_restart_count,
        "gnb_restart_count_change": gnb_restart_count_change,
        "gnb_health": gnb_health,
        "rollback": rollback,
        "gNB_untouched": True,
        "final_test6_accessed": False,
        "abc_authorized": False,
        "full_trace_replay_authorized": False,
    }
    BASE.write_json(output / "execution_state.json", execution_state)
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"TRACE_ROWS={len(trace_rows)} PING={ping_fraction:.3f}", flush=True)
    print(f"TELEMETRY={output / 'phase3i_short_trace_telemetry.csv'}", flush=True)
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
        "--commands", default="/local/repository/etc/phase3i-short-trace-commands.csv"
    )
    root.add_argument(
        "--override-file", default="/local/upv-phase3i-short-trace-v1/ue.override.yaml"
    )
    root.add_argument("--output-root", default="/local/logs/upv-phase3i-short-trace-v1")
    root.add_argument("--debug-image", default=DEBUG_IMAGE)
    root.add_argument("--expected-debug-image-id", required=True)
    root.add_argument("--expected-profile-revision", required=True)
    root.add_argument("--expected-runner-sha256", required=True)
    root.add_argument("--expected-commands-sha256", required=True)
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
