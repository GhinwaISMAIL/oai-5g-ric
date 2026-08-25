#!/usr/bin/env python3
"""Add additive stage timing to the pinned, runtime-instrumented VRTSIM write path."""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "VRTSIM_SPLIT_DEBUG_V1"
RUNTIME_MARKER = "VRTSIM_RUNTIME_DEBUG_V1"
STATE_ANCHOR = """  histogram_t chanmod_histogram;
  int64_t last_runtime_debug_second;
  int chanmod;
"""
STATE_REPLACEMENT = """  histogram_t chanmod_histogram;
  int64_t last_runtime_debug_second;
  int64_t last_split_debug_second;
  int chanmod;
"""
FUNCTION_ANCHOR = """static int vrtsim_write_with_chanmod(vrtsim_state_t *vrtsim_state,
                                     openair0_timestamp_t timestamp,
                                     void **samplesVoid,
                                     int nsamps,
                                     int nbAnt)
{
"""
FUNCTION_REPLACEMENT = """static int vrtsim_write_with_chanmod(vrtsim_state_t *vrtsim_state,
                                     openair0_timestamp_t timestamp,
                                     void **samplesVoid,
                                     int nsamps,
                                     int nbAnt,
                                     vrtsim_chanmod_split_t *split)
{
  *split = (vrtsim_chanmod_split_t){0};
  double split_stage_started_us = vrtsim_monotonic_microseconds();
"""
CIRDB_END_ANCHOR = """  }

  int noise_power_dBFS = get_noise_power_dBFS();
"""
CIRDB_END_REPLACEMENT = """  }
  double split_stage_ended_us = vrtsim_monotonic_microseconds();
  split->cirdb_update_us += split_stage_ended_us - split_stage_started_us;
  split_stage_started_us = split_stage_ended_us;

  int noise_power_dBFS = get_noise_power_dBFS();
"""
PRE_CONVOLUTION_ANCHOR = """    size_t saved_samples_input_len = channel_length - 1;

#ifdef CHANNEL_SIM_CUDA
"""
PRE_CONVOLUTION_REPLACEMENT = """    size_t saved_samples_input_len = channel_length - 1;
    split_stage_ended_us = vrtsim_monotonic_microseconds();
    split->preparation_us += split_stage_ended_us - split_stage_started_us;
    split_stage_started_us = split_stage_ended_us;

#ifdef CHANNEL_SIM_CUDA
"""
POST_CONVOLUTION_ANCHOR = """#endif

    for (int aarx = 0; aarx < nb_rx; aarx++) {
"""
POST_CONVOLUTION_REPLACEMENT = """#endif
    split_stage_ended_us = vrtsim_monotonic_microseconds();
    split->convolution_us += split_stage_ended_us - split_stage_started_us;
    split_stage_started_us = split_stage_ended_us;

    for (int aarx = 0; aarx < nb_rx; aarx++) {
"""
POST_WRITE_ANCHOR = """    for (int aarx = 0; aarx < nb_rx; aarx++) {
      vrtsim_write_internal(vrtsim_state, timestamp, output[aarx], nsamps, rx_antenna_offset + aarx);
    }
    rx_antenna_offset += nb_rx;
"""
POST_WRITE_REPLACEMENT = """    for (int aarx = 0; aarx < nb_rx; aarx++) {
      vrtsim_write_internal(vrtsim_state, timestamp, output[aarx], nsamps, rx_antenna_offset + aarx);
    }
    split_stage_ended_us = vrtsim_monotonic_microseconds();
    split->shared_write_us += split_stage_ended_us - split_stage_started_us;
    split_stage_started_us = split_stage_ended_us;
    rx_antenna_offset += nb_rx;
"""
HISTORY_START_ANCHOR = """  }

  // Save samples for next round
"""
HISTORY_START_REPLACEMENT = """  }
  split_stage_ended_us = vrtsim_monotonic_microseconds();
  split->preparation_us += split_stage_ended_us - split_stage_started_us;
  split_stage_started_us = split_stage_ended_us;

  // Save samples for next round
"""
HISTORY_END_ANCHOR = """      memcpy(saved_samples[aatx], &samples[nsamps - SAVED_SAMPLES_LEN], sizeof(c16_t) * (SAVED_SAMPLES_LEN));
    }
  }

  return nsamps;
"""
HISTORY_END_REPLACEMENT = """      memcpy(saved_samples[aatx], &samples[nsamps - SAVED_SAMPLES_LEN], sizeof(c16_t) * (SAVED_SAMPLES_LEN));
    }
  }
  split_stage_ended_us = vrtsim_monotonic_microseconds();
  split->history_copy_us += split_stage_ended_us - split_stage_started_us;

  return nsamps;
"""
CALL_ANCHOR = """    int num_samples_processed = vrtsim_write_with_chanmod(vrtsim_state, timestamp, samplesVoid, nsamps, nbAnt);
"""
CALL_REPLACEMENT = """    vrtsim_chanmod_split_t split;
    int num_samples_processed = vrtsim_write_with_chanmod(vrtsim_state, timestamp, samplesVoid, nsamps, nbAnt, &split);
"""
LOG_CALL_ANCHOR = """    histogram_add(&vrtsim_state->chanmod_histogram, microseconds);
    vrtsim_log_runtime_debug(vrtsim_state, timestamp, microseconds);
    return num_samples_processed;
"""
LOG_CALL_REPLACEMENT = """    histogram_add(&vrtsim_state->chanmod_histogram, microseconds);
    vrtsim_log_runtime_debug(vrtsim_state, timestamp, microseconds);
    vrtsim_log_split_debug(vrtsim_state, timestamp, microseconds, &split);
    return num_samples_processed;
"""
INITIALIZE_ANCHOR = "  vrtsim_state->last_runtime_debug_second = -1;\n"
INITIALIZE_REPLACEMENT = """  vrtsim_state->last_runtime_debug_second = -1;
  vrtsim_state->last_split_debug_second = -1;
"""
LOG_HELPER_ANCHOR = "static int vrtsim_write(openair0_device_t *device,\n"

