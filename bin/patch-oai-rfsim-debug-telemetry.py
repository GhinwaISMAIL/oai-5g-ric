#!/usr/bin/env python3
"""Add fail-closed RFsim channel-state debug telemetry to pinned OAI."""

from __future__ import annotations

from pathlib import Path
import sys


MARKER = "RFSIM_CHANNEL_DEBUG_V1"
INCLUDE_ANCHOR = "#include <numeric>\n"
HELPER_ANCHOR = "static bool flushInput(rfsimulator_state_t *t, int timeout, bool first_time);\n"
CALL_ANCHOR = "      if (ptr->channel_model != NULL) { // apply a channel model\n"

HELPERS = r'''
typedef struct {
  const channel_desc_t *channel;
  int64_t snapshot_id;
  int64_t snapshot_timestamp_ns;
  int64_t last_logged_utc_second;
} rfsim_channel_debug_state_t;

static std::mutex rfsim_channel_debug_mutex;
static std::vector<rfsim_channel_debug_state_t> rfsim_channel_debug_states;
static int64_t rfsim_channel_debug_next_snapshot_id = 0;

static double rfsim_channel_tap_energy(const channel_desc_t *channel)
{
  if (channel == NULL || channel->ch == NULL)
    return NAN;

  double energy = 0.0;
  const int antenna_pairs = channel->nb_tx * channel->nb_rx;
  for (int antenna = 0; antenna < antenna_pairs; antenna++) {
    if (channel->ch[antenna] == NULL)
      return NAN;
    for (int tap = 0; tap < channel->channel_length; tap++) {
      const struct complexd coefficient = channel->ch[antenna][tap];
      energy += coefficient.r * coefficient.r + coefficient.i * coefficient.i;
    }
  }
  return energy;
}

static void log_rfsim_channel_debug(const channel_desc_t *channel)
{
  struct timespec now = {0};
  if (clock_gettime(CLOCK_REALTIME, &now) != 0)
    return;

  std::lock_guard<std::mutex> lock(rfsim_channel_debug_mutex);
  auto state = std::find_if(rfsim_channel_debug_states.begin(),
                            rfsim_channel_debug_states.end(),
                            [channel](const rfsim_channel_debug_state_t &candidate) {
                              return candidate.channel == channel;
                            });
  if (state == rfsim_channel_debug_states.end()) {
    rfsim_channel_debug_states.push_back({
        .channel = channel,
        .snapshot_id = rfsim_channel_debug_next_snapshot_id++,
        .snapshot_timestamp_ns = (int64_t)now.tv_sec * 1000000000LL + now.tv_nsec,
        .last_logged_utc_second = -1,
    });
    state = std::prev(rfsim_channel_debug_states.end());
  }

  if (state->last_logged_utc_second == now.tv_sec)
    return;

  const double tap_energy = rfsim_channel_tap_energy(channel);
  if (!std::isfinite(tap_energy))
    return;

  state->last_logged_utc_second = now.tv_sec;
  const int64_t emitted_epoch_us = (int64_t)now.tv_sec * 1000000LL + now.tv_nsec / 1000;
  LOG_I(HW,
        "RFSIM_CHANNEL_DEBUG_V1 utc_second=%lld emitted_epoch_us=%lld model=%s "
        "channel_snapshot_id=static-%lld channel_snapshot_timestamp_ns=%lld "
        "tap_energy_linear=%.17g applied_gain_db=%.9f noise_power_db=%.9f\n",
        (long long)now.tv_sec,
        (long long)emitted_epoch_us,
        channel->model_name != NULL ? channel->model_name : "unnamed",
        (long long)state->snapshot_id,
        (long long)state->snapshot_timestamp_ns,
        tap_energy,
        channel->path_loss_dB,
        channel->noise_power_dB);
}

'''


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
            "rfsim_channel_tap_energy",
            "rfsim_channel_debug_states",
            "channel_snapshot_timestamp_ns=",
            "tap_energy_linear=",
            "applied_gain_db=",
            "noise_power_db=",
            "log_rfsim_channel_debug(ptr->channel_model);",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial RFsim debug telemetry patch: missing {missing}")
        print(f"Already patched: {path}")
        return False

    text = replace_once(
        text,
        INCLUDE_ANCHOR,
        INCLUDE_ANCHOR + "#include <cmath>\n#include <ctime>\n",
        "debug telemetry includes",
    )
    text = replace_once(
        text,
        HELPER_ANCHOR,
        HELPER_ANCHOR + "\n" + HELPERS,
        "debug telemetry helpers",
    )
    text = replace_once(
        text,
        CALL_ANCHOR,
        CALL_ANCHOR + "        log_rfsim_channel_debug(ptr->channel_model);\n",
        "debug telemetry call",
    )
    path.write_text(text)
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} RFSIMULATOR_CPP", file=sys.stderr)
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
