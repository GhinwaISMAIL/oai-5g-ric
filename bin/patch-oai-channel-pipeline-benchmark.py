#!/usr/bin/env python3
"""Add the exact Phase 3C 1x1/L8/23040-sample/4-worker benchmark case."""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "Phase3C_1x1_L8_Samples23040_Workers4"
FUNCTION_ANCHOR = """static void BM_channel_convolution_tpool(benchmark::State &state)
{
  int nb_rx = state.range(0);
  int nb_tx = state.range(1);
  int num_samples = state.range(2);
  int channel_length = 16;
"""
FUNCTION_REPLACEMENT = """static void BM_channel_convolution_tpool(benchmark::State &state)
{
  int nb_rx = state.range(0);
  int nb_tx = state.range(1);
  int num_samples = state.range(2);
  int channel_length = state.range(3);
  int num_workers = state.range(4);
"""
TPOOL_ANCHOR = "  void *tpool = init_tpool(16);\n"
TPOOL_REPLACEMENT = "  void *tpool = init_tpool(num_workers);\n"
REGISTRATION_ANCHOR = """BENCHMARK(BM_channel_convolution_tpool)
    ->ArgsProduct({
        {1, 2, 4, 8, 16}, // nb_rx
        {1, 2, 4, 8, 16}, // nb_tx
        {61440}, // num_samples
    })
    ->Iterations(50);
"""
REGISTRATION_REPLACEMENT = """/* Phase3C_1x1_L8_Samples23040_Workers4 */
BENCHMARK(BM_channel_convolution_tpool)
    ->Args({1, 1, 23040, 8, 4})
    ->Args({1, 1, 61440, 16, 16})
    ->Iterations(1000)
    ->Unit(benchmark::kMicrosecond);
"""
CORRECTNESS_ANCHOR = "::testing::Values(100, 1024, 6000, 614400)"
CORRECTNESS_REPLACEMENT = "::testing::Values(100, 1024, 6000, 23040, 614400)"


def replace_once(text: str, old: str, new: str, context: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{context}: expected one occurrence, found {count}")
    return text.replace(old, new, 1)


def patch_benchmark(path: Path) -> bool:
    text = path.read_text()
    if MARKER in text:
        required = (
            "int channel_length = state.range(3);",
            "int num_workers = state.range(4);",
            "init_tpool(num_workers)",
            "Args({1, 1, 23040, 8, 4})",
            "Args({1, 1, 61440, 16, 16})",
            "Iterations(1000)",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial Phase 3C benchmark patch: {missing}")
        print(f"Already patched: {path}")
        return False

    text = replace_once(
        text,
        FUNCTION_ANCHOR,
        FUNCTION_REPLACEMENT,
        "benchmark function",
    )
    text = replace_once(text, TPOOL_ANCHOR, TPOOL_REPLACEMENT, "benchmark tpool")
    text = replace_once(
        text,
        REGISTRATION_ANCHOR,
        REGISTRATION_REPLACEMENT,
        "benchmark registration",
    )
    path.write_text(text)
    print(f"Patched {path}")
    return True


def patch_correctness_test(path: Path) -> bool:
    text = path.read_text()
    if CORRECTNESS_REPLACEMENT in text:
        print(f"Already patched: {path}")
        return False
    text = replace_once(
        text,
        CORRECTNESS_ANCHOR,
        CORRECTNESS_REPLACEMENT,
        "correctness sample set",
    )
    path.write_text(text)
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"usage: {Path(sys.argv[0]).name} BENCHMARK_CPP CORRECTNESS_TEST_CPP",
            file=sys.stderr,
        )
        return 2
    benchmark = Path(sys.argv[1])
    correctness = Path(sys.argv[2])
    if not benchmark.is_file() or not correctness.is_file():
        print("error: both OAI test sources must exist", file=sys.stderr)
        return 2
    try:
        patch_benchmark(benchmark)
        patch_correctness_test(correctness)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
