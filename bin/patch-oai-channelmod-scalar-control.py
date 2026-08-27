#!/usr/bin/env python3
"""Keep RFsim taps fixed when channelmod changes scalar gain or noise."""

from __future__ import annotations

import sys
from pathlib import Path

ORIGINAL_HEADER = """static int channelmod_modify_cmd(char *buff, int debug, telnet_printfunc_t prnt) {
  char *param=NULL, *value=NULL;
  int cd_id= -1;
"""
PATCHED_HEADER = """static int channelmod_modify_cmd(char *buff, int debug, telnet_printfunc_t prnt) {
  char *param=NULL, *value=NULL;
  int cd_id= -1;
  int regenerate_channel = 1;
"""
ORIGINAL_SCALAR_BLOCK = """    } else if ( strcmp(param,"ploss") == 0) {
      double dbl = atof(value);
      defined_channels[cd_id]->path_loss_dB=dbl;
    } else if ( strcmp(param,"noise_power_dB") == 0) {
      double dbl = atof(value);
      defined_channels[cd_id]->noise_power_dB=dbl;
"""
PATCHED_SCALAR_BLOCK = """    } else if ( strcmp(param,"ploss") == 0) {
      double dbl = atof(value);
      defined_channels[cd_id]->path_loss_dB=dbl;
      regenerate_channel = 0;
    } else if ( strcmp(param,"noise_power_dB") == 0) {
      double dbl = atof(value);
      defined_channels[cd_id]->noise_power_dB=dbl;
      regenerate_channel = 0;
"""
ORIGINAL_TAIL = """    display_channelmodel(defined_channels[cd_id],debug,prnt);
    free(param);
    free(value);
    random_channel(defined_channels[cd_id],false);
"""
PATCHED_TAIL = """    display_channelmodel(defined_channels[cd_id],debug,prnt);
    free(param);
    free(value);
    if (regenerate_channel)
      random_channel(defined_channels[cd_id],false);
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
    if PATCHED_HEADER in text:
        if text.count(PATCHED_SCALAR_BLOCK) != 1 or text.count(PATCHED_TAIL) != 1:
            raise RuntimeError("partial channelmod scalar-control patch")
        print(f"Already patched: {path}")
        return False

    text = replace_once(
        text,
        ORIGINAL_HEADER,
        PATCHED_HEADER,
        "channelmod modify header",
    )
    text = replace_once(
        text,
        ORIGINAL_SCALAR_BLOCK,
        PATCHED_SCALAR_BLOCK,
        "channelmod scalar parameters",
    )
    text = replace_once(
        text,
        ORIGINAL_TAIL,
        PATCHED_TAIL,
        "channelmod regeneration",
    )
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
