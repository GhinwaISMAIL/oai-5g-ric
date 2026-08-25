from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "patch-oai-vrtsim-runtime-telemetry.py"
SPEC = importlib.util.spec_from_file_location("vrtsim_runtime_patch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

SOURCE = f'''typedef struct {{
{MODULE.STATE_ANCHOR}}} vrtsim_state_t;

{MODULE.HELPER_ANCHOR}    int placeholder)
{{
{MODULE.CALL_ANCHOR}}}

void device_init(void)
{{
{MODULE.INITIALIZE_ANCHOR}}}
'''


def test_patch_adds_complete_runtime_telemetry_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "vrtsim.c"
        path.write_text(SOURCE)

        assert MODULE.patch_file(path) is True
        result = path.read_text()
        assert MODULE.patch_file(path) is False

        assert path.read_text() == result
        assert result.count("VRTSIM_RUNTIME_DEBUG_V1") == 1
        assert "channel_processing_us=%.3f" in result
        assert "average_tx_budget_us=%.3f" in result
        assert "tx_samples_late=%llu" in result
        assert "rx_samples_late=%llu" in result
        assert "tx_samples_total=%llu" in result
        assert "rx_samples_total=%llu" in result
        assert "last_runtime_debug_second = -1" in result


def test_patch_rejects_partial_existing_marker() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "vrtsim.c"
        path.write_text(SOURCE + "\n/* VRTSIM_RUNTIME_DEBUG_V1 */\n")

        with pytest.raises(RuntimeError, match="partial VRTSIM runtime telemetry"):
            MODULE.patch_file(path)
