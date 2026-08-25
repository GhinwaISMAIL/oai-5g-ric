from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "patch-oai-vrtsim-split-telemetry.py"
SPEC = importlib.util.spec_from_file_location("vrtsim_split_patch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

SOURCE = f'''typedef struct {{
{MODULE.STATE_ANCHOR}}} vrtsim_state_t;

/* {MODULE.RUNTIME_MARKER} */

{MODULE.FUNCTION_ANCHOR}  sample_history();
{MODULE.CIRDB_END_ANCHOR}  prepare();
{MODULE.PRE_CONVOLUTION_ANCHOR}  cuda_pipeline();
#else
  cpu_pipeline();
{MODULE.POST_CONVOLUTION_ANCHOR}      write_placeholder();
    }}
{MODULE.POST_WRITE_ANCHOR}  }}
{MODULE.HISTORY_START_ANCHOR}  if (short_history) {{
    copy_short_history();
  }} else {{
    for (int aatx = 0; aatx < nbAnt; aatx++) {{
      c16_t *samples = (c16_t *)samplesVoid[aatx];
{MODULE.HISTORY_END_ANCHOR}}}

static void runtime_helper(void)
{{
}}

{MODULE.LOG_HELPER_ANCHOR}                        int placeholder)
{{
{MODULE.CALL_ANCHOR}    finish_timing();
{MODULE.LOG_CALL_ANCHOR}  }}
}}

void device_init(void)
{{
{MODULE.INITIALIZE_ANCHOR}}}
'''


def test_patch_adds_all_split_stages_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "vrtsim.c"
        path.write_text(SOURCE)

        assert MODULE.patch_file(path) is True
        result = path.read_text()
        assert MODULE.patch_file(path) is False

        assert path.read_text() == result
        assert result.count("VRTSIM_SPLIT_DEBUG_V1") == 1
        assert "split->cirdb_update_us" in result
        assert "split->preparation_us" in result
        assert "split->convolution_us" in result
        assert "split->shared_write_us" in result
        assert "split->history_copy_us" in result
        assert "accounted_us" in result
        assert "residual_us" in result
        assert "last_split_debug_second = -1" in result


def test_patch_requires_runtime_telemetry_first() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "vrtsim.c"
        path.write_text(SOURCE.replace(MODULE.RUNTIME_MARKER, "missing"))

        with pytest.raises(RuntimeError, match="runtime telemetry must be applied"):
            MODULE.patch_file(path)


def test_patch_rejects_partial_existing_marker() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "vrtsim.c"
        path.write_text(SOURCE + "\n/* VRTSIM_SPLIT_DEBUG_V1 */\n")

        with pytest.raises(RuntimeError, match="partial VRTSIM split telemetry"):
            MODULE.patch_file(path)
