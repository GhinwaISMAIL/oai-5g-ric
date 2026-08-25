from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "patch-oai-channel-pipeline-balance.py"
SPEC = importlib.util.spec_from_file_location("patch_oai_channel_pipeline_balance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def source_with_anchor() -> str:
    return "before\n" + MODULE.ANCHOR + "after\n"


def test_patch_balances_contiguous_ranges_and_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "channel_pipeline.c"
    source.write_text(source_with_anchor())

    assert MODULE.patch_file(source) is True
    patched = source.read_text()
    assert MODULE.MARKER in patched
    assert "((size_t)num_samples * job_index) / num_jobs" in patched
    assert "((size_t)num_samples * (job_index + 1)) / num_jobs" in patched
    assert "batch_start < job_end" in patched
    assert "min(batch_start + batch_size, job_end)" in patched
    assert MODULE.patch_file(source) is False


def test_patch_rejects_unknown_or_partial_source(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.c"
    unknown.write_text("unrelated source\n")
    with pytest.raises(RuntimeError, match="expected one occurrence"):
        MODULE.patch_file(unknown)

    partial = tmp_path / "partial.c"
    partial.write_text(f"/* {MODULE.MARKER} */\n")
    with pytest.raises(RuntimeError, match="partial"):
        MODULE.patch_file(partial)


def test_partition_is_complete_nonoverlapping_and_balanced() -> None:
    num_samples = 23_040
    num_jobs = 4
    ranges = [
        (
            num_samples * job / num_jobs,
            num_samples * (job + 1) / num_jobs,
        )
        for job in range(num_jobs)
    ]
    integer_ranges = [(int(start), int(end)) for start, end in ranges]

    assert integer_ranges == [
        (0, 5_760),
        (5_760, 11_520),
        (11_520, 17_280),
        (17_280, 23_040),
    ]
    assert integer_ranges[0][0] == 0
    assert integer_ranges[-1][1] == num_samples
    assert all(
        integer_ranges[index][1] == integer_ranges[index + 1][0]
        for index in range(num_jobs - 1)
    )
