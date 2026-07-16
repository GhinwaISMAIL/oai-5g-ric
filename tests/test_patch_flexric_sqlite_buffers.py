#!/usr/bin/env python3
"""Regression tests for the pinned FlexRIC SQLite writer patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-flexric-sqlite-buffers.py"
SPEC = importlib.util.spec_from_file_location("flexric_sqlite_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


def writer_source(name: str, message_type: str, count_expression: str) -> str:
    serializer = {
        "write_mac_stats": "to_sql_string_mac_ue",
        "write_rlc_stats": "to_sql_string_rlc_rb",
        "write_pdcp_stats": "to_sql_string_pdcp_rb",
        "write_gtp_stats": "to_sql_string_gtp_NGUT",
    }[name]

    return f"""
static
void {name}(sqlite3* db, global_e2_node_id_t const* id, void const* ind)
{{
  {message_type} const* msg = ind;

  char buffer[2048] = {{0}};
  int pos = 0;

  for(size_t i = 0; i < {count_expression}; ++i){{
    pos += {serializer}(id, NULL, 0, buffer + pos, 2048 - pos);
  }}

  insert_db(db, buffer);
}}
"""


class PatchFlexricSqliteBuffersTest(unittest.TestCase):
    def make_source(self) -> str:
        return "#include <assert.h>\n" + "".join(
            (
                writer_source(
                    "write_mac_stats",
                    "mac_ind_msg_t",
                    "ind_msg_mac->len_ue_stats",
                ),
                writer_source(
                    "write_rlc_stats", "rlc_ind_msg_t", "ind_msg_rlc->len"
                ),
                writer_source(
                    "write_pdcp_stats", "pdcp_ind_msg_t", "ind_msg_pdcp->len"
                ),
                writer_source(
                    "write_gtp_stats", "gtp_ind_msg_t", "ind_msg_gtp->len"
                ),
            )
        )

    def test_patches_all_writers_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sqlite3_wrapper.c"
            path.write_text(self.make_source())

            changed = PATCH_MODULE.patch_file(path)
            first_result = path.read_text()
            changed_again = PATCH_MODULE.patch_file(path)

            self.assertEqual(changed, 5)
            self.assertEqual(changed_again, 0)
            self.assertEqual(path.read_text(), first_result)
            self.assertIn("#include <stdlib.h>", first_result)
            self.assertNotIn("char buffer[2048]", first_result)
            self.assertNotIn("2048 - pos", first_result)
            self.assertEqual(first_result.count("char* buffer = calloc("), 4)
            self.assertEqual(first_result.count("size_t pos = 0;"), 4)
            self.assertEqual(first_result.count("free(buffer);"), 4)

            for count_expression in PATCH_MODULE.WRITERS.values():
                self.assertIn(
                    f"(size_t){count_expression} * 1024 + 1", first_result
                )

    def test_rejects_a_partial_existing_patch(self) -> None:
        source = self.make_source().replace(
            "  char buffer[2048] = {0};",
            (
                "  size_t const buffer_len = "
                "(size_t)ind_msg_mac->len_ue_stats * 1024 + 1;"
            ),
            1,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sqlite3_wrapper.c"
            path.write_text(source)

            with self.assertRaisesRegex(RuntimeError, "partial existing patch"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
