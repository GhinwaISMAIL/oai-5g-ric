#!/usr/bin/env python3
"""Regression tests for the pinned OAI RFsim RNG initialization patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPOSITORY / "bin" / "patch-oai-rfsim-rng-init.py"
SPEC = importlib.util.spec_from_file_location("oai_rfsim_rng_patch", PATCH_SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
PATCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH_MODULE)


SOURCE = """rfsimulator->beam_ctrl = new rfsim_beam_ctrl_t;
  rfsimulator_readconfig(rfsimulator);

  AssertFatal((rfsimulator->epollfd = epoll_create1(0)) != -1, "failed");
  // we need to call randominit() for telnet server (use gaussdouble=>uniformrand)
  randominit();
  set_taus_seed(0);
  add_telnet_commands();
"""


class PatchOaiRfsimRngInitTest(unittest.TestCase):
    def test_moves_rng_initialization_before_channel_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulator.cpp"
            path.write_text(SOURCE)

            self.assertTrue(PATCH_MODULE.patch_file(path))
            result = path.read_text()
            self.assertFalse(PATCH_MODULE.patch_file(path))

            self.assertEqual(path.read_text(), result)
            self.assertLess(result.index("randominit();"), result.index("rfsimulator_readconfig"))
            self.assertEqual(result.count("randominit();"), 1)
            self.assertEqual(result.count("set_taus_seed(0);"), 1)

    def test_rejects_missing_late_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulator.cpp"
            path.write_text(SOURCE.replace(PATCH_MODULE.LATE_BLOCK, ""))

            with self.assertRaisesRegex(RuntimeError, "late RNG initialization"):
                PATCH_MODULE.patch_file(path)


if __name__ == "__main__":
    unittest.main()
