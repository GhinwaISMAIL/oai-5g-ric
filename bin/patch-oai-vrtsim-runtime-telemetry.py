#!/usr/bin/env python3
"""Add periodic VRTSIM runtime timing and lateness telemetry to pinned OAI."""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "VRTSIM_RUNTIME_DEBUG_V1"
STATE_ANCHOR = "  histogram_t chanmod_histogram;\n  int chanmod;\n"
HELPER_ANCHOR = "static int vrtsim_write(openair0_device_t *device,\n"
CALL_ANCHOR = """    histogram_add(&vrtsim_state->chanmod_histogram, microseconds);
    return num_samples_processed;
"""
INITIALIZE_ANCHOR = """  vrtsim_state->tx_num_channels = openair0_cfg->tx_num_channels;
  vrtsim_state->rx_num_channels = openair0_cfg->rx_num_channels;
"""

STATE_REPLACEMENT = """  histogram_t chanmod_histogram;
  int64_t last_runtime_debug_second;
  int chanmod;
"""

HELPER = r'''static void vrtsim_log_runtime_debug(vrtsim_state_t *vrtsim_state,
                                     openair0_timestamp_t timestamp,
                                     double channel_processing_us)
{
  const int64_t elapsed_second = (int64_t)(timestamp / vrtsim_state->sample_rate);
  if (elapsed_second <= vrtsim_state->last_runtime_debug_second)
    return;
  vrtsim_state->last_runtime_debug_second = elapsed_second;
  const uint64_t current_sample = shm_td_iq_channel_get_current_sample(vrtsim_state->channel);
  const char *role_name = vrtsim_state->role == ROLE_SERVER ? ROLE_SERVER_STRING : ROLE_CLIENT_STRING;
  LOG_I(HW,
        "VRTSIM_RUNTIME_DEBUG_V1 role=%s elapsed_second=%lld tx_timestamp=%lld current_sample=%llu "
        "channel_processing_us=%.3f average_tx_budget_us=%.3f tx_samples_late=%llu tx_samples_total=%llu "
        "rx_samples_late=%llu rx_samples_total=%llu tx_early=%llu rx_early=%llu\n",
        role_name,
        (long long)elapsed_second,
        (long long)timestamp,
        (unsigned long long)current_sample,
        channel_processing_us,
        vrtsim_state->tx_timing.average_tx_budget,
        (unsigned long long)vrtsim_state->tx_timing.tx_samples_late,
        (unsigned long long)vrtsim_state->tx_timing.tx_samples_total,
        (unsigned long long)vrtsim_state->rx_samples_late,
        (unsigned long long)vrtsim_state->rx_samples_total,
        (unsigned long long)vrtsim_state->tx_timing.tx_early,
        (unsigned long long)vrtsim_state->rx_early);
}

'''

CALL_REPLACEMENT = """    histogram_add(&vrtsim_state->chanmod_histogram, microseconds);
    vrtsim_log_runtime_debug(vrtsim_state, timestamp, microseconds);
    return num_samples_processed;
"""

INITIALIZE_REPLACEMENT = """  vrtsim_state->tx_num_channels = openair0_cfg->tx_num_channels;
  vrtsim_state->rx_num_channels = openair0_cfg->rx_num_channels;
  vrtsim_state->last_runtime_debug_second = -1;
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
    if MARKER in text:
        required = (
            "last_runtime_debug_second",
            "channel_processing_us=",
            "average_tx_budget_us=",
            "tx_samples_late=",
            "tx_samples_total=",
            "rx_samples_late=",
            "rx_samples_total=",
            "tx_early=",
            "rx_early=",
            "vrtsim_log_runtime_debug(vrtsim_state, timestamp, microseconds);",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial VRTSIM runtime telemetry patch: {missing}")
        print(f"Already patched: {path}")
        return False

    text = replace_once(
        text,
        STATE_ANCHOR,
        STATE_REPLACEMENT,
        "VRTSIM runtime telemetry state",
    )
    text = replace_once(
        text,
        HELPER_ANCHOR,
        HELPER + HELPER_ANCHOR,
        "VRTSIM runtime telemetry helper",
    )
    text = replace_once(
        text,
        CALL_ANCHOR,
        CALL_REPLACEMENT,
        "VRTSIM runtime telemetry call",
    )
    text = replace_once(
        text,
        INITIALIZE_ANCHOR,
        INITIALIZE_REPLACEMENT,
        "VRTSIM runtime telemetry initialization",
    )
    path.write_text(text)
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} VRTSIM_C", file=sys.stderr)
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
