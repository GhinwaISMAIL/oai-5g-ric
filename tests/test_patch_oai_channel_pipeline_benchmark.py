from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "patch-oai-channel-pipeline-benchmark.py"
SPEC = importlib.util.spec_from_file_location("patch_oai_channel_pipeline_benchmark", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_exact_case_benchmark_patch_is_idempotent(tmp_path: Path) -> None:
    benchmark = tmp_path / "benchmark.cpp"
    benchmark.write_text(
        "before\n"
        + MODULE.FUNCTION_ANCHOR
        + "middle\n"
        + MODULE.TPOOL_ANCHOR
        + "middle2\n"
        + MODULE.REGISTRATION_ANCHOR
        + "after\n"
    )
    correctness = tmp_path / "test.cpp"
    correctness.write_text("before " + MODULE.CORRECTNESS_ANCHOR + " after\n")

    assert MODULE.patch_benchmark(benchmark) is True
    assert MODULE.patch_correctness_test(correctness) is True
    assert MODULE.patch_benchmark(benchmark) is False
    assert MODULE.patch_correctness_test(correctness) is False

    patched = benchmark.read_text()
    assert MODULE.MARKER in patched
    assert "Args({1, 1, 23040, 8, 4})" in patched
    assert "Args({1, 1, 61440, 16, 16})" in patched
    assert "Iterations(1000)" in patched
    assert "init_tpool(num_workers)" in patched
    assert "23040" in correctness.read_text()


def test_exact_case_benchmark_patch_rejects_unknown_source(tmp_path: Path) -> None:
    benchmark = tmp_path / "benchmark.cpp"
    benchmark.write_text("unrelated source\n")
    with pytest.raises(RuntimeError, match="benchmark function"):
        MODULE.patch_benchmark(benchmark)

    correctness = tmp_path / "test.cpp"
    correctness.write_text("unrelated source\n")
    with pytest.raises(RuntimeError, match="correctness sample set"):
        MODULE.patch_correctness_test(correctness)
