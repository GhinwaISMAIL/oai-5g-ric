#!/usr/bin/env python3
"""Give each OAI TDL channel independent delay storage."""

from __future__ import annotations

from pathlib import Path
import sys


OLD_DELAY_BLOCK = """  chan_desc->amps           = calloc(chan_desc->nb_taps, sizeof(double));

  for (int i = 0; i<chan_desc->nb_taps; i++) {
    chan_desc->amps[i]      = pow(10,.1*tdl_amps_dB[i]);
    sum_amps += chan_desc->amps[i];
  }

  for (int i = 0; i<chan_desc->nb_taps; i++) {
    chan_desc->amps[i] /= sum_amps;
    tdl_delays[i] *= DS_TDL;
  }

  chan_desc->delays         = tdl_delays;
"""
NEW_DELAY_BLOCK = """  chan_desc->amps           = calloc(chan_desc->nb_taps, sizeof(double));
  chan_desc->delays         = calloc(chan_desc->nb_taps, sizeof(double));
  chan_desc->free_flags    |= CHANMODEL_FREE_DELAY;

  for (int i = 0; i<chan_desc->nb_taps; i++) {
    chan_desc->amps[i]      = pow(10,.1*tdl_amps_dB[i]);
    sum_amps += chan_desc->amps[i];
  }

  for (int i = 0; i<chan_desc->nb_taps; i++) {
    chan_desc->amps[i] /= sum_amps;
    chan_desc->delays[i] = tdl_delays[i] * DS_TDL;
  }
"""

OLD_DOPPLER_BLOCK = """  chan_desc->max_Doppler                = maxDoppler;
  chan_desc->corr_level                 = corr_level;
"""
NEW_DOPPLER_BLOCK = """  chan_desc->max_Doppler                = maxDoppler;
  chan_desc->Doppler_phase_cur          = calloc(nb_rx, sizeof(*chan_desc->Doppler_phase_cur));
  chan_desc->corr_level                 = corr_level;
"""

def replace_once(text: str, old: str, new: str, context: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{context}: expected one occurrence of {old!r}, found {count}"
        )
    return text.replace(old, new, 1)


def patch_file(path: Path) -> bool:
    text = path.read_text()
    changed = False
    if NEW_DELAY_BLOCK in text:
        if OLD_DELAY_BLOCK in text:
            raise RuntimeError("mixed TDL model patch")
    else:
        text = replace_once(text, OLD_DELAY_BLOCK, NEW_DELAY_BLOCK, "TDL delay storage")
        changed = True

    if NEW_DOPPLER_BLOCK in text:
        if OLD_DOPPLER_BLOCK in text:
            raise RuntimeError("mixed Doppler state patch")
    else:
        text = replace_once(text, OLD_DOPPLER_BLOCK, NEW_DOPPLER_BLOCK, "Doppler phase state")
        changed = True

    if not changed:
        print(f"Already patched: {path}")
        return False
    path.write_text(text)
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} RANDOM_CHANNEL_C", file=sys.stderr)
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
