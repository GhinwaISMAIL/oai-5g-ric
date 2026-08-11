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


def test_cell_setup_preserves_awgn_images_and_uses_fading_images():
    text = (REPOSITORY / "bin" / "cell-setup.sh").read_text()
    node_setup = (REPOSITORY / "bin" / "node-setup.sh").read_text()
    profile = (REPOSITORY / "profile.py").read_text()
    ue_config = (REPOSITORY / "etc" / "nr-ue.conf.tmpl").read_text()

    assert 'AWGN)' in text
    assert 'DEFAULT_GNB_IMAGE="ghinwa555/oai-gnb-e2-chan:v2"' in text
    assert 'DEFAULT_UE_IMAGE="ghinwa555/oai-nr-ue-chan:v4"' in text
    assert 'DEFAULT_GNB_IMAGE="ghinwa555/oai-gnb-e2-chan:v3"' in text
    assert 'DEFAULT_UE_IMAGE="ghinwa555/oai-nr-ue-chan:v5"' in text
    assert 'TDL_A|TDL_B|TDL_C|EPA|EVA)' in text
    assert 'unsupported channel type' in text
    assert 'GNB_IMAGE="${OAI_GNB_IMAGE:-${DEFAULT_GNB_IMAGE}}"' in text
    assert 'UE_IMAGE="${OAI_UE_IMAGE:-${DEFAULT_UE_IMAGE}}"' in text
    assert "image: ${GNB_IMAGE}" in text
    assert "image: ${UE_IMAGE}" in text
    assert "rsrp_offset_dB = -56.0;" in ue_config
    assert "ghinwa555/oai-nr-ue-chan:v2" not in text
    assert '"EPA", "EVA")' in profile
    assert '"EVA", "ETU")' not in profile
    assert 'defineParameter("tdl_delay_spread_ns"' in profile
    assert "params.tdl_delay_spread_ns not in (10, 30, 100)" in profile
    assert 'CHANMOD_DS_TDL_NS="$TDL_DELAY_SPREAD_NS"' in node_setup
    assert '"${CHANMOD_DS_TDL_NS}"' in text


def test_tdl_models_use_nonzero_delay_spread(tmp_path):
    for channel_type in ("TDL_A", "TDL_B", "TDL_C"):
        output = tmp_path / f"{channel_type}.conf"
        subprocess.run(
            [
                "bash",
                str(REPOSITORY / "bin" / "gen-channelmod.sh"),
                "1",
                str(output),
                "uniform",
                channel_type,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        text = output.read_text()
        assert text.count("ds_tdl         = 0.00000003;") == 2


def test_tdl_models_use_selected_delay_spread(tmp_path):
    expected = {
        "10": "0.00000001",
        "30": "0.00000003",
        "100": "0.00000010",
    }

    for delay_ns, delay_seconds in expected.items():
        output = tmp_path / f"TDL_B-{delay_ns}.conf"
        subprocess.run(
            [
                "bash",
                str(REPOSITORY / "bin" / "gen-channelmod.sh"),
                "1",
                str(output),
                "uniform",
                "TDL_B",
                delay_ns,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert output.read_text().count(
            f"ds_tdl         = {delay_seconds};"
        ) == 2


def test_invalid_tdl_delay_spread_is_rejected(tmp_path):
    result = subprocess.run(
        [
            "bash",
            str(REPOSITORY / "bin" / "gen-channelmod.sh"),
            "1",
            str(tmp_path / "invalid.conf"),
            "uniform",
            "TDL_B",
            "50",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "must be 10, 30, or 100 ns" in result.stderr


def test_awgn_keeps_zero_tdl_delay_spread(tmp_path):
    output = tmp_path / "AWGN.conf"
    subprocess.run(
        [
            "bash",
            str(REPOSITORY / "bin" / "gen-channelmod.sh"),
            "1",
            str(output),
            "uniform",
            "AWGN",
            "100",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert output.read_text().count("ds_tdl         = 0;") == 2
