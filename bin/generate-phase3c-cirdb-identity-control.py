#!/usr/bin/env python3
"""Generate the frozen eight-tap unity CIRDB transport control."""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

SIDECAR = """entries:
- model_id: 1
  n_tx: 1
  n_rx: 1
  L: 8
  S: 1
  fs_hz: 46080000.0
  snapshot_dt_s: 0.002
  ds_ns: 30.0
  speed_mps: 1.5
  pair_order: 0
  offset_bytes: 0
  nbytes: 64
"""


def identity_binary() -> bytes:
    values = [1.0, 0.0] + [0.0] * 14
    return struct.pack("<16f", *values)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_frozen(path: Path, value: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != value:
            raise RuntimeError(f"refusing to replace a different artifact: {path}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def generate(output: Path) -> dict[str, str]:
    output.mkdir(parents=True, exist_ok=True)
    binary = identity_binary()
    sidecar = SIDECAR.encode("utf-8")
    write_frozen(output / "cir_db.bin", binary)
    write_frozen(output / "vrtsim.yaml", sidecar)
    return {
        "cir_db.bin": sha256_bytes(binary),
        "vrtsim.yaml": sha256_bytes(sidecar),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    args = parser.parse_args()
    try:
        hashes = generate(Path(args.output).resolve())
    except (OSError, RuntimeError) as error:
        parser.error(str(error))
    for name, digest in hashes.items():
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
