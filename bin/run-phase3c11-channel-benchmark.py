#!/usr/bin/env python3
"""Run the frozen Phase 3C11 baseline-versus-balanced channel benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

OAI_REVISION = "70508ebaf52f2aae420566d380c6537f2efb9f0c"
EXPECTED_SUBMODULES = {
    "openair2/E2AP/flexric": "ef6d722f22191eea74089966983da1f5ec1fedd4",
    "openair2/E2AP/flexric/ci-scripts/common": "20de484fa9025c9c342b8b945d89a10797ec2503",
}
BALANCE_PATCH_SHA256 = "66693c54be7fb62e36569d43d2c48ab1841a51b7fe69fbf077de2c80410825c6"
BENCHMARK_PATCH_SHA256 = "7d07b2a13f22578d34af684e8463a19d1f239fec7ad79ca44fdeccd7ddd13d67"
BASE_IMAGE = "phase3c11-ran-base:70508eb"
COMPOSE_SHA256 = "db5aade37a4613a95c3f9682cdddf3bc5bc73d74f398c004105547c80b8d0260"
GNB_CONTAINER = "ric5g-gnb-cell1"
UE_CONTAINER = "ric5g-ue-cell1-1"
GNB_SERVICE = "oai-gnb"
UE_SERVICE = "oai-nr-ue1"
ABBA = ("baseline-1", "optimized-1", "optimized-2", "baseline-2")
KNOWN_TIMING_CONTAMINANTS = ("git pack-objects", "benchmark_channel_pipeline")
BENCHMARK_SOURCE = Path("openair1/PHY/TOOLS/tests/benchmark_channel_pipeline.cpp")
CORRECTNESS_SOURCE = Path("openair1/PHY/TOOLS/tests/test_channel_pipeline.cpp")
PIPELINE_SOURCE = Path("openair1/SIMULATION/TOOLS/channel_pipeline.c")


class BenchmarkError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    log_path: Path | None = None,
    check: bool = True,
) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if log_path is not None:
        log_path.write_text(result.stdout)
    if check and result.returncode != 0:
        raise BenchmarkError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout[-2000:]}"
        )
    return result.stdout.strip()


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise BenchmarkError(f"missing or unsafe frozen input: {path}")
    observed = sha256(path)
    if observed != expected:
        raise BenchmarkError(
            f"checksum mismatch for {path}: expected {expected}, observed {observed}"
        )


def require_safe_new_directory(path: Path, label: str) -> None:
    if not path.is_absolute() or path == Path("/"):
        raise BenchmarkError(f"{label} must be an explicit absolute non-root path")
    if path.exists():
        raise BenchmarkError(f"{label} already exists: {path}")


def docker_inspect(format_string: str, container: str) -> str:
    return run_command(["docker", "inspect", "--format", format_string, container])


def cell_baseline() -> dict[str, Any]:
    gnb_health = docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER)
    if gnb_health != "healthy":
        raise BenchmarkError(f"{GNB_CONTAINER} is not healthy: {gnb_health}")
    attachment = run_command(
        ["docker", "exec", UE_CONTAINER, "ip", "-o", "-4", "addr", "show", "oaitun_ue1"]
    )
    if "inet " not in attachment:
        raise BenchmarkError(f"{UE_CONTAINER} is not attached")
    return {
        "gnb_image_id": docker_inspect("{{.Image}}", GNB_CONTAINER),
        "ue_image_id": docker_inspect("{{.Image}}", UE_CONTAINER),
        "gnb_restart_count": int(docker_inspect("{{.RestartCount}}", GNB_CONTAINER)),
        "ue_restart_count": int(docker_inspect("{{.RestartCount}}", UE_CONTAINER)),
        "gnb_health": gnb_health,
        "ue_attachment": attachment,
    }


def wait_for_cell_restore(timeout_seconds: float = 180.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        health = docker_inspect("{{.State.Health.Status}}", GNB_CONTAINER)
        attachment = run_command(
            ["docker", "exec", UE_CONTAINER, "ip", "-o", "-4", "addr", "show", "oaitun_ue1"],
            check=False,
        )
        if health == "healthy" and "inet " in attachment:
            return
        time.sleep(2.0)
    raise BenchmarkError("cell baseline was not healthy and attached after restore")


def repository_storage_metadata(repository: Path) -> dict[str, Any]:
    git_directory = Path(
        run_command(["git", "-C", str(repository), "rev-parse", "--absolute-git-dir"])
    )
    common_directory_text = run_command(
        ["git", "-C", str(repository), "rev-parse", "--git-common-dir"]
    )
    common_directory = Path(common_directory_text)
    if not common_directory.is_absolute():
        common_directory = (repository / common_directory).resolve()
    alternates_path_text = run_command(
        [
            "git",
            "-C",
            str(repository),
            "rev-parse",
            "--git-path",
            "objects/info/alternates",
        ]
    )
    alternates_path = Path(alternates_path_text)
    if not alternates_path.is_absolute():
        alternates_path = (repository / alternates_path).resolve()
    alternates_file = ""
    if alternates_path.is_file():
        alternates_file = alternates_path.read_text().strip()
    alternates_config = run_command(
        ["git", "-C", str(repository), "config", "--get", "objects.info.alternates"],
        check=False,
    )
    alternates_environment = os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES", "")
    if alternates_file or alternates_config or alternates_environment:
        raise BenchmarkError(
            f"repository has Git alternates: {repository}; "
            f"file={alternates_file!r}, config={alternates_config!r}, "
            f"environment={alternates_environment!r}"
        )
    return {
        "git_directory": str(git_directory),
        "common_directory": str(common_directory),
        "alternates_present": False,
        "remote_origin_promisor": run_command(
            ["git", "-C", str(repository), "config", "--get", "remote.origin.promisor"],
            check=False,
        )
        or None,
        "extensions_partial_clone": run_command(
            ["git", "-C", str(repository), "config", "--get", "extensions.partialclone"],
            check=False,
        )
        or None,
    }


def source_preflight(source: Path) -> dict[str, Any]:
    if not (source / ".git").exists():
        raise BenchmarkError(f"not a Git checkout: {source}")
    head = run_command(["git", "-C", str(source), "rev-parse", "HEAD"])
    if head != OAI_REVISION:
        raise BenchmarkError(f"OAI revision mismatch: {head}")
    status = run_command(["git", "-C", str(source), "status", "--porcelain"])
    if status:
        raise BenchmarkError(f"source checkout is not clean:\n{status}")
    storage = {"main": repository_storage_metadata(source)}
    submodule_text = run_command(
        ["git", "-C", str(source), "submodule", "status", "--recursive"]
    )
    observed: dict[str, str] = {}
    for line in submodule_text.splitlines():
        fields = line.strip().split()
        if len(fields) >= 2:
            observed[fields[1]] = fields[0].lstrip("-+")
    if observed != EXPECTED_SUBMODULES:
        raise BenchmarkError(
            f"submodule mismatch: expected {EXPECTED_SUBMODULES}, observed {observed}"
        )
    for submodule in sorted(observed):
        storage[submodule] = repository_storage_metadata(source / submodule)
    return {
        "head": head,
        "submodules": observed,
        "repository_storage": storage,
        "working_tree_complete_for_pinned_revision": True,
        "full_history_checkout_claimed": False,
    }


def container_command(source: Path, script: str, *extra: str) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "--volume",
        f"{source}:/oai-ran",
        "--workdir",
        "/oai-ran",
    ]
    command.extend(extra)
    command.extend([BASE_IMAGE, "/bin/bash", "-lc", script])
    return command


def build_plan(source: Path, work_root: Path, output_root: Path) -> dict[str, Any]:
    baseline = work_root / "baseline"
    optimized = work_root / "optimized"
    configure_and_build = (
        "source oaienv && "
        "cmake -GNinja -S /oai-ran -B /oai-ran/cmake_targets/phase3c11_bench/build "
        "-DENABLE_TESTS=ON "
        "-DAVX512=OFF -DOAI_VRTSIM_TAPS_CLIENT=ON "
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo "
        "-DCMAKE_C_FLAGS=-Werror -DCMAKE_CXX_FLAGS=-Werror && "
        "cmake --build /oai-ran/cmake_targets/phase3c11_bench/build "
        "--target benchmark_channel_pipeline test_channel_pipeline -- -j$(nproc)"
    )
    correctness = (
        "/oai-ran/cmake_targets/phase3c11_bench/build/openair1/PHY/TOOLS/tests/"
        "test_channel_pipeline"
    )
    benchmark_binary = (
        "/oai-ran/cmake_targets/phase3c11_bench/build/openair1/PHY/TOOLS/tests/"
        "benchmark_channel_pipeline"
    )
    invocations: list[dict[str, str]] = []
    for label in ABBA:
        condition = label.split("-", 1)[0]
        source_path = baseline if condition == "baseline" else optimized
        invocations.append(
            {
                "label": label,
                "condition": condition,
                "source": str(source_path),
                "json": str(output_root / f"{label}.json"),
                "log": str(output_root / f"{label}.log"),
            }
        )
    return {
        "source": str(source),
        "work_root": str(work_root),
        "output_root": str(output_root),
        "baseline": str(baseline),
        "optimized": str(optimized),
        "base_image": BASE_IMAGE,
        "configure_and_build": configure_and_build,
        "correctness_binary": correctness,
        "benchmark_binary": benchmark_binary,
        "benchmark_invocations": invocations,
        "network_services_started_for_benchmark": False,
        "radio_network_active_during_measurement": False,
        "cell_services_quiesced_during_correctness_and_timing": [
            GNB_CONTAINER,
            UE_CONTAINER,
        ],
        "cell_restore_mandatory": True,
    }


def collect_metadata() -> dict[str, Any]:
    commands = {
        "uname": ["uname", "-a"],
        "lscpu": ["lscpu"],
        "nproc": ["nproc"],
        "docker_version": ["docker", "version", "--format", "{{json .}}"],
        "load_average": ["cat", "/proc/loadavg"],
    }
    values = {
        name: run_command(command, check=False) for name, command in commands.items()
    }
    governor_paths = sorted(
        Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")
    )
    values["cpu_governors"] = {
        str(path): path.read_text().strip() for path in governor_paths if path.is_file()
    }
    values["python"] = platform.python_version()
    processes = run_command(
        ["ps", "-eo", "pid=,ppid=,pgid=,etimes=,pcpu=,comm=,args=", "--sort=-pcpu"],
        check=False,
    )
    values["processes_top_cpu"] = "\n".join(processes.splitlines()[:25])
    return values


def assert_no_known_timing_contaminants() -> str:
    snapshot = run_command(
        ["ps", "-eo", "pid=,ppid=,pgid=,etimes=,pcpu=,comm=,args=", "--sort=-pcpu"]
    )
    contaminants = [
        line.strip()
        for line in snapshot.splitlines()
        if any(marker in line for marker in KNOWN_TIMING_CONTAMINANTS)
    ]
    if contaminants:
        raise BenchmarkError(
            "known competing process present before timing:\n" + "\n".join(contaminants)
        )
    return "\n".join(snapshot.splitlines()[:25])


def evidence_hashes(output_root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(output_root)): sha256(path)
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.name != "execution_state.json"
    }


def execute(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    work_root = Path(args.work_root).resolve()
    output_root = Path(args.output_root).resolve()
    profile_bin = Path(__file__).resolve().parent
    plan = build_plan(source, work_root, output_root)
    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise BenchmarkError("run the Phase 3C11 benchmark as root")
    require_safe_new_directory(work_root, "work root")
    require_safe_new_directory(output_root, "output root")
    if platform.machine() != "x86_64":
        raise BenchmarkError(f"expected x86_64, observed {platform.machine()}")
    benchmark_patch = profile_bin / "patch-oai-channel-pipeline-benchmark.py"
    balance_patch = profile_bin / "patch-oai-channel-pipeline-balance.py"
    compose_file = Path(args.compose_file).resolve()
    require_hash(benchmark_patch, BENCHMARK_PATCH_SHA256)
    require_hash(balance_patch, BALANCE_PATCH_SHA256)
    require_hash(compose_file, COMPOSE_SHA256)
    preflight = source_preflight(source)
    original_cell = cell_baseline()

    work_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    state: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at_epoch": time.time(),
        "plan": plan,
        "source_preflight": preflight,
        "compose_file": str(compose_file),
        "original_cell": original_cell,
    }
    state_path = output_root / "execution_state.json"
    quiesced = False
    execution_error: Exception | None = None
    try:
        baseline = work_root / "baseline"
        optimized = work_root / "optimized"
        shutil.copytree(source, baseline, symlinks=True)
        shutil.copytree(source, optimized, symlinks=True)
        for condition in (baseline, optimized):
            run_command(
                [
                    sys.executable,
                    str(benchmark_patch),
                    str(condition / BENCHMARK_SOURCE),
                    str(condition / CORRECTNESS_SOURCE),
                ]
            )
        run_command(
            [sys.executable, str(balance_patch), str(optimized / PIPELINE_SOURCE)]
        )
        expected_diffs = {
            "baseline": sorted([str(BENCHMARK_SOURCE), str(CORRECTNESS_SOURCE)]),
            "optimized": sorted(
                [str(BENCHMARK_SOURCE), str(CORRECTNESS_SOURCE), str(PIPELINE_SOURCE)]
            ),
        }
        for name, condition in (("baseline", baseline), ("optimized", optimized)):
            run_command(["git", "-C", str(condition), "diff", "--check"])
            changed = sorted(
                run_command(["git", "-C", str(condition), "diff", "--name-only"])
                .splitlines()
            )
            if changed != expected_diffs[name]:
                raise BenchmarkError(
                    f"unexpected {name} source differences: {changed}"
                )
            (output_root / f"{name}-source.diff").write_text(
                run_command(["git", "-C", str(condition), "diff"]) + "\n"
            )

        run_command(
            [
                "docker",
                "build",
                "--target",
                "ran-base",
                "--tag",
                BASE_IMAGE,
                "--file",
                str(source / "docker/Dockerfile.base.ubuntu"),
                str(source),
            ],
            log_path=output_root / "base-image-build.log",
        )
        state["base_image_id"] = run_command(
            ["docker", "image", "inspect", BASE_IMAGE, "--format", "{{.Id}}"]
        )
        state["metadata_before"] = collect_metadata()

        for name, condition in (("baseline", baseline), ("optimized", optimized)):
            run_command(
                container_command(condition, plan["configure_and_build"]),
                log_path=output_root / f"{name}-build.log",
            )
            binary = condition / plan["benchmark_binary"].removeprefix("/oai-ran/")
            state[f"{name}_benchmark_binary_sha256"] = sha256(binary)

        if cell_baseline() != original_cell:
            raise BenchmarkError("cell baseline changed during benchmark preparation")
        quiesced = True
        run_command(["docker", "stop", GNB_CONTAINER, UE_CONTAINER])
        state["cell_quiesced_at_epoch"] = time.time()
        for container in (GNB_CONTAINER, UE_CONTAINER):
            observed_state = docker_inspect("{{.State.Status}}", container)
            if observed_state == "running":
                raise BenchmarkError(f"failed to quiesce {container}")
        time.sleep(5.0)
        state["processes_before_correctness_and_timing"] = (
            assert_no_known_timing_contaminants()
        )

        for name, condition in (("baseline", baseline), ("optimized", optimized)):
            run_command(
                container_command(condition, plan["correctness_binary"]),
                log_path=output_root / f"{name}-correctness.log",
            )

        for invocation in plan["benchmark_invocations"]:
            condition = baseline if invocation["condition"] == "baseline" else optimized
            label = invocation["label"]
            benchmark_command = (
                f"{plan['benchmark_binary']} "
                "--benchmark_filter=^BM_channel_convolution_tpool/ "
                "--benchmark_repetitions=30 "
                "--benchmark_report_aggregates_only=false "
                "--benchmark_min_warmup_time=1 "
                f"--benchmark_out=/evidence/{label}.json "
                "--benchmark_out_format=json"
            )
            run_command(
                container_command(
                    condition,
                    benchmark_command,
                    "--volume",
                    f"{output_root}:/evidence",
                ),
                log_path=output_root / f"{label}.log",
            )

        state["metadata_after"] = collect_metadata()
    except (OSError, BenchmarkError, subprocess.SubprocessError) as error:
        execution_error = error
    finally:
        if quiesced:
            rollback: dict[str, Any] = {"attempted": True, "passed": False}
            try:
                require_hash(compose_file, COMPOSE_SHA256)
                run_command(
                    [
                        "docker",
                        "compose",
                        "--file",
                        str(compose_file),
                        "up",
                        "-d",
                        "--no-deps",
                        "--force-recreate",
                        GNB_SERVICE,
                        UE_SERVICE,
                    ]
                )
                wait_for_cell_restore()
                restored_cell = cell_baseline()
                rollback["restored_cell"] = restored_cell
                rollback["passed"] = (
                    restored_cell["gnb_image_id"] == original_cell["gnb_image_id"]
                    and restored_cell["ue_image_id"] == original_cell["ue_image_id"]
                    and restored_cell["gnb_restart_count"]
                    == original_cell["gnb_restart_count"]
                    and restored_cell["ue_restart_count"]
                    == original_cell["ue_restart_count"]
                )
                if not rollback["passed"]:
                    raise BenchmarkError(f"cell rollback mismatch: {rollback}")
            except (OSError, BenchmarkError, subprocess.SubprocessError) as error:
                rollback["error"] = str(error)
                if execution_error is None:
                    execution_error = error
                else:
                    execution_error = BenchmarkError(
                        f"{execution_error}; cell rollback failed: {error}"
                    )
            state["rollback"] = rollback

    if execution_error is None:
        state["status"] = "completed"
        state["completed_at_epoch"] = time.time()
    else:
        state["status"] = "failed"
        state["error"] = str(execution_error)
        state["failed_at_epoch"] = time.time()
    state["evidence_sha256"] = evidence_hashes(output_root)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    if execution_error is not None:
        raise BenchmarkError(str(execution_error))
    print(f"OUTPUT_DIR={output_root}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--source", required=True)
    root.add_argument("--work-root", required=True)
    root.add_argument("--output-root", required=True)
    root.add_argument(
        "--compose-file", default="/local/repository/etc/docker-compose-cell1.yaml"
    )
    root.add_argument("--dry-run", action="store_true")
    return root


def main() -> int:
    try:
        return execute(parser().parse_args())
    except (OSError, BenchmarkError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
