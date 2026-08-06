import subprocess
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]


def test_gradient_uses_negative_path_gain_for_attenuation(tmp_path):
    output = tmp_path / "channelmod.conf"

    subprocess.run(
        ["bash", str(REPOSITORY / "bin" / "gen-channelmod.sh"),
         "4", str(output), "gradient", "AWGN"],
        check=True, capture_output=True, text=True,
    )

    text = output.read_text()
    assert text.count("ploss_dB       = 0;") == 2
    assert "ploss_dB       = -10;" in text
    assert "ploss_dB       = -20;" in text
    assert "ploss_dB       = -30;" in text


def test_cell_setup_uses_measurement_image():
    text = (REPOSITORY / "bin" / "cell-setup.sh").read_text()
    ue_config = (REPOSITORY / "etc" / "nr-ue.conf.tmpl").read_text()

    assert text.count("ghinwa555/oai-nr-ue-chan:v4") == 2
    assert "rsrp_offset_dB = -56.0;" in ue_config
    assert "ghinwa555/oai-nr-ue-chan:v2" not in text
