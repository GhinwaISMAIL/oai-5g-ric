from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "patch-oai-vrtsim-cirdb-telemetry.py"
SPEC = importlib.util.spec_from_file_location("vrtsim_cirdb_patch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

SOURCE = f'''typedef struct cirdb_provider_s {{
{MODULE.STATE_ANCHOR}}} extra_text;

{MODULE.HELPER_ANCHOR}
void cirdb_update(cirdb_g *G, uint64_t ns_since_start)
{{
{MODULE.UPDATE_ANCHOR}}}
'''


def test_patch_adds_complete_telemetry_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "cirdb_provider.c"
        path.write_text(SOURCE)

        assert MODULE.patch_file(path) is True
        result = path.read_text()
        assert MODULE.patch_file(path) is False

        assert path.read_text() == result
        assert result.count("VRTSIM_CIRDB_DEBUG_V1") == 1
        assert "expected_cirdb_step=%lld" in result
        assert "current_cirdb_snapshot_index=%u" in result
        assert "applied_cirdb_updates=%" in result
        assert "skipped_cirdb_snapshots=%" in result
        assert "maximum_consecutive_skipped_cirdb_snapshots=%" in result
        assert "current_tap_energy_linear=%.17g" in result


def test_patch_rejects_partial_existing_marker() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "cirdb_provider.c"
        path.write_text(SOURCE + "\n/* VRTSIM_CIRDB_DEBUG_V1 */\n")

        with pytest.raises(RuntimeError, match="partial CIRDB telemetry patch"):
            MODULE.patch_file(path)
