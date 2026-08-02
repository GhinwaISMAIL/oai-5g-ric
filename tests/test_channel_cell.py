import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "bin" / "channel-cell.py"
SPEC = importlib.util.spec_from_file_location("channel_cell", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


SHOW = """
model 0 rfsimu_channel_enB0 type AWGN:
path loss: 0.000000  noise: -30.000000 rchannel offset: 0    forget factor; 0.000000
----------------
model 1 rfsimu_channel_ue0 type AWGN:
ricean fact.: 1.500000    angle of arrival: 12.000000 (randomized:No)
path loss: 8.000000  noise: -24.000000 rchannel offset: 2    forget factor; 0.250000
----------------
softmodem_5Gue>
"""


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [("noise_power_dB", -24.0), ("ploss", 8.0), ("riceanf", 1.5),
     ("aoa", 12.0), ("offset", 2.0), ("forgetf", 0.25)],
)
def test_read_back_supported_parameters(parameter, expected):
    assert MODULE.observed_value(SHOW, 1, parameter) == expected


def test_missing_model_is_rejected():
    with pytest.raises(RuntimeError, match="model 3"):
        MODULE.model_block(SHOW, 3)


def test_model_identity_is_captured_for_dataset_labels():
    assert MODULE.model_identity(SHOW, 1) == ("rfsimu_channel_ue0", "AWGN")


def test_downlink_selects_active_model_zero_in_target_ue(monkeypatch):
    monkeypatch.setattr(
        MODULE, "run",
        lambda *args: "172.21.0.13" if args[0] == "docker" else "",
    )
    assert MODULE.endpoint(1, "dl", 3) == (
        "ric5g-ue-cell1-3", "172.21.0.13", 0,
    )


def test_uplink_is_rejected_until_connection_identity_is_stable():
    with pytest.raises(RuntimeError, match="UL runtime channel control is disabled"):
        MODULE.endpoint(1, "ul", None)


def test_active_downlink_model_identity_and_value():
    assert MODULE.model_identity(SHOW, 0) == ("rfsimu_channel_enB0", "AWGN")
    assert MODULE.observed_value(SHOW, 0, "noise_power_dB") == -30.0
