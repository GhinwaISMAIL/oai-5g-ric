#!/usr/bin/env python3
"""Add an explicit RSRP scale offset to the pinned OAI RF simulator."""

from __future__ import annotations

from pathlib import Path
import sys


MARKER = "RFSIMU_RSRP_OFFSET"


def replace_once(text: str, old: str, new: str, context: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{context}: expected one occurrence of {old!r}, found {count}"
        )
    return text.replace(old, new, 1)


def patch_file(path: Path) -> bool:
    text = path.read_text()
    if MARKER in text:
        required = (
            '"rsrp_offset_dB"',
            "rsrp_offset_dB;",
            "rfsimulator->rsrp_offset_dB =",
            "openair0_cfg->rx_gain[0] = -rfsimulator->rsrp_offset_dB;",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial RFsim RSRP calibration patch: missing {missing}")
        print(f"Already patched: {path}")
        return False

    text = replace_once(
        text,
        '#define RFSIMU_PROP_DELAY "prop_delay"\n',
        '#define RFSIMU_PROP_DELAY "prop_delay"\n'
        '#define RFSIMU_RSRP_OFFSET "rsrp_offset_dB"\n',
        "parameter name",
    )
    text = replace_once(
        text,
        '  DOUBLEPARAM(RFSIMU_PROP_DELAY,        "<propagation delay in ms>\\n",              simOpt, NULL,                             0.0),                   \\\n',
        '  DOUBLEPARAM(RFSIMU_PROP_DELAY,        "<propagation delay in ms>\\n",              simOpt, NULL,                             0.0),                   \\\n'
        '  DOUBLEPARAM(RFSIMU_RSRP_OFFSET,       "<RSRP reporting scale offset in dB>\\n",    simOpt, NULL,                             0.0),                   \\\n',
        "parameter definition",
    )
    text = replace_once(
        text,
        "  double prop_delay_ms;\n",
        "  double prop_delay_ms;\n  double rsrp_offset_dB;\n",
        "state field",
    )
    text = replace_once(
        text,
        "  rfsimulator->prop_delay_ms = *(gpd(rfsimuParam, sizeofArray(rfsimuParams), RFSIMU_PROP_DELAY)->dblptr);\n",
        "  rfsimulator->prop_delay_ms = *(gpd(rfsimuParam, sizeofArray(rfsimuParams), RFSIMU_PROP_DELAY)->dblptr);\n"
        "  rfsimulator->rsrp_offset_dB = *(gpd(rfsimuParam, sizeofArray(rfsimuParams), RFSIMU_RSRP_OFFSET)->dblptr);\n",
        "parameter read",
    )
    text = replace_once(
        text,
        "  openair0_cfg->rx_gain[0] = 0;\n",
        "  openair0_cfg->rx_gain[0] = -rfsimulator->rsrp_offset_dB;\n"
        "  LOG_I(HW, \"RSRP reporting scale offset %.3f dB\\n\", rfsimulator->rsrp_offset_dB);\n",
        "RFsim gain metadata",
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
