#!/usr/bin/env python3
"""Initialize the RFsim random generator before configured fading channels."""

from __future__ import annotations

from pathlib import Path
import sys


EARLY_BLOCK = """  // we need to call randominit() for telnet server (use gaussdouble=>uniformrand)
  randominit();
  set_taus_seed(0);
  rfsimulator_readconfig(rfsimulator);
"""
LATE_BLOCK = """  // we need to call randominit() for telnet server (use gaussdouble=>uniformrand)
  randominit();
  set_taus_seed(0);
"""


def replace_once(text: str, old: str, new: str, context: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{context}: expected one occurrence of {old!r}, found {count}"
        )
    return text.replace(old, new, 1)


def patch_file(path: Path) -> bool:
    text = path.read_text()
    if EARLY_BLOCK in text:
        if text.count("  randominit();\n") != 1 or text.count("  set_taus_seed(0);\n") != 1:
            raise RuntimeError("partial RFsim RNG initialization patch")
        print(f"Already patched: {path}")
        return False

    text = replace_once(
        text,
        LATE_BLOCK,
        "",
        "late RNG initialization",
    )
    text = replace_once(
        text,
        "  rfsimulator_readconfig(rfsimulator);\n",
        EARLY_BLOCK,
        "channel configuration",
    )
    path.write_text(text)
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} RFSIMULATOR_CPP", file=sys.stderr)
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