TIMING_TYPES_AND_HELPER = r'''/* Phase 3C11 additive VRTSIM stage timing. */
typedef struct {
  double cirdb_update_us;
  double preparation_us;
  double convolution_us;
  double shared_write_us;
  double history_copy_us;
} vrtsim_chanmod_split_t;

static double vrtsim_monotonic_microseconds(void)
{
  struct timespec now;
  int ret = clock_gettime(CLOCK_MONOTONIC, &now);
  AssertFatal(ret == 0, "clock_gettime failed\n");
  return now.tv_sec * 1e6 + now.tv_nsec / 1e3;
}

'''

LOG_HELPER = r'''static void vrtsim_log_split_debug(vrtsim_state_t *vrtsim_state,
                                   openair0_timestamp_t timestamp,
                                   double total_us,
                                   const vrtsim_chanmod_split_t *split)
{
  const int64_t elapsed_second = (int64_t)(timestamp / vrtsim_state->sample_rate);
  if (elapsed_second <= vrtsim_state->last_split_debug_second)
    return;
  vrtsim_state->last_split_debug_second = elapsed_second;
  const double accounted_us = split->cirdb_update_us + split->preparation_us + split->convolution_us
                              + split->shared_write_us + split->history_copy_us;
  const double residual_us = total_us - accounted_us;
  const char *role_name = vrtsim_state->role == ROLE_SERVER ? ROLE_SERVER_STRING : ROLE_CLIENT_STRING;
  LOG_I(HW,
        "VRTSIM_SPLIT_DEBUG_V1 role=%s elapsed_second=%lld total_us=%.3f cirdb_update_us=%.3f "
        "preparation_us=%.3f convolution_us=%.3f shared_write_us=%.3f history_copy_us=%.3f "
        "accounted_us=%.3f residual_us=%.3f\n",
        role_name,
        (long long)elapsed_second,
        total_us,
        split->cirdb_update_us,
        split->preparation_us,
        split->convolution_us,
        split->shared_write_us,
        split->history_copy_us,
        accounted_us,
        residual_us);
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
            "last_split_debug_second",
            "vrtsim_chanmod_split_t",
            "cirdb_update_us=%.3f",
            "preparation_us=%.3f",
            "convolution_us=%.3f",
            "shared_write_us=%.3f",
            "history_copy_us=%.3f",
            "accounted_us=%.3f",
            "residual_us=%.3f",
            "vrtsim_log_split_debug(vrtsim_state, timestamp, microseconds, &split);",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial VRTSIM split telemetry patch: {missing}")
        print(f"Already patched: {path}")
        return False
    if RUNTIME_MARKER not in text:
        raise RuntimeError("VRTSIM runtime telemetry must be applied before split telemetry")

    replacements = (
        (STATE_ANCHOR, STATE_REPLACEMENT, "split telemetry state"),
        (FUNCTION_ANCHOR, TIMING_TYPES_AND_HELPER + FUNCTION_REPLACEMENT, "write function"),
        (CIRDB_END_ANCHOR, CIRDB_END_REPLACEMENT, "CIRDB stage boundary"),
        (PRE_CONVOLUTION_ANCHOR, PRE_CONVOLUTION_REPLACEMENT, "pre-convolution boundary"),
        (POST_CONVOLUTION_ANCHOR, POST_CONVOLUTION_REPLACEMENT, "post-convolution boundary"),
        (POST_WRITE_ANCHOR, POST_WRITE_REPLACEMENT, "post-write boundary"),
        (HISTORY_START_ANCHOR, HISTORY_START_REPLACEMENT, "history start boundary"),
        (HISTORY_END_ANCHOR, HISTORY_END_REPLACEMENT, "history end boundary"),
        (CALL_ANCHOR, CALL_REPLACEMENT, "instrumented write call"),
        (LOG_HELPER_ANCHOR, LOG_HELPER + LOG_HELPER_ANCHOR, "split log helper"),
        (LOG_CALL_ANCHOR, LOG_CALL_REPLACEMENT, "split log call"),
        (INITIALIZE_ANCHOR, INITIALIZE_REPLACEMENT, "split state initialization"),
    )
    for old, new, context in replacements:
        text = replace_once(text, old, new, context)
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
