#!/usr/bin/env python3
"""Let the FlexRIC monitor xApp store the whole E2 stream (measured on reservation 320310, 22 Oct 2026).

Usage: patch-flexric-xapp-capture.py SQLITE3_WRAPPER_C XAPP_MONITOR_C

1. sqlite3_wrapper.c: synchronous=off and a larger WAL checkpoint interval. With synchronous=normal
   the single DB writer waited on fsync (spinning disks) and stored about 70 report times per second.
2. xapp_gtp_mac_rlc_pdcp_moni.c: 10 ms report interval, the slowest this build accepts. At 1_ms the xApp
   received 1000 reports per second and the writer stored about 7% of them.
Both edits fail loudly if the text is not found exactly once.
"""
import sys
from pathlib import Path


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected one match for {old!r}, found {text.count(old)}")
    return text.replace(old, new, 1)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    wrapper, monitor = Path(sys.argv[1]), Path(sys.argv[2])
    wrapper_text = replace_once(
        wrapper.read_text(),
        '"pragma synchronous=normal"',
        '"pragma synchronous=off; pragma wal_autocheckpoint=20000"')
    monitor_text = monitor.read_text()
    for index in range(4):
        monitor_text = replace_once(
            monitor_text,
            f'const char* i_{index} = "1_ms";',
            f'const char* i_{index} = "10_ms";')
    wrapper.write_text(wrapper_text)
    monitor.write_text(monitor_text)
    print(f"patched {wrapper} and {monitor}")


if __name__ == "__main__":
    main()
