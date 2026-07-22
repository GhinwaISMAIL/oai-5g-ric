#!/usr/bin/env python3
"""Regression tests for FlexRIC dual-clock SQLite rows."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-flexric-receipt-timestamps.py"
SPEC = importlib.util.spec_from_file_location("flexric_receipt_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


def table_source(table: str) -> str:
    return f'''char* sql = "CREATE TABLE {table}(tstamp INT CHECK(tstamp > 0),";
'''


def serializer_source(name: str, table: str) -> str:
    return f'''
static
int {name}(global_e2_node_id_t const* id, void* stats, int64_t tstamp, char* out, size_t out_len)
{{
  int rc = snprintf(out, out_len,
      "INSERT INTO {table} VALUES("
      "%ld," // tstamp
      "%d"
      ");"
      , tstamp
      , id->type
      );
  return rc;
}}
'''


def writer_source(name: str, serializer: str, message: str) -> str:
    count = (f"{message}->len_ue_stats" if message == "ind_msg_mac"
             else f"{message}->len")
    return f'''
static
void {name}(sqlite3* db, global_e2_node_id_t const* id, void const* ind)
{{
  void const* {message} = ind;

  size_t const buffer_len = (size_t){count} * 1024 + 1;
  char* buffer = calloc(buffer_len, sizeof(*buffer));
  size_t pos = 0;
  for(size_t i = 0; i < {count}; ++i){{
    pos += {serializer}(id, NULL, {message}->tstamp, buffer + pos, buffer_len - pos);
  }}
  insert_db(db, buffer);
  free(buffer);
}}
'''


class PatchFlexricReceiptTimestampsTest(unittest.TestCase):
    def make_source(self) -> str:
        parts = ['#include "../../../util/time_now_us.h"\n']
        for table, config in PATCH_MODULE.TABLES.items():
            parts.extend((
                table_source(table),
                serializer_source(config["serializer"], table),
                writer_source(
                    config["writer"], config["serializer"], config["message"]
                ),
            ))
        return "".join(parts)

    def test_adds_dual_clocks_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sqlite3_wrapper.c"
            path.write_text(self.make_source())

            changed = PATCH_MODULE.patch_file(path)
            result = path.read_text()
            changed_again = PATCH_MODULE.patch_file(path)

            self.assertEqual(changed, 12)
            self.assertEqual(changed_again, 0)
            self.assertEqual(path.read_text(), result)
            self.assertEqual(
                result.count("recv_tstamp INT CHECK(recv_tstamp > 0)"), 4
            )
            self.assertEqual(
                result.count(
                    "int64_t const recv_tstamp = (int64_t)time_now_us();"
                ),
                4,
            )
            self.assertEqual(result.count("tstamp, recv_tstamp, buffer + pos"), 4)
            self.assertEqual(result.count("int64_t tstamp, int64_t recv_tstamp"), 4)
            self.assertEqual(result.count(", recv_tstamp\n      , id->type"), 4)

    def test_rejects_partial_existing_patch(self) -> None:
        source = self.make_source().replace(
            "int64_t tstamp, char* out",
            "int64_t tstamp, int64_t recv_tstamp, char* out",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sqlite3_wrapper.c"
            path.write_text(source)
            with self.assertRaisesRegex(RuntimeError, "receipt timestamp"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
