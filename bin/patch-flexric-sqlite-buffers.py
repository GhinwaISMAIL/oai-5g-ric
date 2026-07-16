#!/usr/bin/env python3
"""Fix FlexRIC xApp SQLite aggregation buffers.

The upstream writers aggregate one SQL statement per UE/bearer into a fixed
2048-byte buffer, while each serializer requires up to 1024 bytes. Multi-UE
indications therefore exhaust the remaining buffer and assert. Allocate one
serializer-sized segment per record instead.
"""

from __future__ import annotations

from pathlib import Path
import sys


WRITERS = {
    "write_mac_stats": "ind_msg_mac->len_ue_stats",
    "write_rlc_stats": "ind_msg_rlc->len",
    "write_pdcp_stats": "ind_msg_pdcp->len",
    "write_gtp_stats": "ind_msg_gtp->len",
}


def function_bounds(text: str, name: str) -> tuple[int, int]:
    marker = f"void {name}("
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"cannot find {name}")

    opening = text.find("{", start)
    if opening < 0:
        raise RuntimeError(f"cannot find opening brace for {name}")

    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return start, index + 1

    raise RuntimeError(f"cannot find closing brace for {name}")


def replace_once(block: str, old: str, new: str, name: str) -> str:
    count = block.count(old)
    if count != 1:
        raise RuntimeError(
            f"{name}: expected one occurrence of {old!r}, found {count}"
        )
    return block.replace(old, new, 1)


def patch_writer(block: str, name: str, count_expression: str) -> tuple[str, bool]:
    buffer_declaration = (
        f"  size_t const buffer_len = (size_t){count_expression} * 1024 + 1;"
    )

    if buffer_declaration in block:
        required = (
            "  char* buffer = calloc(buffer_len, sizeof(*buffer));",
            "  assert(buffer != NULL);",
            "  size_t pos = 0;",
            "buffer_len - pos",
            "  free(buffer);",
        )
        missing = [line for line in required if line not in block]
        if missing:
            raise RuntimeError(f"{name}: partial existing patch, missing {missing}")
        forbidden = ("char buffer[2048]", "  int pos = 0;", "2048 - pos")
        remaining = [text for text in forbidden if text in block]
        if remaining:
            raise RuntimeError(
                f"{name}: partial existing patch, still contains {remaining}"
            )
        return block, False

    block = replace_once(
        block,
        "  char buffer[2048] = {0};",
        "\n".join(
            (
                buffer_declaration,
                "  char* buffer = calloc(buffer_len, sizeof(*buffer));",
                "  assert(buffer != NULL);",
            )
        ),
        name,
    )
    block = replace_once(block, "  int pos = 0;", "  size_t pos = 0;", name)
    block = replace_once(block, "2048 - pos", "buffer_len - pos", name)
    block = replace_once(
        block,
        "  insert_db(db, buffer);",
        "  insert_db(db, buffer);\n  free(buffer);",
        name,
    )
    return block, True


def patch_file(path: Path) -> int:
    text = path.read_text()
    changed = []

    if "#include <stdlib.h>" not in text:
        first_include = text.find("#include")
        if first_include < 0:
            raise RuntimeError("cannot find include block")
        text = text[:first_include] + "#include <stdlib.h>\n" + text[first_include:]
        changed.append("stdlib include")

    for name, count_expression in WRITERS.items():
        start, end = function_bounds(text, name)
        block, was_changed = patch_writer(
            text[start:end], name, count_expression
        )
        text = text[:start] + block + text[end:]
        if was_changed:
            changed.append(name)

    if changed:
        path.write_text(text)
        print(f"Patched {path}: {', '.join(changed)}")
    else:
        print(f"Already patched: {path}")

    return len(changed)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} SQLITE3_WRAPPER_C", file=sys.stderr)
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
