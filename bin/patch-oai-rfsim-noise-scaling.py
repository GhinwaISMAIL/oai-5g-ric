#!/usr/bin/env python3
"""Correct power-domain noise scaling in the pinned OAI RF simulator."""

from __future__ import annotations

import math
from pathlib import Path
import sys


MARKER = "RFSIM_NOISE_POWER_SCALING_V1"
REFERENCE_RMS = 256.0
OLD_NOISE_LINE = (
    "  const double noise_per_sample = "
    "pow(10, channelDesc->noise_power_dB / 10.0) * 256;\n"
)
NEW_NOISE_BLOCK = (
    "  // RFSIM_NOISE_POWER_SCALING_V1: noise_power_dB is a power-domain value,\n"
    "  // while the Gaussian samples below are scaled by an RMS amplitude.\n"
    "  const double noise_per_sample = "
    "pow(10, channelDesc->noise_power_dB / 20.0) * 256;\n"
)
OLD_LOG_BLOCK = """        channelDesc->path_loss_dB,
        10 * log10(noise_per_sample));
"""
NEW_LOG_BLOCK = """        channelDesc->path_loss_dB,
        channelDesc->noise_power_dB);
"""
OLD_LOG_FORMAT = "channel path loss %f, noise coeff: %f \\n"
NEW_LOG_FORMAT = "channel path loss %f, noise power: %f dB \\n"


def noise_rms(noise_power_db: float, reference_rms: float = REFERENCE_RMS) -> float:
    """Return the per-component RMS amplitude for a power value in dB."""

    return reference_rms * math.pow(10.0, noise_power_db / 20.0)


def relative_power_db(rms: float, reference_rms: float = REFERENCE_RMS) -> float:
    """Recover relative power in dB from a positive RMS amplitude."""

    if rms <= 0.0 or reference_rms <= 0.0:
        raise ValueError("RMS amplitudes must be positive")
    return 20.0 * math.log10(rms / reference_rms)


def legacy_equivalent_corrected_db(legacy_noise_db: float) -> float:
    """Map a legacy /10 command to the corrected /20 command with equal RMS."""

    return 2.0 * legacy_noise_db


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
            "channelDesc->noise_power_dB / 20.0",
            NEW_LOG_FORMAT,
            "channelDesc->noise_power_dB);",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial RFsim noise scaling patch: missing {missing}")
        print(f"Already patched: {path}")
        return False

    text = replace_once(text, OLD_NOISE_LINE, NEW_NOISE_BLOCK, "noise RMS scaling")
    text = replace_once(text, OLD_LOG_FORMAT, NEW_LOG_FORMAT, "noise log label")
    text = replace_once(text, OLD_LOG_BLOCK, NEW_LOG_BLOCK, "noise log value")
    path.write_text(text)
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} APPLY_CHANNELMOD_C", file=sys.stderr)
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
