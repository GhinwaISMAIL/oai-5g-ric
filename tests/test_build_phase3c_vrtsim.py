from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_dedicated_vrtsim_dockerfile_packages_both_pinned_binaries_and_module() -> None:
    dockerfile = (ROOT / "etc" / "Dockerfile.phase3c-vrtsim").read_text()

    assert "--gNB --nrUE" in dockerfile
    assert "-DOAI_VRTSIM_TAPS_CLIENT=ON" in dockerfile
    assert "/opt/oai-nr-ue/bin/nr-uesoftmodem" in dockerfile
    assert "/opt/oai-gnb/bin/nr-softmodem" in dockerfile
    assert dockerfile.count("/usr/local/lib/libvrtsim.so") >= 4
    assert dockerfile.count('grep -q "not found"') == 4


def test_build_script_is_pinned_and_applies_only_required_phase3c_patches() -> None:
    script = (ROOT / "bin" / "build-phase3c-vrtsim-images.sh").read_text()

    assert "70508ebaf52f2aae420566d380c6537f2efb9f0c" in script
    assert "patch-oai-ue-radio-measurements.py" in script
    assert "patch-oai-rfsim-noise-scaling.py" in script
    assert "patch-oai-vrtsim-cirdb-telemetry.py" in script
    assert "patch-oai-vrtsim-runtime-telemetry.py" in script
    assert "patch-oai-vrtsim-split-telemetry.py" in script
    assert "Dockerfile.phase3c-vrtsim" in script
    assert script.count("docker run --rm") == 2
