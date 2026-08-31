#!/usr/bin/env python3
"""Run the frozen corrected RFsim one-UE AWGN noise validation."""

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

SUPPORT_SCRIPT = Path(__file__).with_name("run-phase3c14-awgn-control.py")
SUPPORT_SPEC = importlib.util.spec_from_file_location(
    "rfsim_noise_validation_support", SUPPORT_SCRIPT
)
if SUPPORT_SPEC is None or SUPPORT_SPEC.loader is None:
    raise RuntimeError(f"cannot load execution support: {SUPPORT_SCRIPT}")
SUPPORT = importlib.util.module_from_spec(SUPPORT_SPEC)
SUPPORT_SPEC.loader.exec_module(SUPPORT)
BASE = SUPPORT.BASE
PARSER = SUPPORT.PARSER

OAI_REVISION = "70508ebaf52f2aae420566d380c6537f2efb9f0c"
CHANNEL_FAMILY = "AWGN"
DEBUG_IMAGE = "oai-nr-ue-rfsim-noise-v1:70508eb-21d0713"
ORIGINAL_IMAGE = "ghinwa555/oai-nr-ue-chan:v4"
ATTACH_NOISE_DB = -60.0
FIXED_GAIN_DB = 0.0
SETTLING_SECONDS = 5.0
USABLE_SECONDS = 15.0
PING_TARGET = "12.1.1.1"
STATE_ORDER = (
    (-60.0, -30.0, -20.0, -40.0, -25.0),
    (-25.0, -60.0, -40.0, -20.0, -30.0),
    (-20.0, -40.0, -25.0, -30.0, -60.0),
)
EXECUTION_PLAN = tuple(
    (repetition, position, state, 41000 + (repetition - 1) * 5 + position)
    for repetition, states in enumerate(STATE_ORDER, start=1)
    for position, state in enumerate(states, start=1)
)
UE_CONTAINER = BASE.UE_CONTAINER
GNB_CONTAINER = BASE.GNB_CONTAINER
UE_SERVICE = BASE.UE_SERVICE
ATTACH_NOISE_PATTERN = re.compile(r"(noise_power_dB\s*=\s*)-30(\s*;)")

