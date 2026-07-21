#!/usr/bin/env python3
"""Disable FlexRIC's unbounded per-indication iApp file trace.

The pinned FlexRIC revision writes every indication handled by the stdout iApp
to a relative ``log.txt``.  With multi-cell MAC/RLC/PDCP/GTP subscriptions that
file grows continuously and can fill the core node, which then makes SQLite
fail with SQLITE_FULL.  Normal RIC stdout remains available through
``/local/logs/nearRT-RIC.log``; only the redundant high-rate file trace is sent
to ``/dev/null``.
"""

from __future__ import annotations

from pathlib import Path
import sys


ORIGINAL = 'const char* file_path = "log.txt";'
DISABLED = 'const char* file_path = "/dev/null";'


def patch_file(path: Path) -> bool:
    text = path.read_text()
    if DISABLED in text:
        if ORIGINAL in text:
            raise RuntimeError("source contains both original and disabled paths")
        print(f"Already patched: {path}")
        return False

    count = text.count(ORIGINAL)
    if count != 1:
        raise RuntimeError(
            f"expected exactly one {ORIGINAL!r}, found {count} in {path}"
        )

    path.write_text(text.replace(ORIGINAL, DISABLED, 1))
    print(f"Patched {path}: disabled unbounded iApp log.txt")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} STDOUT_C", file=sys.stderr)
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
