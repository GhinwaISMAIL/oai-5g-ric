#!/usr/bin/env python3
"""Balance OAI CPU channel-pipeline sample ranges across worker jobs."""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "Phase 3C: contiguous balanced sample partition"
ANCHOR = """  const int batch_size = 4000;
  cf_t work_buffer[batch_size] __attribute__((aligned(64)));
  for (int batch_start = job_index * batch_size; batch_start < num_samples; batch_start += batch_size * num_jobs) {
    int batch_end = min(batch_start + batch_size, num_samples);
"""
REPLACEMENT = """  const int batch_size = 4000;
  cf_t work_buffer[batch_size] __attribute__((aligned(64)));
  /* Phase 3C: contiguous balanced sample partition. Each output sample is
   * independent, so assigning an equal contiguous interval to each worker
   * preserves the per-sample arithmetic while avoiding a long final batch on
   * one worker when num_samples is not divisible by batch_size * num_jobs. */
  const int job_start = (int)(((size_t)num_samples * job_index) / num_jobs);
  const int job_end = (int)(((size_t)num_samples * (job_index + 1)) / num_jobs);
  for (int batch_start = job_start; batch_start < job_end; batch_start += batch_size) {
    int batch_end = min(batch_start + batch_size, job_end);
"""


def patch_file(path: Path) -> bool:
    text = path.read_text()
    if MARKER in text:
        required = (
            "const int job_start =",
            "const int job_end =",
            "batch_start < job_end",
            "min(batch_start + batch_size, job_end)",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial channel-pipeline balance patch: {missing}")
        print(f"Already patched: {path}")
        return False

    count = text.count(ANCHOR)
    if count != 1:
        raise RuntimeError(
            f"channel-pipeline balance anchor: expected one occurrence, found {count}"
        )
    path.write_text(text.replace(ANCHOR, REPLACEMENT, 1))
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} CHANNEL_PIPELINE_C", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 2
    try:
        patch_file(path)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