TELEMETRY_FIELDS = (
    "execution_id",
    "repetition",
    "position",
    "oai_rng_seed",
    "commanded_noise_power_db",
    "applied_noise_power_db",
    "commanded_gain_db",
    "applied_gain_db",
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

ValidationError = BASE.ReplayError


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValidationError(f"missing or unsafe frozen input: {path}")
    observed = sha256(path)
    if observed != expected:
        raise ValidationError(
            f"checksum mismatch for {path}: expected {expected}, observed {observed}"
        )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TELEMETRY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def derive_attach_config(source: Path, destination: Path) -> int:
    source_text = source.read_text()
    derived_text, substitutions = ATTACH_NOISE_PATTERN.subn(
        rf"\g<1>{ATTACH_NOISE_DB:g}\g<2>", source_text
    )
    if substitutions < 1:
        raise ValidationError("generated channel config has no -30 dB noise state")
    destination.write_text(derived_text)
    return substitutions


def override_text(image: str, seed: int, attach_config: Path) -> str:
    return (
        "services:\n"
        f"  {UE_SERVICE}:\n"
        f"    image: {image}\n"
        "    environment:\n"
        f'      OAI_RNGSEED: "{seed}"\n'
        "    volumes:\n"
        f"      - {attach_config}:/opt/oai-nr-ue/etc/channelmod_rfsimu.conf:ro\n"
    )


def ping_once() -> bool:
    result = subprocess.run(
        ("docker", "exec", UE_CONTAINER, "ping", "-c", "1", "-W", "1", PING_TARGET),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def channel_command(
    helper: Path,
    operation: str,
    parameter: str,
    value: float | None = None,
) -> dict[str, Any]:
    return SUPPORT.channel_command(helper, operation, parameter, value)


def build_telemetry_rows(
    execution_id: str,
    repetition: int,
    position: int,
    rng_seed: int,
    commanded_noise_db: float,
    log_text: str,
    usable_start_epoch: float,
    usable_end_epoch: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ue_rows = SUPPORT.parse_marker_lines(log_text, "UE_RADIO_DEBUG_V1")
    channel_rows = SUPPORT.parse_marker_lines(log_text, "RFSIM_CHANNEL_DEBUG_V1")
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
            raise ValidationError(
                f"incomplete RFsim channel telemetry: {sorted(missing)}"
            )
        if channel["oai_rng_seed"] != str(rng_seed):
            raise ValidationError(
                f"RNG seed mismatch: expected {rng_seed}, "
                f"observed {channel['oai_rng_seed']}"
            )
        row = {
            "execution_id": execution_id,
            "repetition": repetition,
            "position": position,
            "oai_rng_seed": rng_seed,
            "commanded_noise_power_db": commanded_noise_db,
            "applied_noise_power_db": channel["noise_power_db"],
            "commanded_gain_db": FIXED_GAIN_DB,
            "applied_gain_db": channel["applied_gain_db"],
            "utc_second": utc_second,
            "channel_family": CHANNEL_FAMILY,
            "channel_model_name": channel["model"],
            "channel_snapshot_id": channel["channel_snapshot_id"],
            "channel_snapshot_timestamp_ns": channel[
                "channel_snapshot_timestamp_ns"
            ],
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
        numeric_fields = set(TELEMETRY_FIELDS) - {
            "execution_id",
            "channel_family",
            "channel_model_name",
            "channel_snapshot_id",
            "tap_fingerprint_fnv1a64",
            "attached",
        }
        if not all(math.isfinite(float(row[field])) for field in numeric_fields):
            raise ValidationError(f"non-finite telemetry row: {row}")
        rows.append(row)
    return rows, {
        "ue_debug_seconds": len(ue_rows),
        "channel_debug_seconds": len(channel_rows),
        "matched_usable_rows": len(rows),
        "missing_channel_rows": missing_channel,
    }


def run_one_execution(
    repetition: int,
    position: int,
    commanded_noise_db: float,
    rng_seed: int,
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
    execution_id = f"r{repetition}-p{position}-n{abs(int(commanded_noise_db))}"
    override_file.write_text(override_text(debug_image, rng_seed, attach_config))
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
        raise ValidationError(f"debug UE image mismatch: {observed_image}")
    environment = BASE.docker_inspect(
        "{{range .Config.Env}}{{println .}}{{end}}", UE_CONTAINER
    ).splitlines()
    if environment.count(f"OAI_RNGSEED={rng_seed}") != 1:
        raise ValidationError(f"container does not have one OAI_RNGSEED={rng_seed}")
    mounted_config = BASE.docker_inspect(
        "{{range .Mounts}}{{if eq .Destination "
        '"/opt/oai-nr-ue/etc/channelmod_rfsimu.conf"}}'
        "{{.Source}}{{end}}{{end}}",
        UE_CONTAINER,
    )
    if Path(mounted_config).resolve() != attach_config:
        raise ValidationError(f"unexpected attached channel config: {mounted_config}")

    BASE.wait_attached(attach_timeout_seconds)
    identity = channel_command(channel_helper, "show", "noise_power_dB")
    if not math.isclose(float(identity["observed"]), ATTACH_NOISE_DB, abs_tol=1e-6):
        raise ValidationError(f"UE did not attach at {ATTACH_NOISE_DB:g} dB: {identity}")
    channel_command(channel_helper, "set", "ploss", FIXED_GAIN_DB)
    channel_command(channel_helper, "set", "noise_power_dB", ATTACH_NOISE_DB)
    BASE.wait_for_markers(15.0)
    ue_log_prefix = BASE.run_command("docker", "logs", UE_CONTAINER)
    gnb_log_prefix = BASE.run_command("docker", "logs", GNB_CONTAINER)

    applied = channel_command(
        channel_helper, "set", "noise_power_dB", commanded_noise_db
    )
    try:
        settling_deadline = time.monotonic() + SETTLING_SECONDS
        while time.monotonic() < settling_deadline:
            if not BASE.is_attached():
                raise ValidationError(f"attachment lost while settling {execution_id}")
            time.sleep(min(1.0, max(0.0, settling_deadline - time.monotonic())))

        usable_start_epoch = time.time()
        attachment_checks: list[dict[str, Any]] = []
        ping_checks: list[dict[str, Any]] = []
        usable_deadline = time.monotonic() + USABLE_SECONDS
        while time.monotonic() < usable_deadline:
            attached = BASE.is_attached()
            ping_pass = ping_once() if attached else False
            now = time.time()
            attachment_checks.append({"epoch": now, "attached": attached})
            ping_checks.append({"epoch": now, "passed": ping_pass})
            if not attached:
                raise ValidationError(f"attachment lost during {execution_id}")
            time.sleep(min(1.0, max(0.0, usable_deadline - time.monotonic())))
        usable_end_epoch = time.time()
    finally:
        channel_command(channel_helper, "set", "noise_power_dB", ATTACH_NOISE_DB)

    time.sleep(2.0)
    ue_log_text = BASE.run_command("docker", "logs", UE_CONTAINER)
    gnb_log_text = BASE.run_command("docker", "logs", GNB_CONTAINER)
    ue_observation_log = SUPPORT.log_suffix(ue_log_text, ue_log_prefix, "UE")
    gnb_observation_log = SUPPORT.log_suffix(gnb_log_text, gnb_log_prefix, "gNB")
    ue_log_path = output / f"{execution_id}-ue.log"
    gnb_log_path = output / f"{execution_id}-gnb.log"
    ue_log_path.write_text(ue_log_text + "\n")
    gnb_log_path.write_text(gnb_log_text + "\n")
    BASE.write_json(output / f"{execution_id}-attachment.json", attachment_checks)
    BASE.write_json(output / f"{execution_id}-ping.json", ping_checks)

    rows, diagnostics = build_telemetry_rows(
        execution_id,
        repetition,
        position,
        rng_seed,
        commanded_noise_db,
        ue_log_text,
        usable_start_epoch,
        usable_end_epoch,
    )
    write_csv(output / f"{execution_id}-telemetry.csv", rows)
    if len(rows) < 10:
        raise ValidationError(f"{execution_id} produced only {len(rows)} paired rows")
    if {
        (row["channel_model_name"], row["channel_length"], row["nb_taps"])
        for row in rows
    } != {("rfsimu_channel_enB0", "1", "1")}:
        raise ValidationError(f"{execution_id} did not retain the AWGN identity path")
    if any(
        not math.isclose(float(row["tap_energy_linear"]), 1.0, abs_tol=1e-9)
        for row in rows
    ):
        raise ValidationError(f"{execution_id} AWGN tap energy changed")
    if any(
        not math.isclose(
            float(row["applied_noise_power_db"]), commanded_noise_db, abs_tol=1e-6
        )
        for row in rows
    ):
        raise ValidationError(f"{execution_id} applied noise differs from command")
    if any(
        not math.isclose(
            float(row["applied_gain_db"]), FIXED_GAIN_DB, abs_tol=1e-6
        )
        for row in rows
    ):
        raise ValidationError(f"{execution_id} applied gain differs from zero")

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
        raise ValidationError(
            f"{execution_id} ping success fraction {ping_fraction:.3f} is below 0.9"
        )
    if critical_failure_count != 0:
        raise ValidationError(f"critical radio failure during {execution_id}: {failures}")
    if ue_restart_count != 0 or gnb_restart_count != expected_gnb_restart_count:
        raise ValidationError(f"container restart during {execution_id}")
    if gnb_health != "healthy":
        raise ValidationError(f"gNB is not healthy after {execution_id}")

    summary = {
        "execution_id": execution_id,
        "repetition": repetition,
        "position": position,
        "oai_rng_seed": rng_seed,
        "commanded_noise_power_db": commanded_noise_db,
        "applied_command_result": applied,
        "channel_identity_at_attach": identity,
        "usable_start_epoch": usable_start_epoch,
        "usable_end_epoch": usable_end_epoch,
        "diagnostics": diagnostics,
        "paired_radio_samples": len(rows),
        "ping_success_fraction": ping_fraction,
        "continuous_attachment": all(item["attached"] for item in attachment_checks),
        "failure_marker_counts": failures,
        "critical_pbch_failure_count": failures["ue"]["pbch_decode_error"],
        "critical_pusch_failure_count": failures["gnb"]["pusch_ul_failure"],
        "critical_failure_count": critical_failure_count,
        "ue_restart_count": ue_restart_count,
        "gnb_restart_count": gnb_restart_count,
        "gnb_health": gnb_health,
        "ue_log_sha256": sha256(ue_log_path),
        "gnb_log_sha256": sha256(gnb_log_path),
    }
    BASE.write_json(output / f"{execution_id}-summary.json", summary)
    return rows, summary


def make_output(root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output = root / f"rfsim-noise-validation-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def execute(args: argparse.Namespace) -> int:
    if hasattr(sys, "geteuid") and sys.geteuid() != 0:
        raise ValidationError("run this command as root")
    compose_file = Path(args.compose_file).resolve()
    channel_config = Path(args.channel_config).resolve()
    ue_config = Path(args.ue_config).resolve()
    channel_helper = Path(args.channel_helper).resolve()
    override_file = Path(args.override_file).resolve()
    output = make_output(Path(args.output_root).resolve())
    print(f"OUTPUT_DIR={output}", flush=True)

    profile_root = Path(args.profile_root).resolve()
    profile_revision = BASE.run_command(
        "git", "-C", str(profile_root), "rev-parse", "HEAD"
    )
    if profile_revision != args.expected_profile_revision:
        raise ValidationError(
            f"profile revision mismatch: expected {args.expected_profile_revision}, "
            f"observed {profile_revision}"
        )
    runner_sha256 = sha256(Path(__file__).resolve())
    if runner_sha256 != args.expected_runner_sha256:
        raise ValidationError(
            f"runner checksum mismatch: expected {args.expected_runner_sha256}, "
            f"observed {runner_sha256}"
        )
    require_hash(compose_file, args.expected_compose_sha256)
    require_hash(channel_config, args.expected_channel_config_sha256)
    require_hash(ue_config, args.expected_ue_config_sha256)
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
        raise ValidationError("gNB is not healthy before validation")

    attach_config = output / "channelmod-attach-minus60.conf"
    substitutions = derive_attach_config(channel_config, attach_config)
    override_file.parent.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    error: str | None = None
    rollback: dict[str, Any] = {"attempted": False, "passed": False}
    mutated = False
    try:
        mutated = True
        for repetition, position, state, seed in EXECUTION_PLAN:
            rows, summary = run_one_execution(
                repetition,
                position,
                state,
                seed,
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
            print(
                f"PASS execution={summary['execution_id']} "
                f"noise={state:g} paired={len(rows)} "
                f"ping={summary['ping_success_fraction']:.3f}",
                flush=True,
            )
        write_csv(output / "corrected_noise_telemetry.csv", all_rows)
    except (KeyboardInterrupt, OSError, ValidationError, subprocess.SubprocessError) as exc:
        error = str(exc)
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
                gnb_restarts = int(
                    BASE.docker_inspect("{{.RestartCount}}", GNB_CONTAINER)
                )
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
                error = (
                    f"{error + '; ' if error else ''}rollback failed: {rollback_error}"
                )

    execution_state = {
        "schema_version": 1,
        "stage": "corrected_rfsim_noise_response_validation",
        "execution_completed": error is None and len(summaries) == len(EXECUTION_PLAN),
        "error": error,
        "oai_revision": OAI_REVISION,
        "profile_revision": profile_revision,
        "runner_sha256": runner_sha256,
        "compose_sha256": sha256(compose_file),
        "channel_config_sha256": sha256(channel_config),
        "ue_config_sha256": sha256(ue_config),
        "attach_config_sha256": sha256(attach_config),
        "attach_config_noise_substitutions": substitutions,
        "debug_image": args.debug_image,
        "debug_image_id": args.expected_debug_image_id,
        "debug_image_revision_label": image_revision,
        "original_image": ORIGINAL_IMAGE,
        "original_image_id": original_image_id,
        "execution_plan": [
            {
                "repetition": repetition,
                "position": position,
                "noise_power_db": state,
                "oai_rng_seed": seed,
            }
            for repetition, position, state, seed in EXECUTION_PLAN
        ],
        "executions": summaries,
        "rollback": rollback,
        "gNB_untouched": True,
    }
    BASE.write_json(output / "execution_state.json", execution_state)
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"TELEMETRY={output / 'corrected_noise_telemetry.csv'}", flush=True)
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
    root.add_argument(
        "--channel-helper", default="/local/repository/bin/channel-cell.py"
    )
    root.add_argument(
        "--override-file", default="/local/rfsim-noise-validation-v1/ue.override.yaml"
    )
    root.add_argument(
        "--output-root", default="/local/logs/rfsim-noise-validation-v1"
    )
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
    except (OSError, ValidationError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
