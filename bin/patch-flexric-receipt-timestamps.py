#!/usr/bin/env python3
"""Add a core receipt timestamp to FlexRIC SQLite indication tables.

The service-model ``tstamp`` follows RFsim/radio time and can advance more
slowly than wall time.  MGEN and channel-control events use host wall time, so
the two clocks must not be joined directly.  This patch preserves ``tstamp``
and adds ``recv_tstamp`` (microseconds since the Unix epoch on the core) to the
MAC, RLC, PDCP and GTP rows written by the multi-service monitor.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys


TABLES = {
    "MAC_UE": {
        "serializer": "to_sql_string_mac_ue",
        "writer": "write_mac_stats",
        "message": "ind_msg_mac",
    },
    "RLC_bearer": {
        "serializer": "to_sql_string_rlc_rb",
        "writer": "write_rlc_stats",
        "message": "ind_msg_rlc",
    },
    "PDCP_bearer": {
        "serializer": "to_sql_string_pdcp_rb",
        "writer": "write_pdcp_stats",
        "message": "ind_msg_pdcp",
    },
    "GTP_NGUT": {
        "serializer": "to_sql_string_gtp_NGUT",
        "writer": "write_gtp_stats",
        "message": "ind_msg_gtp",
    },
}


def function_bounds(text: str, name: str) -> tuple[int, int]:
    match = re.search(rf"\b(?:int|void)\s+{re.escape(name)}\s*\(", text)
    if match is None:
        raise RuntimeError(f"cannot find {name}")
    start = match.start()
    opening = text.find("{", match.end())
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


def replace_once(text: str, old: str, new: str, context: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{context}: expected one occurrence of {old!r}, found {count}"
        )
    return text.replace(old, new, 1)


def patch_table(text: str, table: str) -> tuple[str, bool]:
    existing = (
        f"CREATE TABLE {table}(tstamp INT CHECK(tstamp > 0),"
        "recv_tstamp INT CHECK(recv_tstamp > 0),"
    )
    if existing in text:
        return text, False

    original = f"CREATE TABLE {table}(tstamp INT CHECK(tstamp > 0),"
    return replace_once(text, original, existing, table), True


def patch_serializer(block: str, name: str, table: str) -> tuple[str, bool]:
    argument_order = re.compile(
        r"(?m)^[ \t]*,[ \t]*tstamp[^\n]*\n"
        r"[ \t]*,[ \t]*recv_tstamp[^\n]*\n"
        r"[ \t]*,[ \t]*id->type"
    )
    if "int64_t recv_tstamp" in block:
        required = (
            "int64_t tstamp, int64_t recv_tstamp, char* out",
            "recv_tstamp",
        )
        missing = [item for item in required if item not in block]
        if missing:
            raise RuntimeError(f"{name}: partial receipt-time patch: {missing}")
        if argument_order.search(block) is None:
            raise RuntimeError(f"{name}: receipt timestamp argument is out of order")
        return block, False

    block = replace_once(
        block,
        "int64_t tstamp, char* out",
        "int64_t tstamp, int64_t recv_tstamp, char* out",
        name,
    )

    insert = block.find(f'INSERT INTO {table} VALUES(')
    if insert < 0:
        raise RuntimeError(f"{name}: cannot find INSERT for {table}")
    value = block.find('"%ld,"', insert)
    if value < 0:
        raise RuntimeError(f"{name}: cannot find source timestamp value")
    line_end = block.find("\n", value)
    if line_end < 0:
        raise RuntimeError(f"{name}: malformed source timestamp line")
    indent = re.match(r"\s*", block[block.rfind("\n", 0, value) + 1:value]).group(0)
    block = (
        block[:line_end + 1]
        + f'{indent}"%ld," // recv_tstamp\n'
        + block[line_end + 1:]
    )

    values_start = block.find('");"', insert)
    if values_start < 0:
        raise RuntimeError(f"{name}: cannot find end of INSERT format")
    argument = re.search(
        r"(?m)^([ \t]*),[ \t]*tstamp[ \t]*(?:[^\n]*)$",
        block[values_start:],
    )
    if argument is None:
        raise RuntimeError(f"{name}: cannot find source timestamp argument")
    argument_end = values_start + argument.end()
    argument_indent = argument.group(1)
    block = (
        block[:argument_end]
        + f"\n{argument_indent}, recv_tstamp"
        + block[argument_end:]
    )
    if argument_order.search(block) is None:
        raise RuntimeError(f"{name}: failed to preserve serializer argument order")
    return block, True


def patch_writer(block: str, name: str, message: str) -> tuple[str, bool]:
    declaration = "  int64_t const recv_tstamp = (int64_t)time_now_us();"
    if declaration in block:
        expected_call = f"{message}->tstamp, recv_tstamp, buffer + pos"
        if expected_call not in block:
            raise RuntimeError(f"{name}: receipt timestamp is not serialized")
        return block, False

    buffer_marker = "  size_t const buffer_len ="
    block = replace_once(
        block,
        buffer_marker,
        f"{declaration}\n\n{buffer_marker}",
        name,
    )
    block = replace_once(
        block,
        f"{message}->tstamp, buffer + pos",
        f"{message}->tstamp, recv_tstamp, buffer + pos",
        name,
    )
    return block, True


def patch_file(path: Path) -> int:
    text = path.read_text()
    changed: list[str] = []

    if '"../../../util/time_now_us.h"' not in text:
        raise RuntimeError("time_now_us.h include is missing")

    for table, config in TABLES.items():
        text, was_changed = patch_table(text, table)
        if was_changed:
            changed.append(f"{table} schema")

        start, end = function_bounds(text, config["serializer"])
        block, was_changed = patch_serializer(
            text[start:end], config["serializer"], table
        )
        text = text[:start] + block + text[end:]
        if was_changed:
            changed.append(config["serializer"])

        start, end = function_bounds(text, config["writer"])
        block, was_changed = patch_writer(
            text[start:end], config["writer"], config["message"]
        )
        text = text[:start] + block + text[end:]
        if was_changed:
            changed.append(config["writer"])

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
