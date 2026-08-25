from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "bin" / "generate-phase3c-cirdb-identity-control.py"
SPEC = importlib.util.spec_from_file_location("phase3c_identity_control", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_identity_control_is_one_unity_and_seven_zero_complex_taps(
    tmp_path: Path,
) -> None:
    hashes = MODULE.generate(tmp_path)
    values = struct.unpack("<16f", (tmp_path / "cir_db.bin").read_bytes())

    assert values == (1.0, 0.0) + (0.0,) * 14
    assert (tmp_path / "cir_db.bin").stat().st_size == 64
    assert "L: 8" in (tmp_path / "vrtsim.yaml").read_text()
    assert "S: 1" in (tmp_path / "vrtsim.yaml").read_text()
    assert hashes == {
        name: MODULE.sha256_bytes((tmp_path / name).read_bytes())
        for name in ("cir_db.bin", "vrtsim.yaml")
    }


def test_identity_control_refuses_to_replace_different_artifact(
    tmp_path: Path,
) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "cir_db.bin").write_bytes(b"different")

    with pytest.raises(RuntimeError, match="refusing to replace"):
        MODULE.generate(tmp_path)
