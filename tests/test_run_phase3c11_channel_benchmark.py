from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "run-phase3c11-channel-benchmark.py"
SPEC = importlib.util.spec_from_file_location("run_phase3c11_channel_benchmark", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_plan_is_benchmark_only_and_abba(tmp_path: Path) -> None:
    plan = MODULE.build_plan(
        tmp_path / "source",
        tmp_path / "work",
        tmp_path / "evidence",
    )

    assert plan["network_services_started_for_benchmark"] is False
    assert plan["radio_network_active_during_measurement"] is False
    assert [item["label"] for item in plan["benchmark_invocations"]] == list(
        MODULE.ABBA
    )
    assert "-DENABLE_TESTS=ON" in plan["configure_and_build"]
    assert "23040" not in plan["configure_and_build"]
    assert "benchmark_channel_pipeline" in plan["benchmark_binary"]
    assert "test_channel_pipeline" in plan["correctness_binary"]
    serialized = json.dumps(plan)
    assert "docker compose" not in serialized
    assert "oai-gnb" not in serialized
    assert "oai-nr-ue" not in serialized


def test_dry_run_does_not_require_source_or_create_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "missing-source"
    work = tmp_path / "work"
    output = tmp_path / "output"
    args = MODULE.parser().parse_args(
        [
            "--source",
            str(source),
            "--work-root",
            str(work),
            "--output-root",
            str(output),
            "--dry-run",
        ]
    )

    assert MODULE.execute(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["source"] == str(source)
    assert not work.exists()
    assert not output.exists()


def test_new_directory_guard_rejects_existing_or_root(tmp_path: Path) -> None:
    with pytest.raises(MODULE.BenchmarkError, match="non-root"):
        MODULE.require_safe_new_directory(Path("/"), "work root")
    with pytest.raises(MODULE.BenchmarkError, match="already exists"):
        MODULE.require_safe_new_directory(tmp_path, "work root")


def test_container_command_mounts_source_and_optional_evidence(tmp_path: Path) -> None:
    command = MODULE.container_command(
        tmp_path / "source",
        "run benchmark",
        "--volume",
        f"{tmp_path / 'output'}:/evidence",
    )

    assert command[:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path / 'source'}:/oai-ran" in command
    assert f"{tmp_path / 'output'}:/evidence" in command
    assert command[-3:] == [MODULE.BASE_IMAGE, "/bin/bash", "-lc", "run benchmark"][-3:]


def test_repository_storage_metadata_records_promisor_without_claiming_alternates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    git_directory = repository / ".git"
    (git_directory / "objects" / "info").mkdir(parents=True)
    responses = {
        "--absolute-git-dir": str(git_directory),
        "--git-common-dir": str(git_directory),
        "objects/info/alternates": str(git_directory / "objects" / "info" / "alternates"),
        "objects.info.alternates": "",
        "remote.origin.promisor": "true",
        "extensions.partialclone": "origin",
    }

    def fake_run(command: list[str], **_: object) -> str:
        return responses[command[-1]]

    monkeypatch.setattr(MODULE, "run_command", fake_run)
    metadata = MODULE.repository_storage_metadata(repository)

    assert metadata["alternates_present"] is False
    assert metadata["remote_origin_promisor"] == "true"
    assert metadata["extensions_partial_clone"] == "origin"


def test_repository_storage_metadata_rejects_alternates_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    git_directory = repository / ".git"
    alternates_path = git_directory / "objects" / "info" / "alternates"
    alternates_path.parent.mkdir(parents=True)
    alternates_path.write_text("/external/objects\n")
    responses = {
        "--absolute-git-dir": str(git_directory),
        "--git-common-dir": str(git_directory),
        "objects/info/alternates": str(alternates_path),
        "objects.info.alternates": "",
    }

    def fake_run(command: list[str], **_: object) -> str:
        return responses[command[-1]]

    monkeypatch.delenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", raising=False)
    monkeypatch.setattr(MODULE, "run_command", fake_run)
    with pytest.raises(MODULE.BenchmarkError, match="Git alternates"):
        MODULE.repository_storage_metadata(repository)


def test_repository_storage_metadata_rejects_alternates_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    git_directory = repository / ".git"
    (git_directory / "objects" / "info").mkdir(parents=True)
    responses = {
        "--absolute-git-dir": str(git_directory),
        "--git-common-dir": str(git_directory),
        "objects/info/alternates": str(git_directory / "objects" / "info" / "alternates"),
        "objects.info.alternates": "",
    }

    def fake_run(command: list[str], **_: object) -> str:
        return responses[command[-1]]

    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", "/external/objects")
    monkeypatch.setattr(MODULE, "run_command", fake_run)
    with pytest.raises(MODULE.BenchmarkError, match="Git alternates"):
        MODULE.repository_storage_metadata(repository)


def test_known_timing_contaminants_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "run_command",
        lambda *_args, **_kwargs: (
            "100 1 100 20 98.0 git git pack-objects --revs --stdout\n"
            "200 1 200 10 1.0 python3 benchmark runner"
        ),
    )

    with pytest.raises(MODULE.BenchmarkError, match="git pack-objects"):
        MODULE.assert_no_known_timing_contaminants()


def test_quiet_process_snapshot_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = "200 1 200 10 1.0 python3 benchmark runner"
    monkeypatch.setattr(
        MODULE, "run_command", lambda *_args, **_kwargs: snapshot
    )

    assert MODULE.assert_no_known_timing_contaminants() == snapshot
