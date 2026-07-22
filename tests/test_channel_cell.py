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
